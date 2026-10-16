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


def test_runnable_statuses_mirror():
    assert RUNNABLE_STATUSES == frozenset({"deferred", "in_progress"})
    assert set(RUNNABLE_STATUSES) <= set(VALID_STATUSES)
    assert set(RUNNABLE_STATUSES).isdisjoint(ABOLISHED_STATUSES)
    assert "pending" in ABOLISHED_STATUSES


def test_legacy_pending_row_is_not_runnable():
    tasks = [{"id": "t-pend", "title": "Legacy", "status": "pending"}]
    assert runnable_tasks(tasks).tasks == []


def test_legacy_pending_row_is_not_next_pickable():
    tasks = [{"id": "t-pend", "title": "Legacy", "status": "pending", "agent": "alice"}]
    out = next_task(tasks, assignee="alice")
    assert out.task is None
    assert out.candidate_count == 0


def test_runnable_and_next_agree_on_pending():
    row = {"id": "t-pend", "title": "Legacy", "status": "pending", "agent": "alice"}
    assert runnable_tasks([row]).tasks == []
    assert next_task([row], assignee="alice").task is None


def test_dispatcher_docstrings_name_deferred_not_pending_as_runnable():
    for doc in (runnable_mod.__doc__ or "", next_mod.__doc__ or ""):
        assert "``deferred``, ``in_progress``" in doc
        assert "``pending``, ``in_progress``" not in doc
