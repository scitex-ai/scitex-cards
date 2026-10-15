#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the deadline fixtures (pending abolition).

``tests/scitex_cards/test__deadline.py`` seeded the abolished ``pending``
status in 11 fixtures (6 ACCEPT YAML seeds + 5 REJECT validator dicts)
after ``pending`` was abolished 2026-07-10. The assertions pin
deadline/scheduled validation, not statuses — ``_validate_tasks`` with
``strict=False`` only WARNS on ``pending`` (tolerated read-side) and still
raises the deadline error, so the suite passed while encoding a dead
status as live. Same dead-fixture class as the deadline_multi_recurring
(3) + is_overdue (19) + org (18) + mermaid (2) + idle-guard (4) +
write-blocker (1) + group-field (5) repoints. The fixtures now seed
``deferred`` (open, in VALID_STATUSES); no src change.

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

_FIXTURE_TEXT = Path(__file__).with_name("test__deadline.py").read_text(
    encoding="utf-8"
)


def test_fixture_text_seeds_no_abolished_status():
    # Both fixture spellings: the ACCEPT YAML seeds (`status: pending`)
    # and the REJECT validator dicts (`"status": "pending"`).
    assert "status: pending" not in _FIXTURE_TEXT
    assert '"status": "pending"' not in _FIXTURE_TEXT
    # Non-vacuity anchors: the successor default is actually seeded in
    # both spellings, so the sweep above is not passing on an empty file.
    assert "status: deferred" in _FIXTURE_TEXT
    assert '"status": "deferred"' in _FIXTURE_TEXT


def test_pending_seed_warns_abolished_while_deferred_is_silent():
    # Mechanism guard: the two statuses are NOT interchangeable seeds —
    # ``pending`` warns abolished (tolerated), ``deferred`` validates clean.
    for status, expect_warning in (("pending", True), ("deferred", False)):
        tasks = [
            {
                "id": "a",
                "title": "A",
                "status": status,
                "deadline": "2026-06-15",
            }
        ]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _validate_tasks(tasks, source="<t>", strict=False)
        abolished = [w for w in caught if "abolished" in str(w.message)]
        assert (len(abolished) > 0) is expect_warning


def test_deferred_fixtures_still_reject_bad_deadlines():
    # Behavior pin: the repointed REJECT fixtures still raise the same
    # deadline errors — the status swap changed nothing about validation.
    bad = [
        ({"deadline": ""}, "invalid deadline"),
        ({"deadline": 20260615}, "invalid deadline"),
        ({"deadline": "next Tuesday"}, "unparseable deadline"),
        ({"scheduled": "soon"}, "unparseable scheduled"),
        (
            {"deadline": "2026-06-10", "scheduled": "2026-06-15"},
            "before scheduled",
        ),
    ]
    for extra, match in bad:
        tasks = [{"id": "a", "title": "A", "status": "deferred", **extra}]
        with pytest.raises(TaskValidationError, match=match):
            _validate_tasks(tasks, source="<t>", strict=False)


def test_deferred_clean_fixture_loads_without_warning():
    # A well-formed deferred row with deadline+scheduled validates silent —
    # the ACCEPT-shape fixtures are meaningful, not just non-rejecting.
    tasks = [
        {
            "id": "a",
            "title": "A",
            "status": "deferred",
            "deadline": "2026-06-20",
            "scheduled": "2026-06-15",
        }
    ]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(tasks, source="<t>", strict=False)
    assert [w for w in caught if "abolished" in str(w.message)] == []


def test_enum_mirror_pending_abolished_deferred_valid():
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
