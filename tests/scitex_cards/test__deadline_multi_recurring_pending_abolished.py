#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the deadline-multi-recurring fixtures (pending abolition).

``tests/scitex_cards/test__deadline_multi_recurring.py`` seeded the abolished
``pending`` status in 3 fixtures after ``pending`` was abolished 2026-07-10.
The assertions pin deadline-shape errors (deadline-vs-deadlines mutual
exclusion, empty list, garbage entry), not statuses — ``_validate_tasks``
with ``strict=False`` only WARNS on ``pending`` (tolerated read-side) and
still raises the deadline error, so the suite passed while encoding a dead
status as live. Same dead-fixture class as the is_overdue (19) + org (18) +
mermaid (2) + idle-guard (4) + write-blocker (1) + group-field (5) repoints.
The fixtures now seed ``deferred`` (open, in VALID_STATUSES); no src change.

These pins use file text, in-memory dicts, and the status enum only:
no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from scitex_cards import TaskValidationError
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import _validate_tasks

_FIXTURE_TEXT = Path(__file__).with_name("test__deadline_multi_recurring.py").read_text(
    encoding="utf-8"
)


def test_fixture_text_seeds_no_abolished_status():
    assert '"status": "pending"' not in _FIXTURE_TEXT
    assert "'status': 'pending'" not in _FIXTURE_TEXT
    # Non-vacuity anchor: the successor default is actually seeded, so the
    # sweep above is not passing on an empty file.
    assert '"status": "deferred"' in _FIXTURE_TEXT


def test_pending_seed_warns_abolished_while_deferred_is_silent():
    # Mechanism guard: the two statuses are NOT interchangeable seeds —
    # ``pending`` warns abolished (tolerated), ``deferred`` validates clean.
    for status, expect_warning in (("pending", True), ("deferred", False)):
        tasks = [{"id": "a", "title": "A", "status": status, "deadline": "2026-06-15"}]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _validate_tasks(tasks, source="<t>", strict=False)
        abolished = [w for w in caught if "abolished" in str(w.message)]
        assert (len(abolished) > 0) is expect_warning


def test_deferred_fixture_still_rejects_both_deadline_shapes():
    tasks = [
        {
            "id": "a",
            "title": "A",
            "status": "deferred",
            "deadline": "2026-06-15",
            "deadlines": ["2026-06-15"],
        }
    ]
    with pytest.raises(TaskValidationError, match="BOTH deadline and deadlines"):
        _validate_tasks(tasks, source="<t>", strict=False)


def test_enum_mirror_pending_abolished_deferred_valid():
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
