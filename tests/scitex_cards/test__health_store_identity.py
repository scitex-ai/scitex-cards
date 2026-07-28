#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`health` reports when the store and the DB's stamp disagree.

The store identity lives in two places — the launcher env and the DB's
provenance stamp — with nothing keeping them in step. A mismatch makes the
ownership guard refuse EVERY write, correctly, but the symptom is a total write
outage with no monitor.

On 2026-07-19 the MCP server resolved ~/.scitex/cards/tasks.yaml (deleted during
the cutover) while the DB was stamped ~/.scitex/todo/tasks.yaml. Every write
through the surface other agents use was refused and nothing reported it:
`store_canonical` answers "does a parseable file exist there", not "can this
process write".
"""

from __future__ import annotations

import pytest

from scitex_cards._health import health

_SEED = "tasks:\n- id: t\n  title: T\n  status: done\n  assignee: x\n  created_by: x\n"


def _check(report: dict, name: str) -> dict:
    return {c["name"]: c for c in report["checks"]}[name]


def _seed_and_stamp(db_path, store_path) -> None:
    """Seed the canonical DB from ``_SEED`` and stamp its provenance for ``store_path``.

    Replaces the deleted ``import_from_yaml(tasks_path=store)``. That entry point
    built the DB from the YAML at ``store`` AND recorded ``store`` in the DB's
    provenance stamp (``KEY_YAML_PATH``). SQLite is now the only store and the
    importer is gone, so both halves are done explicitly: seed the DB from the
    same in-memory doc via ``seed_db_from_doc`` (the surviving rebuild
    primitive), then stamp ``KEY_YAML_PATH`` with ``store_path`` so the
    ``store_identity`` check has a stamped identity to agree or disagree with.
    """
    from conftest import seed_db_from_doc

    from scitex_cards._db import connect
    from scitex_cards._db_freshness import stamp_store_provenance
    from scitex_cards._yaml import safe_load

    doc = safe_load(_SEED) or {}
    seed_db_from_doc(doc, str(db_path))
    conn = connect(str(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        stamp_store_provenance(conn, store_path)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def two_stores(tmp_path, env):
    """Two real stores and a DB seeded + stamped for the FIRST of them."""
    a = tmp_path / "a" / "tasks.yaml"
    a.parent.mkdir()
    a.write_text(_SEED)
    b = tmp_path / "b" / "tasks.yaml"
    b.parent.mkdir()
    b.write_text(_SEED)
    db = tmp_path / "cards.db"
    env.set("SCITEX_CARDS_DB", str(db))
    env.set("SCITEX_TODO_DB", str(db))
    _seed_and_stamp(db, a)
    return a, b


def test_matching_store_and_stamp_is_ok(two_stores):
    """The normal case must not raise a false alarm."""
    # Arrange
    store_a, _ = two_stores

    # Act
    check = _check(health(store=str(store_a)), "store_identity")

    # Assert
    assert check["ok"] is True, check["detail"]


def test_a_mismatch_is_reported_as_a_write_outage(two_stores):
    """The 2026-07-19 shape: resolved store != stamped store."""
    # Arrange
    _, store_b = two_stores

    # Act
    check = _check(health(store=str(store_b)), "store_identity")

    # Assert
    assert check["ok"] is False
    assert "STORE IDENTITY MISMATCH" in check["detail"]


def test_the_hint_names_both_ways_to_resolve_it(two_stores):
    """A failing check must say what to DO, not merely that something is wrong.

    This assertion used to require the literal string ``db import`` — a CLI verb
    that does not exist. So the test did not merely tolerate the dead remedy, it
    PINNED it: removing the bad advice would have broken the suite. A test can
    hold a defect in place, and this one did, on the total-write-outage path.

    It now requires that the hint names the POINTER (the thing the reader can
    actually change) and stays silent about re-stamping, which asserts the
    database belongs to a different store — the very claim the ownership guard
    exists to doubt.
    """
    # Arrange
    _, store_b = two_stores

    # Act
    check = _check(health(store=str(store_b)), "store_identity")

    # Assert
    assert "SCITEX_CARDS_DB" in check["hint"]
    assert "db path" in check["hint"], "the hint must name a runnable verb"
    assert "db import" not in check["hint"], (
        "`db import` does not exist; naming it strands the reader on the path "
        "where every write is already being refused"
    )


def test_a_missing_db_is_not_an_alarm(tmp_path, env):
    """Nothing to disagree with yet — a fresh install must report clean."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    store.write_text(_SEED)
    env.set("SCITEX_CARDS_DB", str(tmp_path / "absent.db"))
    env.set("SCITEX_TODO_DB", str(tmp_path / "absent.db"))

    # Act
    check = _check(health(store=str(store)), "store_identity")

    # Assert
    assert check["ok"] is True, check["detail"]


# EOF
