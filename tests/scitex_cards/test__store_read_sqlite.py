#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The S2 SQLite read accelerator is DELETED — this file pins the new contract.

INCIDENT (2026-07-21, P0): agent scitex-dev, running scitex-cards 0.17.4 with
the deprecated ``SCITEX_TODO_READ_BACKEND=sqlite`` env var set (and NOT
``SCITEX_CARDS_STORE_BACKEND``), hit ``_store_read_sqlite.py``'s refusal ("THE
SQLITE READ BACKEND IS REFUSING TO SERVE ... falling back to the canonical
YAML"). SQLite had already become the ONE canonical store by then (no mirror,
no YAML behind it), so the accelerator's freshness check compared the
database's provenance stamp against a YAML file that no longer existed and
refused UNCONDITIONALLY, every time. Its "safe" fallback then resolved through
the (also already-deleted) YAML / bundled-example chain to an EMPTY board,
serving it silently while a banner claimed reads were merely slow. An
accelerator whose guard can never again pass is not a slow path — it is dead
code that fails dangerous, so it is deleted rather than repaired.

This file used to prove the SQLite-indexed read path
(``scitex_cards._store_read_sqlite.list_tasks_sqlite``) and the
Python-predicate path (``scitex_cards._store_list._match``) returned
IDENTICAL rows for every filter combination. There is no longer a second
backend to prove identical to anything — ``list_tasks`` has exactly ONE read
path now, straight through :func:`scitex_cards._model.load_tasks` /
:func:`scitex_cards._store._read_canonical_db_or_raise`, which already carries
its own thorough raise-on-failure coverage
(``tests/scitex_cards/test__dual_write.py``). What is pinned here is narrower
and specific to THIS deletion: the module is gone, the two env vars that
caused the incident do nothing and can never be typo'd back into ``src``
without failing CI, and ``list_tasks`` itself — the entry point an agent
actually calls — raises rather than silently returning an empty board.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "scitex_cards"

#: The two deprecated env var names named in the incident report. Their exact
#: spelling matters — a maintainer "helpfully" reintroducing the accelerator
#: (or even just its read-backend selector) would almost certainly reuse one
#: of these literal strings, so the sentinel checks for THEM by name, not a
#: generic "*_BACKEND" pattern that would also flag unrelated code.
_BANNED_ENV_NAMES = ("SCITEX_TODO_READ_BACKEND", "SCITEX_CARDS_STORE_BACKEND")


def test_the_s2_accelerator_module_is_gone():
    """``_store_read_sqlite`` must not exist — deleted whole, not disabled."""
    # Arrange / Act / Assert
    with pytest.raises(ModuleNotFoundError):
        import scitex_cards._store_read_sqlite  # noqa: F401


def test_the_deprecated_env_var_names_appear_nowhere_in_src():
    """Reintroduction sentinel — same pattern as ``test__dual_write.py`` (#545).

    A literal source scan, not an import check: the incident env var could be
    reintroduced in a freshly written module that never imports the deleted
    one (a CLI flag default, a new docstring example, ...), and none of those
    would trip a module-existence check.
    """
    # Arrange
    offenders = []
    # Act
    for py in sorted(SRC.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        for banned in _BANNED_ENV_NAMES:
            if banned in text:
                offenders.append(f"{py.relative_to(SRC)}: contains {banned!r}")
    # Assert
    assert not offenders, (
        "the S2 accelerator's env var names must never reappear in src "
        "(2026-07-21 incident — see this module's docstring):\n" + "\n".join(offenders)
    )


def test_list_tasks_raises_when_the_store_is_unresolvable(env, tmp_path):
    """The PUBLIC entry point an agent actually calls, not just the primitive.

    ``_read_canonical_db_or_raise`` already has thorough dedicated coverage
    (``test__dual_write.py``); this pins that ``list_tasks`` itself reaches
    it end to end, rather than silently swallowing the raise or resolving to
    an empty document somewhere between the two.
    """
    # Arrange — nothing at the resolved path; a missing DB is a configuration
    # error, never an empty board (see `_read_canonical_db_or_raise`).
    from scitex_cards import _store

    missing = tmp_path / "never-created" / "cards.db"
    env.set("SCITEX_CARDS_DB", str(missing))

    # Act / Assert
    with pytest.raises(RuntimeError, match="does not exist"):
        _store.list_tasks(scope="")


def test_the_deprecated_read_backend_env_var_is_ignored(env):
    """Setting the old read-backend flag changes NOTHING — no code reads it."""
    # Arrange
    from conftest import seed_db_from_doc

    from scitex_cards import _store

    env.set("SCITEX_TODO_READ_BACKEND", "sqlite")
    seed_db_from_doc(
        {"tasks": [{"id": "a", "title": "A", "status": "deferred"}]},
        os.environ["SCITEX_CARDS_DB"],
    )

    # Act
    rows = _store.list_tasks(scope="")

    # Assert — the ordinary DB-canonical read, unaffected by the stale flag.
    assert [r["id"] for r in rows] == ["a"]


def test_the_deprecated_store_backend_env_var_is_ignored(env):
    """Same proof for the write-side selector's name — no toggle reads it either."""
    # Arrange
    from conftest import seed_db_from_doc

    from scitex_cards import _store

    env.set("SCITEX_CARDS_STORE_BACKEND", "sqlite")
    seed_db_from_doc(
        {"tasks": [{"id": "b", "title": "B", "status": "deferred"}]},
        os.environ["SCITEX_CARDS_DB"],
    )

    # Act
    rows = _store.list_tasks(scope="")

    # Assert
    assert [r["id"] for r in rows] == ["b"]


# EOF
