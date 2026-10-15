#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the throughput fixtures (pending abolition).

``tests/scitex_cards/test__throughput.py`` seeded the abolished ``pending``
status in 12 pure fixtures (aggregate x4, classify x4, notify-body x4)
after ``pending`` was abolished 2026-07-10. The assertions pin
grouping/open-count/stale/classify/notify composition, not statuses —
``aggregate``/``classify``/``build_notify_body`` take plain dicts with no
validation, so the suite passed while encoding a dead status as live.
Same dead-fixture class as the deadline (11) + deadline_multi_recurring
(3) + is_overdue (19) + org (18) + mermaid (2) + idle-guard (4) +
write-blocker (1) + group-field (5) repoints. The fixtures now seed
``deferred`` (the successor default: open, in VALID_STATUSES); no src
change (``_throughput`` was already correct: OPEN excludes only
terminal+goal, stale flags only ``in_progress``, classify gates only on
``blocked``/``depends_on``).

These pins use file text, in-memory dicts, and the status enum only:
no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

from pathlib import Path

from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._throughput import aggregate, build_notify_body, classify

_FIXTURE_TEXT = Path(__file__).with_name("test__throughput.py").read_text(
    encoding="utf-8"
)


def test_fixture_text_seeds_no_abolished_status():
    # Arrange
    text = _FIXTURE_TEXT
    # Act
    found = '"status": "pending"' in text
    # Assert
    assert found is False


def test_fixture_text_seeds_the_successor_default():
    # Arrange
    text = _FIXTURE_TEXT
    # Act
    found = '"status": "deferred"' in text
    # Assert
    assert found is True


def test_deferred_counts_as_open_in_aggregate():
    # Arrange
    tasks = [{"agent": "a", "status": "deferred"}]
    # Act
    rows = aggregate(tasks, by="agent")
    # Assert
    assert rows[0].open_count == 1


def test_deferred_is_runnable_in_classify():
    # Arrange
    task = {"id": "x", "status": "deferred"}
    # Act
    gate = classify(task, {"x": task})
    # Assert
    assert gate.label == "RUNNABLE"


def test_deferred_excluded_from_stale_count():
    # Arrange
    tasks = [
        {"agent": "a", "status": "deferred", "last_activity": "2026-01-01T00:00:00Z"}
    ]
    # Act
    rows = aggregate(tasks, by="agent", stale_hours=24)
    # Assert
    assert rows[0].stale_count == 0


def test_deferred_notify_body_counts_open():
    # Arrange
    tasks = [{"id": "t1", "title": "T1", "status": "deferred", "agent": "a"}]
    # Act
    body = build_notify_body("a", tasks)
    # Assert
    assert body.startswith("a ·")


def test_enum_mirror_pending_abolished_deferred_valid():
    # Arrange
    abolished = ABOLISHED_STATUSES
    # Act
    pending_abolished = "pending" in abolished
    # Assert
    assert pending_abolished is True


def test_enum_mirror_pending_outside_valid():
    # Arrange
    valid = VALID_STATUSES
    # Act
    pending_valid = "pending" in valid
    # Assert
    assert pending_valid is False


def test_enum_mirror_deferred_inside_valid():
    # Arrange
    valid = VALID_STATUSES
    # Act
    deferred_valid = "deferred" in valid
    # Assert
    assert deferred_valid is True
