#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the upsert status gate (pending abolition).

``upsert_task`` (``src/scitex_cards/_store_upsert.py``) took its ``status``
parameter straight to the store with no enum check: the save path only
WARNS on bad values (operator ruling 2026-07-10 — a status value must never
cost someone their card), so an alerter passing ``status="pending"`` minted
a row in a status abolished 2026-07-10 that no live view serves. Measured
2026-10-13: ``upsert_task(id, title, status="pending")`` wrote the row and
returned normally, the warning landing in a place the caller never looks.
Same flaw class as the board reopen/restore holes (now 400 via
``crud._check_status``) and the same source-strict fix: a ``status`` outside
``VALID_STATUSES`` raises ``TaskValidationError`` BEFORE the store lock, so
a rejected upsert never touches the store.

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

from scitex_cards._store_upsert import upsert_task
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES, TaskValidationError


def test_abolished_pending_raises_before_the_store():
    # Arrange
    # Act
    def act():
        upsert_task(id="upsert-pin-1", title="x", status="pending", assignee="agent:pin")

    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_rejection_names_pending_and_the_abolition():
    # Arrange
    # Act
    def act():
        upsert_task(id="upsert-pin-2", title="x", status="pending", assignee="agent:pin")

    # Assert
    with pytest.raises(TaskValidationError, match="pending"):
        act()


def test_rejection_points_at_the_abolition_date():
    # Arrange
    # Act
    def act():
        upsert_task(id="upsert-pin-3", title="x", status="pending", assignee="agent:pin")

    # Assert
    with pytest.raises(TaskValidationError, match="abolished"):
        act()


def test_unknown_status_also_raises_before_the_store():
    # Arrange
    # Act
    def act():
        upsert_task(id="upsert-pin-4", title="x", status="archived", assignee="agent:pin")

    # Assert
    with pytest.raises(TaskValidationError, match="archived"):
        act()


def test_gate_sits_before_the_store_lock():
    # Arrange
    source = inspect.getsource(upsert_task)
    # Act
    gate_at = source.find("VALID_STATUSES")
    lock_at = source.find("_store_lock")
    # Assert
    assert gate_at != -1 and lock_at != -1 and gate_at < lock_at


def test_docstring_records_the_strict_contract():
    # Arrange
    doc = upsert_task.__doc__ or ""
    # Act
    # Assert
    assert "TaskValidationError" in doc


def test_docstring_names_the_abolished_status():
    # Arrange
    doc = upsert_task.__doc__ or ""
    # Act
    # Assert
    assert "pending" in doc


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


def test_none_status_is_refused_as_missing():
    # Arrange
    # Act
    def act():
        upsert_task(id="upsert-pin-none", title="x", status=None, assignee="agent:pin")

    # Assert
    with pytest.raises(ValueError, match="status"):
        act()


def test_empty_status_is_refused_as_missing():
    # Arrange
    # Act
    def act():
        upsert_task(id="upsert-pin-empty", title="x", status="", assignee="agent:pin")

    # Assert
    with pytest.raises(ValueError, match="status"):
        act()


def test_whitespace_status_is_refused_as_missing():
    # Arrange
    # Act
    def act():
        upsert_task(id="upsert-pin-blank", title="x", status="   ", assignee="agent:pin")

    # Assert
    with pytest.raises(ValueError, match="status"):
        act()


# EOF
