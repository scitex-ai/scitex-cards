#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the update_task status gate (pending abolition).

``update_task`` (``src/scitex_cards/_store_mutate.py``) refused ``status=None``
and the ``""`` clear-sentinel but took any other caller-supplied ``status``
straight to the store with no enum check: the save path only WARNS on bad
values (operator ruling 2026-07-10 — a status value must never cost someone
their card), so a direct Python/CLI/MCP caller passing ``status="pending"``
minted a row in a status abolished 2026-07-10 that the board handler 400s
(``crud._check_status``) and no live view serves. The CLI (``_update.py``)
and the MCP tool (``_mcp_write.update_task``) both call this verb directly,
so the board's 400 never fired for them. Same flaw class as the
upsert/reopen/restore holes and the same source-strict fix: a ``status``
outside ``VALID_STATUSES`` raises ``TaskValidationError`` BEFORE the store
lock, so a rejected update never touches the store.

These pins call the verb with NO store fixture and NO ``postgres_dsn``: the
rejection fires before resolution, so they run in containers where the
store-backed suite refuses via the fail-LOUD guard.

STRICT scitex doctrine: no unittest.mock, no monkeypatch, AAA markers each
on its own line, one assertion per test. No store is touched — every test is
pure (the invalid-status calls raise before the lock; the valid-status side
is pinned via source order + enum mirror, never a live write).
"""

from __future__ import annotations

import inspect

import pytest

from scitex_cards._store_mutate import update_task
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES, TaskValidationError


def test_abolished_pending_raises_before_the_store():
    # Arrange
    # Act
    def act():
        update_task(task_id="update-pin-1", status="pending")

    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_rejection_names_pending_and_the_abolition():
    # Arrange
    # Act
    def act():
        update_task(task_id="update-pin-2", status="pending")

    # Assert
    with pytest.raises(TaskValidationError, match="pending"):
        act()


def test_rejection_points_at_the_abolition_date():
    # Arrange
    # Act
    def act():
        update_task(task_id="update-pin-3", status="pending")

    # Assert
    with pytest.raises(TaskValidationError, match="abolished"):
        act()


def test_unknown_status_also_raises_before_the_store():
    # Arrange
    # Act
    def act():
        update_task(task_id="update-pin-4", status="archived")

    # Assert
    with pytest.raises(TaskValidationError, match="archived"):
        act()


def test_gate_sits_before_the_store_lock():
    # Arrange
    source = inspect.getsource(update_task)
    # Act
    gate_at = source.find("VALID_STATUSES")
    lock_at = source.find("_store_lock")
    # Assert
    assert gate_at != -1 and lock_at != -1 and gate_at < lock_at


def test_docstring_records_the_strict_contract():
    # Arrange
    doc = update_task.__doc__ or ""
    # Act
    # Assert
    assert "TaskValidationError" in doc


def test_docstring_names_the_abolished_status():
    # Arrange
    doc = update_task.__doc__ or ""
    # Act
    # Assert
    assert "pending" in doc


def test_none_status_is_still_refused_as_a_clear():
    # Arrange
    # Act
    def act():
        update_task(task_id="update-pin-5", status=None)

    # Assert
    with pytest.raises(TaskValidationError, match="cannot be cleared"):
        act()


def test_empty_status_is_still_refused_as_a_clear():
    # Arrange
    # Act
    def act():
        update_task(task_id="update-pin-6", status="")

    # Assert
    with pytest.raises(TaskValidationError, match="cannot clear"):
        act()


def test_pending_is_abolished_not_valid():
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_pending_is_outside_the_live_enum():
    # Arrange
    # Act
    # Assert
    assert "pending" not in VALID_STATUSES


def test_live_enum_is_nonvacuous():
    # Arrange
    # Act
    # Assert
    assert set(VALID_STATUSES) >= {"in_progress", "deferred", "blocked", "done", "cancelled"}


# EOF
