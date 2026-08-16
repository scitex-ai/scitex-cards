#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A backend cutover must not leave undelivered messages behind in silence.

REGRESSION FOR A MEASURED INCIDENT (2026-08-14). The notification rail moved
from SQLite to PostgreSQL on 08-11 and stranded 149 unseen notifications — 0 of
them migrated. Among them an answer the operator was waiting on and another
agent's retraction of a false outage report. It sat for THREE DAYS with every
call reporting success, because the writes and the reads were about different
databases.

WHAT THESE TESTS DO AND DO NOT COVER, stated plainly because the gap matters.
They exercise the COUNTING and the SHAPE against a file built to the incident's
shape — unseen rows beside already-seen ones — including the three-valued
answer, which is the part that made the original defect invisible.

They do NOT exercise the integrated `backend()==POSTGRES` path, because forcing
that would need either a live PostgreSQL inbox or monkeypatching, and mocks are
banned ecosystem-wide. So `check_no_stranded_backlog` is covered for its shape
and its non-Postgres branch only. The counting helper it delegates to is fully
covered, which is where a wrong answer would come from — but that is a weaker
guarantee than end-to-end, and calling it end-to-end would be the same
overclaim this module exists to prevent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scitex_cards._health_stranded_backlog import (
    _recipients_in,
    _unseen_in,
    check_no_stranded_backlog,
)


def _legacy_inbox(tmp_path: Path, unseen: int, seen: int = 0) -> Path:
    """A file shaped like the real stranded inbox."""
    p = tmp_path / "cards.db"
    conn = sqlite3.connect(p)
    conn.execute(
        "create table inbox (id text primary key, recipient text, seen int)"
    )
    rows = [(f"n_u{i}", "operator", 0) for i in range(unseen)]
    rows += [(f"n_s{i}", "operator", 1) for i in range(seen)]
    conn.executemany("insert into inbox values (?,?,?)", rows)
    conn.commit()
    conn.close()
    return p


def test_a_stranded_backlog_is_counted(tmp_path):
    # Arrange — the incident's shape: unseen rows beside already-seen ones.
    path = _legacy_inbox(tmp_path, unseen=149, seen=216)
    # Act
    count = _unseen_in(path)
    # Assert
    assert count == 149


def test_an_empty_legacy_inbox_counts_zero(tmp_path):
    # Arrange
    path = _legacy_inbox(tmp_path, unseen=0, seen=5)
    # Act
    count = _unseen_in(path)
    # Assert
    assert count == 0


def test_an_unreadable_file_is_unknown_not_zero(tmp_path):
    # Arrange — UNKNOWN must never collapse into "nothing stranded"; that
    # collapse is how the original defect stayed invisible.
    path = tmp_path / "not-a-database.db"
    path.write_text("this is not sqlite")
    # Act
    count = _unseen_in(path)
    # Assert
    assert count is None


def test_a_missing_file_is_unknown_rather_than_a_guess(tmp_path):
    # Arrange
    path = tmp_path / "absent.db"
    # Act
    count = _unseen_in(path)
    # Assert
    assert count is None


def test_the_breakdown_names_the_recipients(tmp_path):
    # Arrange — the detail line must name WHO is not being reached, so the
    # reader can tell "130 to the operator" from "130 card events".
    path = _legacy_inbox(tmp_path, unseen=3)
    # Act
    who = _recipients_in(path)
    # Assert
    assert "operator:3" in who


def test_a_non_postgres_backend_has_no_cutover_to_strand_behind():
    # Arrange — on SQLite the rail IS the legacy file, so nothing is stranded
    # by definition. The check must not invent a problem there.
    expected = True
    # Act
    result = check_no_stranded_backlog()
    # Assert — three-valued: True or None are both acceptable here; False
    # would be a false alarm, which is what this pins.
    assert result["ok"] in (expected, None)


def test_the_check_answers_in_the_declared_shape():
    # Arrange
    expected = {"ok", "detail", "hint"}
    # Act
    result = check_no_stranded_backlog()
    # Assert
    assert set(result) == expected


def test_the_hint_names_the_safe_recovery_path():
    # Arrange — an error that only states what broke is half-written; this one
    # must name the package's own door and the pre-image, because the recovery
    # I actually performed used both.
    from scitex_cards import _health_stranded_backlog as mod

    required = ("enqueue", "pre-image")
    # Act
    hint = mod._HINT or ""
    # Assert
    assert all(word in hint for word in required)


# EOF
