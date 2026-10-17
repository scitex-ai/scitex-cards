#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free tripwire for the fleet-liveness vs dispatcher runnable divergence.

KNOWN DIVERGENCE, decision pending:
cards-fleet-liveness-vs-dispatcher-runnable-20261013.

``_build_fleet`` counts ``runnable_count`` as tasks NOT in
``_LIVENESS_NONRUNNABLE`` (which contains ``deferred``), while the
dispatcher counts ``RUNNABLE_STATUSES`` (which contains ``deferred``) as
next-pickable. Measured 2026-10-13: a deferred-only agent reads on the
board as ``idle`` with ``runnable_count == 0`` while ``runnable_tasks``
hands out the same card.

These pins call :func:`_build_fleet` and :func:`runnable_tasks` directly
with in-memory dicts: no Django test client, no store, no PostgreSQL —
they run in containers where the store-backed suite refuses via the
fail-LOUD guard.

Each pin asserts ONE side of the divergence plus the divergence itself,
so a fix on EITHER side goes red here and points at the decision card —
update these pins per the decision, do not just repoint them.
"""

from __future__ import annotations

from scitex_cards._django.handlers.graph_fleet import (
    _LIVENESS_NONRUNNABLE,
    _build_fleet,
)
from scitex_cards._runnable import RUNNABLE_STATUSES, runnable_tasks

_DECISION = "cards-fleet-liveness-vs-dispatcher-runnable-20261013"


def _deferred_only():
    # Arrange helper: the backlog state since pending was abolished
    # (2026-07-10); the successor default every other surface ships.
    return [
        {
            "id": "d1",
            "title": "Backlog card",
            "agent": "alice",
            "status": "deferred",
            "priority": 1,
        }
    ]


def test_fleet_counts_a_deferred_only_agent_as_nothing_runnable_case1():
    # Arrange
    # Arrange
    tasks = _deferred_only()
    # Act
    (row,) = _build_fleet(tasks)
    # Assert
    assert row["runnable_count"] == 0


def test_fleet_counts_a_deferred_only_agent_as_nothing_runnable_case2():
    # Arrange
    # Arrange
    tasks = _deferred_only()
    # Act
    (row,) = _build_fleet(tasks)
    # Assert
    assert row["status"] == "idle"


def test_dispatcher_hands_out_the_same_deferred_card():
    # Arrange
    tasks = _deferred_only()
    # Act
    picked = runnable_tasks(tasks).tasks
    # Assert: the dispatch SSOT treats deferred as the live backlog.
    assert [t["id"] for t in picked] == ["d1"]


def test_the_two_surfaces_disagree_on_deferred_case1():
    # Arrange
    # Arrange
    tasks = _deferred_only()
    # Act
    (row,) = _build_fleet(tasks)
    # Assert
    picked = runnable_tasks(tasks).tasks
    assert row["runnable_count"] == 0


def test_the_two_surfaces_disagree_on_deferred_case2():
    # Arrange
    # Arrange
    tasks = _deferred_only()
    # Act
    (row,) = _build_fleet(tasks)
    picked = runnable_tasks(tasks).tasks
    # Assert
    assert len(picked) == 1


def test_deferred_sits_in_both_sets_case1():
    # Arrange + Act: the two set memberships that compose the divergence
    # are asserted together below.
    # Arrange
    # Act
    # Assert
    assert "deferred" in _LIVENESS_NONRUNNABLE


def test_deferred_sits_in_both_sets_case2():
    # Arrange + Act: the two set memberships that compose the divergence
    # are asserted together below.
    # Arrange
    # Act
    # Assert
    assert "deferred" in RUNNABLE_STATUSES


def test_in_progress_agreed_runnable_by_both_surfaces_case1():
    # Arrange: non-vacuity control — the surfaces agree here, so a
    # refactor that breaks _build_fleet/runnable_tasks wholesale cannot
    # hide behind the divergence pins above.
    # Arrange
    tasks = [
        {
            "id": "w1",
            "title": "Live card",
            "agent": "alice",
            "status": "in_progress",
            "priority": 1,
        }
    ]
    # Act
    (row,) = _build_fleet(tasks)
    # Assert
    picked = runnable_tasks(tasks).tasks
    assert row["runnable_count"] == 1


def test_in_progress_agreed_runnable_by_both_surfaces_case2():
    # Arrange: non-vacuity control — the surfaces agree here, so a
    # refactor that breaks _build_fleet/runnable_tasks wholesale cannot
    # hide behind the divergence pins above.
    # Arrange
    tasks = [
        {
            "id": "w1",
            "title": "Live card",
            "agent": "alice",
            "status": "in_progress",
            "priority": 1,
        }
    ]
    # Act
    (row,) = _build_fleet(tasks)
    picked = runnable_tasks(tasks).tasks
    # Assert
    assert [t["id"] for t in picked] == ["w1"]


# EOF
