#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the is_overdue fixtures (pending abolition).

``tests/scitex_cards/test__is_overdue.py`` seeded the abolished
``pending`` status in all 19 fixtures after ``pending`` was abolished
2026-07-10. The assertions pin deadline arithmetic, not statuses —
``is_overdue`` only separates terminal (done / failed / cancelled /
goal) from open — so both ``pending`` and ``deferred`` took the same
open path and the suite passed while encoding a dead status as live.
Same dead-fixture class as the org (18) + mermaid (2) + idle-guard (4)
+ write-blocker (1) + group-field (5) repoints. The fixtures now seed
``deferred`` (the successor default, open and overdue-eligible); no
src change.

These pins use file text, in-memory dicts, and the status enum only:
no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import scitex_cards._deadlines as deadlines_mod
from scitex_cards._model import is_overdue
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

_FIXTURE_TEXT = Path(__file__).with_name("test__is_overdue.py").read_text(
    encoding="utf-8"
)


def _utc(*args):
    return _dt.datetime(*args, tzinfo=_dt.timezone.utc)


def test_fixture_text_seeds_no_abolished_status_case1():
    # Arrange
    # Act
    # Assert
    assert '"status": "pending"' not in _FIXTURE_TEXT


def test_fixture_text_seeds_no_abolished_status_case2():
    # Arrange
    # Act
    # Assert
    assert "'status': 'pending'" not in _FIXTURE_TEXT


def test_fixture_text_seeds_no_abolished_status_case3():
    # Arrange
    # Act
    # Assert
    assert '"status": "deferred"' in _FIXTURE_TEXT


def test_deferred_past_deadline_is_overdue():
    # Arrange
    # Act
    task = {
        "id": "a",
        "title": "A",
        "status": "deferred",
        "deadline": "2026-06-11",
    }
    # Assert
    assert is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0)) is True


def test_terminal_statuses_stay_not_overdue():
    # Arrange
    past = "2026-06-01"
    now = _utc(2026, 6, 12, 12, 0, 0)
    # Act
    still_overdue = [
        status
        for status in ("done", "failed", "cancelled", "goal")
        if is_overdue(
            {"id": "a", "title": "A", "status": status, "deadline": past}, now=now
        )
        is not False
    ]
    # Assert
    assert still_overdue == [], still_overdue


def test_deferred_past_deadline_stays_overdue():
    # Arrange
    past = "2026-06-01"
    now = _utc(2026, 6, 12, 12, 0, 0)
    open_task = {"id": "a", "title": "A", "status": "deferred", "deadline": past}
    # Act
    # Assert
    assert is_overdue(open_task, now=now) is True


def test_status_enum_mirror_case1():
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_status_enum_mirror_case2():
    # Arrange
    # Act
    # Assert
    assert "pending" not in VALID_STATUSES


def test_status_enum_mirror_case3():
    # Arrange
    # Act
    # Assert
    assert "deferred" in VALID_STATUSES


def test_status_enum_mirror_case4():
    # Arrange
    # Act
    hint = ABOLISHED_STATUSES["pending"]
    # Assert
    assert "deferred" in hint


def test_overdue_doc_names_deferred_as_not_terminal():
    # Arrange
    # Act
    doc = deadlines_mod.is_overdue.__doc__ or ""
    # Assert
    assert "`deferred` is NOT" in doc
