#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the fleet-strip ``current_task`` backlog leg.

``_build_fleet`` fell back to a hardcoded ``"pending"`` pool when no task
carried ``last_activity``. ``pending`` was abolished 2026-07-10, so the pool
matched nothing and degenerated to ALL items — the tooltip could name a
closed (done/failed/cancelled) card as current. The leg now reads
``_backlog_triage.BACKLOG_STATUS`` (``deferred``), the same single source
the CLI triage verb and the /stale panel read.

These pins call :func:`_build_fleet` directly with in-memory dicts: no
Django test client, no store, no PostgreSQL — they run in containers
where the store-backed suite refuses via the fail-LOUD guard.

Companion contract: the store-backed ``test_graph_fleet.py`` still seeds
legacy ``pending`` rows (they arrive as tolerated reads), and two of its
assertions depend on how this module treats them — a ``pending`` row
counts toward ``runnable_count`` (it is not in ``_LIVENESS_NONRUNNABLE``)
and toward ``overdue_count`` (it is not terminal for :func:`is_overdue`).
Those assumptions are pinned here PG-free so the store-backed file's
fixture choice stays load-bearing rather than incidental.
"""

from __future__ import annotations

from scitex_cards._backlog_triage import BACKLOG_STATUS
from scitex_cards._django.handlers.graph_fleet import _build_fleet
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES


def _fleet_by_agent(tasks):
    return {row["name"]: row for row in _build_fleet(tasks)}


def test_backlog_deferred_preferred_over_closed_when_no_activity():
    # Arrange
    tasks = [
        {"id": "a-done", "title": "Done", "agent": "alice", "status": "done", "priority": 0},
        {"id": "a-def", "title": "Backlog", "agent": "alice", "status": "deferred", "priority": 5},
    ]
    # Act
    fleet = _fleet_by_agent(tasks)
    # Assert
    assert fleet["alice"]["current_task_id"] == "a-def"


def test_legacy_pending_row_gets_no_priority():
    # Arrange
    tasks = [
        {"id": "a-pend", "title": "Legacy", "agent": "alice", "status": "pending", "priority": 0},
        {"id": "a-def", "title": "Backlog", "agent": "alice", "status": "deferred", "priority": 5},
    ]
    # Act
    fleet = _fleet_by_agent(tasks)
    # Assert
    assert fleet["alice"]["current_task_id"] == "a-def"


def test_backlog_leg_falls_back_to_items_when_no_backlog():
    # Arrange
    tasks = [
        {"id": "a-done", "title": "Done", "agent": "alice", "status": "done", "priority": 0},
    ]
    # Act
    fleet = _fleet_by_agent(tasks)
    # Assert
    assert fleet["alice"]["current_task_id"] == "a-done"


def test_backlog_status_mirror_case1():
    # Arrange
    # Act
    # Assert
    assert BACKLOG_STATUS == "deferred"


def test_backlog_status_mirror_case2():
    # Arrange
    # Act
    # Assert
    assert BACKLOG_STATUS in VALID_STATUSES


def test_backlog_status_mirror_case3():
    # Arrange
    # Act
    # Assert
    assert BACKLOG_STATUS not in ABOLISHED_STATUSES


def test_backlog_status_mirror_case4():
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_in_progress_precedence_intact():
    # Arrange
    tasks = [
        {"id": "a-def", "title": "Backlog", "agent": "alice", "status": "deferred", "priority": 0},
        {"id": "a-wip", "title": "Working", "agent": "alice", "status": "in_progress", "priority": 9},
    ]
    # Act
    fleet = _fleet_by_agent(tasks)
    # Assert
    assert fleet["alice"]["current_task_id"] == "a-wip"


def test_recent_activity_precedence_intact():
    # Arrange
    tasks = [
        {"id": "a-def", "title": "Backlog", "agent": "alice", "status": "deferred", "priority": 0},
        {
            "id": "a-recent",
            "title": "Recent",
            "agent": "alice",
            "status": "blocked",
            "blocker": "waiting-on-ci",
            "priority": 9,
            "last_activity": "2026-10-12T11:00:00Z",
        },
    ]
    # Act
    fleet = _fleet_by_agent(tasks)
    # Assert
    assert fleet["alice"]["current_task_id"] == "a-recent"


def test_working_decay_doc_names_deferred_not_pending_as_rest_state_case1():
    # Arrange
    # Act
    doc = _build_fleet.__doc__ or ""
    # Assert
    assert "``deferred``" in doc


def test_working_decay_doc_names_deferred_not_pending_as_rest_state_case2():
    # Arrange
    # Act
    doc = _build_fleet.__doc__ or ""
    # Assert
    assert "in_progress→pending" not in doc


def test_working_decay_doc_names_deferred_not_pending_as_rest_state_case3():
    # Arrange
    # Act
    doc = _build_fleet.__doc__ or ""
    # Assert
    assert "abolished 2026-07-10" in doc


def test_legacy_pending_row_still_counts_as_runnable_case1():
    # The store-backed suite seeds legacy ``pending`` rows and asserts
    # ``runnable_count == 1`` for a 1-blocked + 1-pending agent: that
    # assertion holds only because ``pending`` is NOT in the
    # non-runnable set (a tolerated legacy row is still queued work).
    # Arrange
    tasks = [
    {"id": "a-blk", "title": "Blocked", "agent": "alice", "status": "blocked", "blocker": "x"},
    {"id": "a-pend", "title": "Legacy", "agent": "alice", "status": "pending", "priority": 0},
    ]
    # Act
    from scitex_cards._django.handlers.graph_fleet import (
        _LIVENESS_NONRUNNABLE,
    )
    # Assert
    fleet = _fleet_by_agent(tasks)
    assert "pending" not in _LIVENESS_NONRUNNABLE


def test_legacy_pending_row_still_counts_as_runnable_case2():
    # The store-backed suite seeds legacy ``pending`` rows and asserts
    # ``runnable_count == 1`` for a 1-blocked + 1-pending agent: that
    # assertion holds only because ``pending`` is NOT in the
    # non-runnable set (a tolerated legacy row is still queued work).
    # Arrange
    tasks = [
        {"id": "a-blk", "title": "Blocked", "agent": "alice", "status": "blocked", "blocker": "x"},
        {"id": "a-pend", "title": "Legacy", "agent": "alice", "status": "pending", "priority": 0},
    ]
    # Act
    fleet = _fleet_by_agent(tasks)
    # Assert
    assert fleet["alice"]["runnable_count"] == 1


def test_legacy_pending_past_deadline_still_counts_overdue():
    # Same companion contract for the overdue leg: ``pending`` is not a
    # terminal state for :func:`is_overdue`, so a legacy pending row
    # past its deadline surfaces in ``overdue_count``.
    # Arrange
    tasks = [
        {"id": "a-late", "title": "Late", "agent": "alice", "status": "pending", "deadline": "2000-01-01"},
        {"id": "a-future", "title": "Future", "agent": "alice", "status": "pending", "deadline": "2099-01-01"},
    ]
    # Act
    fleet = _fleet_by_agent(tasks)
    # Assert
    assert fleet["alice"]["overdue_count"] == 1


def test_store_backed_module_doc_names_deferred_derivation_case1():
    # ``test_graph_fleet.py`` documents the derivation chain up front;
    # after the BACKLOG_STATUS fix that prose must name the deferred
    # backlog, not the abolished status, or the next reader re-learns
    # the dead filter from the test file itself.
    # Arrange
    from pathlib import Path
    # Act
    text = (Path(__file__).parent / "test_graph_fleet.py").read_text(encoding="utf-8")
    # Assert
    assert "top-priority pending" not in text


def test_store_backed_module_doc_names_deferred_derivation_case2():
    # ``test_graph_fleet.py`` documents the derivation chain up front;
    # after the BACKLOG_STATUS fix that prose must name the deferred
    # backlog, not the abolished status, or the next reader re-learns
    # the dead filter from the test file itself.
    # Arrange
    from pathlib import Path
    # Act
    text = (Path(__file__).parent / "test_graph_fleet.py").read_text(encoding="utf-8")
    # Assert
    assert "BACKLOG_STATUS" in text


def test_store_backed_module_doc_names_deferred_derivation_case3():
    # ``test_graph_fleet.py`` documents the derivation chain up front;
    # after the BACKLOG_STATUS fix that prose must name the deferred
    # backlog, not the abolished status, or the next reader re-learns
    # the dead filter from the test file itself.
    # Arrange
    from pathlib import Path
    # Act
    text = (Path(__file__).parent / "test_graph_fleet.py").read_text(encoding="utf-8")
    # Assert
    assert "abolished 2026-07-10" in text


def test_backlog_leg_picks_top_priority_among_deferred():
    # Arrange — two deferred rows, no activity: the leg sorts the
    # BACKLOG_STATUS pool by _priority_key (lower first).
    # Act
    tasks = [
        {"id": "a-lo", "title": "Lo", "agent": "alice", "status": "deferred", "priority": 5},
        {"id": "a-hi", "title": "Hi", "agent": "alice", "status": "deferred", "priority": 0},
    ]
    fleet = _fleet_by_agent(tasks)
    # Assert — one assertion per test.
    assert fleet["alice"]["current_task_id"] == "a-hi"


def test_backlog_leg_breaks_priority_ties_by_id():
    # Arrange — equal priority (absent) rows sort by id ascending.
    # Act
    tasks = [
        {"id": "a-b", "title": "B", "agent": "alice", "status": "deferred"},
        {"id": "a-a", "title": "A", "agent": "alice", "status": "deferred"},
    ]
    fleet = _fleet_by_agent(tasks)
    # Assert — one assertion per test.
    assert fleet["alice"]["current_task_id"] == "a-a"
