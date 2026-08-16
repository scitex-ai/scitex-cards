#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TEST-FIRST intent for moving DMs out of the sidecar and into ``cards.db``.

DESIGN: ``docs/design/dm-into-cards-db.md`` (part 1 — current paths, schema,
append-only rules). Part 2 and its tests cover migration + multi-host; see
``tests/scitex_cards/test__dm_into_db_migration_design.py``.

WHY THIS FILE EXISTS BEFORE THE CODE. DMs live in a ``threads.json`` SIDECAR,
so they are the only fleet data the canonical store's protections (WAL,
store-identity stamping, tombstones, no-shrink, export/snapshot) do not cover
— and ``messages.recipient`` being a scalar column is the schema-level reason
group DM cannot exist. Moving them is wipe-class work, so the intended
behaviour is written down and made executable BEFORE a migration is written.

Two kinds of test live here:

* PASSING PINS of today's reality — including the landmine that
  ``threads_path()``, a *path query*, can CREATE the sidecar by reading YAML.
  A migration that does not neutralise that first will re-create the file
  behind its own back.
* ``xfail`` INTENT for the v5 schema. Non-strict on purpose: landing the real
  implementation incrementally turns these green without ever turning CI red
  on a test that started working earlier than expected.

Every database here is an EXPLICIT ``tmp_path / "cards.db"``. Nothing in this
file resolves the ambient store, migrates data, or touches the live fleet.
"""

from __future__ import annotations

import ast
import re
import sqlite3
from collections.abc import Iterator
from functools import partial
from pathlib import Path

import pytest

from scitex_cards._db import open_db, table_columns
from scitex_cards._threads import append_message, threads_path

#: The design this file encodes. Named in every xfail reason so a maintainer
#: reading a skipped test lands on the rationale rather than guessing.
DESIGN_DOC = "docs/design/dm-into-cards-db.md"


def _card(section: str):
    """An xfail marker whose reason points at the design doc section.

    Non-strict: an XPASS means the feature landed, which is good news and must
    not fail the run.
    """
    return pytest.mark.xfail(
        reason=f"DM-in-DB not implemented yet - see {DESIGN_DOC} {section}",
        strict=False,
    )


# --------------------------------------------------------------------------- #
# Fixtures — explicit throwaway stores only                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """An EXPLICIT database path nobody else can resolve.

    The suite's isolation guard exists because a test that lets the store
    resolve itself rebuilt the fleet's production board three times. Naming the
    file is the cheapest way to stay outside that failure class.
    """
    return tmp_path / "cards.db"


@pytest.fixture()
def conn(db_path: Path):
    """A schema-complete connection to the throwaway database."""
    connection = open_db(db_path)
    try:
        yield connection
    finally:
        connection.close()


def _seed_pair_message(connection: sqlite3.Connection) -> str:
    """Insert one thread + one message directly, for the invariant tests.

    Deliberately raw SQL: these tests are about what the ENGINE permits, so
    routing through a Python helper would test the helper instead.
    """
    connection.execute(
        "INSERT INTO dm_threads(id, kind, created_at, origin_host, record_json)"
        " VALUES('dm:agent-x::operator', 'pair', '2026-07-28T00:00:00Z',"
        " 'host-a', '{}')"
    )
    connection.execute(
        "INSERT INTO dm_messages(id, thread_id, sender, body, ts, seq,"
        " origin_host, record_json) VALUES('m_seed',"
        " 'dm:agent-x::operator', 'operator', 'hello',"
        " '2026-07-28T00:00:01Z', 1, 'host-a', '{}')"
    )
    return "m_seed"


# --------------------------------------------------------------------------- #
# PASSING PINS — where DMs actually live today                                 #
# --------------------------------------------------------------------------- #
def test_dm_sidecar_is_a_file_beside_the_database(tmp_path: Path):
    """Pin the starting state the migration moves away from.

    If someone later changes where DMs live without updating this suite, the
    design doc's "current state" section silently becomes fiction. This test
    is what keeps that section honest.
    """
    # Arrange — a store path with no threads sidecar yet.
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")

    # Act
    resolved = threads_path(store)

    # Assert
    assert resolved == tmp_path / "threads.json"


def test_threads_path_does_not_materialise_the_sidecar(tmp_path: Path):
    """The landmine is DEFUSED: a path query is a path query.

    ``threads_path()`` used to call ``_migrate_legacy_yaml_once``, so merely
    asking where the sidecar *would* be created it whenever a legacy
    ``threads.yaml`` was present. A migration that retires the sidecar cannot
    survive a function that re-creates it behind the migration's back, and the
    YAML read contradicted the operator's "we do not use YAML" ruling besides.
    This test was the pin on that behaviour; it is now the pin on its removal.
    """
    # Arrange — legacy YAML present, JSON absent. The exact trigger condition.
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")
    (tmp_path / "threads.yaml").write_text("threads: {}\n", encoding="utf-8")

    # Act — ask ONLY for the path. Nothing here requests a write.
    threads_path(store)

    # Assert
    assert not (tmp_path / "threads.json").exists()


def test_sent_dm_reaches_the_canonical_database(tmp_path: Path, db_path):
    """The whole card in one line: the store now HAS the DM.

    This assertion used to read ``== 0`` and was the defect made observable —
    a message was sent, the canonical store beside it stayed empty, and every
    protection that store offers covered nothing about that message. Inverting
    it is the deliverable.
    """
    # Arrange — a store whose database sits next to the sidecar.
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")

    # Act
    append_message("operator", "agent-x", "hello there", store=store)

    # Assert
    connection = open_db(db_path)
    try:
        rows = connection.execute("SELECT COUNT(*) FROM dm_messages").fetchone()[0]
    finally:
        connection.close()
    assert rows == 1


def test_the_superseded_messages_table_is_left_alone(tmp_path: Path, db_path):
    """The v3 ``messages`` table is not written, not dropped, not rebuilt.

    It is a derived mirror of the sidecar with no live writer — a fossil of
    the deleted YAML tier. An append-only store does not remove a table
    holding real rows (that is a count decrease, the exact bug class this
    design exists to avoid), so it is FROZEN: kept as a pre-migration snapshot
    and superseded by ``dm_messages`` rather than repaired.
    """
    # Arrange
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")

    # Act
    append_message("operator", "agent-x", "hello there", store=store)

    # Assert
    connection = open_db(db_path)
    try:
        rows = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        connection.close()
    assert rows == 0


# --------------------------------------------------------------------------- #
# PASSING GUARDS — the append-only scan, with its positive control             #
# --------------------------------------------------------------------------- #
#: ``DELETE FROM <table>`` in a string the interpreter could hand to a driver.
_DELETE_RE = re.compile(r"DELETE\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


def _docstrings(tree: ast.AST) -> set[ast.Constant]:
    """The string literals that are PROSE ABOUT code rather than values it uses."""
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    found: set[ast.Constant] = set()
    for node in ast.walk(tree):
        if not isinstance(node, holders) or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                found.add(first.value)
    return found


def _executable_strings(path: Path) -> Iterator[str]:
    """Every string literal in ``path`` that could reach a database driver.

    Scanned via the AST rather than the raw file TEXT, because a text scan
    cannot tell a delete from a sentence about one. Comments never enter the
    AST at all, and a docstring is prose ABOUT the code rather than a value
    the code passes anywhere, so both are excluded here.

    A ``SyntaxError`` is raised, never swallowed: a module that does not parse
    is one this scan did not read, and silently skipping it would let the guard
    narrow its own scope while still reporting a clean verdict.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    prose = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node not in prose:
                yield node.value


def _delete_targets(root: Path) -> list[str]:
    """Every table named by a ``DELETE FROM`` under ``root``."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for literal in _executable_strings(path):
            found.extend(_DELETE_RE.findall(literal))
    return found


def _package_root() -> Path:
    """The installed package source tree this scan inspects."""
    import scitex_cards

    return Path(scitex_cards.__file__).parent


def test_delete_from_scan_finds_the_known_card_deletes():
    """POSITIVE CONTROL for the guard below — "found nothing" must mean it.

    A scan that is broken and a scan that is clean produce identical output.
    The mirror genuinely deletes from ``tasks`` when a caller NAMES a card, so
    seeing that proves the instrument works before the next test trusts its
    silence.
    """
    # Arrange
    source_root = _package_root()

    # Act
    targets = _delete_targets(source_root)

    # Assert
    assert "tasks" in targets


def test_no_source_module_deletes_from_a_dm_table():
    """The append-only rule, enforced by inspection rather than by discipline.

    Vacuous today (no ``dm_*`` table exists) and deliberately landed anyway:
    it must already be in the suite on the day the tables arrive, because the
    operator's ruling is that a written record never disappears and a count
    decrease is itself a bug.
    """
    # Arrange
    source_root = _package_root()

    # Act
    dm_targets = [n for n in _delete_targets(source_root) if n.startswith("dm_")]

    # Assert
    assert dm_targets == []


def test_prose_about_a_delete_is_not_counted_as_one(tmp_path):
    """A sentence describing the forbidden write is not the forbidden write.

    The scan read raw file TEXT until 2026-07-31, so a docstring explaining
    WHY ``dm_messages`` must never be deleted from tripped the guard forbidding
    it — measured on ``_enforcement_probe.py``, whose incident write-up quotes
    the statement it is warning about. Documenting a rule must not violate it,
    or the guard teaches maintainers to stop writing the rationale down.
    """
    # Arrange
    module = tmp_path / "explains.py"
    module.write_text(
        '"""Never DELETE FROM dm_messages — the operator ruled records persist."""\n'
        "# Not even DELETE FROM dm_receipts when a row looks wrong.\n"
        "VALUE = 1\n",
        encoding="utf-8",
    )

    # Act
    targets = _delete_targets(tmp_path)

    # Assert
    assert targets == []


def test_a_real_delete_is_still_counted(tmp_path):
    """POSITIVE CONTROL for the exclusion above — it must not blind the scan.

    Narrowing a scan to fix a false positive is how a guard stops guarding, so
    the executable statement is pinned in the same breath as the prose that is
    now ignored.
    """
    # Arrange
    module = tmp_path / "deletes.py"
    module.write_text(
        "def purge(conn):\n"
        '    conn.execute("DELETE FROM dm_messages WHERE id = ?", ("x",))\n',
        encoding="utf-8",
    )

    # Act
    targets = _delete_targets(tmp_path)

    # Assert
    assert targets == ["dm_messages"]


# --------------------------------------------------------------------------- #
# INTENT — schema v5 (design section 3)                                        #
# --------------------------------------------------------------------------- #
def _tables(connection: sqlite3.Connection) -> set[str]:
    """Tables actually present in THIS file — the artifact, not the stamp."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {row[0] for row in rows}


def test_schema_declares_the_dm_threads_table(conn):
    """Threads become rows so the store's own rails finally cover them."""
    # Arrange
    wanted = "dm_threads"

    # Act
    present = _tables(conn)

    # Assert
    assert wanted in present


def test_schema_declares_the_dm_thread_member_events_table(conn):
    """Membership is an append-only event log, not a mutable member list.

    A ``leave`` must be a new row rather than a removed one, or "who was in
    this thread" becomes unanswerable after the fact — and across hosts an
    event log merges by union with no arbitration.
    """
    # Arrange
    wanted = "dm_thread_member_events"

    # Act
    present = _tables(conn)

    # Assert
    assert wanted in present


def test_schema_declares_the_dm_messages_table(conn):
    """The messages themselves, in the canonical store rather than a file."""
    # Arrange
    wanted = "dm_messages"

    # Act
    present = _tables(conn)

    # Assert
    assert wanted in present


def test_schema_declares_the_dm_receipts_table(conn):
    """Read state needs its own rows once a thread can have three members."""
    # Arrange
    wanted = "dm_receipts"

    # Act
    present = _tables(conn)

    # Assert
    assert wanted in present


def test_dm_messages_has_no_recipient_column(conn):
    """The single column that makes group DM impossible must not come along.

    ``messages.recipient`` is a scalar, so a message has exactly one addressee
    by construction. Recipients belong to thread MEMBERSHIP, not to the
    message row. The non-emptiness check is part of the same claim: on a
    missing table the column set is empty and the "not in" half would pass
    while proving nothing.
    """
    # Arrange
    forbidden = "recipient"

    # Act
    columns = table_columns(conn, "dm_messages")

    # Assert
    assert columns and forbidden not in columns


def test_dm_messages_records_the_host_that_wrote_it(conn):
    """Multi-host ordering and merge both need to know where a row came from.

    Without an origin stamp the tie-break in ``ORDER BY seq, ts, origin_host,
    id`` is not total, so two hosts holding identical rows can disagree about
    their order.
    """
    # Arrange
    wanted = "origin_host"

    # Act
    columns = table_columns(conn, "dm_messages")

    # Assert
    assert wanted in columns


# --------------------------------------------------------------------------- #
# INTENT — append-only made unreachable (design section 4)                     #
# --------------------------------------------------------------------------- #
def test_dm_messages_refuses_physical_delete(conn):
    """A guard can be bypassed; an engine trigger cannot be reached around.

    Enforcing append-only in Python leaves every other client — the sqlite3
    CLI, a stray script, a future caller — free to delete. The refusal belongs
    in the database so it binds all of them.
    """
    # Arrange
    _seed_pair_message(conn)
    delete = partial(conn.execute, "DELETE FROM dm_messages")

    # Act
    refusal = pytest.raises(sqlite3.DatabaseError, match="append-only")

    # Assert
    with refusal:
        delete()


def test_dm_messages_body_is_immutable(conn):
    """An edited message is a rewritten record, which append-only forbids.

    Immutability is also what lets a cross-host merge be a pure union: if a
    row can change after it is written, merging needs arbitration.
    """
    # Arrange
    _seed_pair_message(conn)
    edit = partial(conn.execute, "UPDATE dm_messages SET body = 'tampered'")

    # Act
    refusal = pytest.raises(sqlite3.DatabaseError, match="immutable")

    # Assert
    with refusal:
        edit()


def test_dm_messages_tombstone_marks_the_row_in_place(conn):
    """Deleting a DM must cost zero rows, exactly as deleting a card does.

    The card store already learned this the expensive way on 2026-07-21:
    ``delete_task`` marks ``_log_meta.deleted_at`` and keeps the row forever.
    DMs get the same shape so a count can never decrease.
    """
    # Arrange
    _seed_pair_message(conn)

    # Act
    conn.execute("UPDATE dm_messages SET deleted_at = '2026-07-28T00:00:02Z'")

    # Assert
    assert conn.execute("SELECT COUNT(*) FROM dm_messages").fetchone()[0] == 1


def test_pair_thread_id_is_the_legacy_thread_key():
    """Existing thread ids must survive verbatim — rewriting one is a delete.

    Every stored record, MCP response and board URL carries ``dm:<a>::<b>``.
    Minting a new id shape for pairs would mean deleting and re-inserting the
    whole history, which is precisely what the append-only ruling forbids.
    """
    # Arrange
    from scitex_cards._dm.store import pair_thread_id
    from scitex_cards._threads import thread_key

    # Act
    minted = pair_thread_id("operator", "agent-x")

    # Assert
    assert minted == thread_key("operator", "agent-x")


# EOF
