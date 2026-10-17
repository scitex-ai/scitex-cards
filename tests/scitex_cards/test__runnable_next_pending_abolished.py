#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the dispatcher runnable mirror (pending abolition).

``_runnable`` and ``_next`` module docstrings still named ``pending`` as a
runnable status after ``pending`` was abolished 2026-07-10. The code was
already correct (``RUNNABLE_STATUSES == {"deferred", "in_progress"}`` with
``_next`` importing the set as SSOT) — only the prose lied, inviting the
next copy-paste to reintroduce a dead filter. The prose now names
``deferred``; these pins keep code, docs, and enum mirrors aligned.

These pins use in-memory dicts only: no store, no PostgreSQL — they run
in containers where the store-backed suite refuses via the fail-LOUD
guard.
"""

from __future__ import annotations

import scitex_cards._next as next_mod
import scitex_cards._runnable as runnable_mod
from scitex_cards._next import next_task
from scitex_cards._runnable import RUNNABLE_STATUSES, runnable_tasks
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES


def test_runnable_statuses_mirror_case1():
    # Arrange
    # Act
    # Assert
    assert RUNNABLE_STATUSES == frozenset({"deferred", "in_progress"})


def test_runnable_statuses_mirror_case2():
    # Arrange
    # Act
    # Assert
    assert set(RUNNABLE_STATUSES) <= set(VALID_STATUSES)


def test_runnable_statuses_mirror_case3():
    # Arrange
    # Act
    # Assert
    assert set(RUNNABLE_STATUSES).isdisjoint(ABOLISHED_STATUSES)


def test_runnable_statuses_mirror_case4():
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_legacy_pending_row_is_not_runnable():
    # Arrange
    # Act
    tasks = [{"id": "t-pend", "title": "Legacy", "status": "pending"}]
    # Assert
    assert runnable_tasks(tasks).tasks == []


def test_legacy_pending_row_is_not_next_pickable_case1():
    # Arrange
    tasks = [{"id": "t-pend", "title": "Legacy", "status": "pending", "agent": "alice"}]
    # Act
    out = next_task(tasks, assignee="alice")
    # Assert
    assert out.task is None


def test_legacy_pending_row_is_not_next_pickable_case2():
    # Arrange
    tasks = [{"id": "t-pend", "title": "Legacy", "status": "pending", "agent": "alice"}]
    # Act
    out = next_task(tasks, assignee="alice")
    # Assert
    assert out.candidate_count == 0


def test_runnable_and_next_agree_on_pending_case1():
    # Arrange
    # Act
    row = {"id": "t-pend", "title": "Legacy", "status": "pending", "agent": "alice"}
    # Assert
    assert runnable_tasks([row]).tasks == []


def test_runnable_and_next_agree_on_pending_case2():
    # Arrange
    # Act
    row = {"id": "t-pend", "title": "Legacy", "status": "pending", "agent": "alice"}
    # Assert
    assert next_task([row], assignee="alice").task is None


def test_dispatcher_docstrings_name_deferred_as_runnable():
    # Arrange
    docs = (runnable_mod.__doc__ or "", next_mod.__doc__ or "")
    # Act
    missing = [doc for doc in docs if "``deferred``, ``in_progress``" not in doc]
    # Assert
    assert not missing, missing


def test_dispatcher_docstrings_drop_pending_as_runnable():
    # Arrange
    docs = (runnable_mod.__doc__ or "", next_mod.__doc__ or "")
    # Act
    stale = [doc for doc in docs if "``pending``, ``in_progress``" in doc]
    # Assert
    assert not stale, stale
