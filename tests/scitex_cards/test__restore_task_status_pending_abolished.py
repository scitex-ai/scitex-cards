#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the restore_task status gate (pending abolition).

``restore_task`` (``src/scitex_cards/_store_lifecycle.py``) wrote its
caller-supplied ``task`` mapping straight to the store with no enum check:
the save path only WARNS on bad values (operator ruling 2026-07-10 — a
status value must never cost someone their card), so a crafted mapping —
MCP ``restore_task`` replays the ``delete_task`` ``removed`` payload
verbatim — minted a row in a status abolished 2026-07-10 that no live view
serves. A legacy ``pending`` row deleted long ago then undone re-minted
the abolished status the same way. Same flaw class as the board restore
hole (``undo.handle_restore`` now 400s via ``crud._check_status``) and the
upsert hole (now raises before the lock), and the same source-strict fix:
a present-but-outside-``VALID_STATUSES`` status raises
``TaskValidationError`` BEFORE store resolution, and a mapping with NO
status defaults to ``"deferred"`` on the COPY (the ``handle_restore`` twin
default — the caller's dict is never mutated, the Undo payload stays
replayable), so a rejected restore never touches the store.

These pins call the verb with a BOGUS store path and NO ``postgres_dsn``:
the rejection fires before resolution, so they run in containers where the
store-backed suite refuses via the fail-LOUD guard.

STRICT scitex doctrine: no unittest.mock, no monkeypatch, AAA markers each
on its own line, one assertion per test. No store is touched — every test is
pure (the invalid-status calls raise before resolution; the default lands
on the copy, pinned via source order, never a live write).
"""

from __future__ import annotations

import inspect

import pytest

from scitex_cards._store_lifecycle import restore_task
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES, TaskValidationError

_BOGUS_STORE = "/definitely/not/a/restore-pin-store"


def test_abolished_pending_raises_before_the_store():
    # Arrange
    # Act
    def act():
        restore_task(_BOGUS_STORE, task={"id": "restore-pin-1", "status": "pending"})

    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_rejection_names_pending_and_the_abolition():
    # Arrange
    # Act
    def act():
        restore_task(_BOGUS_STORE, task={"id": "restore-pin-2", "status": "pending"})

    # Assert
    with pytest.raises(TaskValidationError, match="pending"):
        act()


def test_rejection_points_at_the_abolition_date():
    # Arrange
    # Act
    def act():
        restore_task(_BOGUS_STORE, task={"id": "restore-pin-3", "status": "pending"})

    # Assert
    with pytest.raises(TaskValidationError, match="abolished"):
        act()


def test_unknown_status_also_raises_before_the_store():
    # Arrange
    # Act
    def act():
        restore_task(_BOGUS_STORE, task={"id": "restore-pin-4", "status": "archived"})

    # Assert
    with pytest.raises(TaskValidationError, match="archived"):
        act()


def test_gate_sits_before_store_resolution_and_lock():
    # Arrange
    source = inspect.getsource(restore_task)
    # Act
    gate_at = source.find("VALID_STATUSES")
    guard_at = source.find("refuse_ineffective_store")
    resolve_at = source.find("_resolved_store")
    lock_at = source.find("_store_lock")
    # Assert
    assert gate_at != -1 and guard_at != -1 and resolve_at != -1 and lock_at != -1 and gate_at < guard_at < resolve_at < lock_at


def test_blank_status_defaults_on_the_copy_not_the_caller():
    # Arrange
    source = inspect.getsource(restore_task)
    # Act
    # Assert
    assert 'restored["status"] = "deferred"' in source


def test_caller_mapping_is_never_rewritten():
    # Arrange
    source = inspect.getsource(restore_task)
    # Act
    # Assert
    assert 'task["status"] =' not in source


def test_docstring_records_the_strict_contract():
    # Arrange
    doc = restore_task.__doc__ or ""
    # Act
    # Assert
    assert "TaskValidationError" in doc


def test_docstring_names_the_abolished_status():
    # Arrange
    doc = restore_task.__doc__ or ""
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


# EOF
