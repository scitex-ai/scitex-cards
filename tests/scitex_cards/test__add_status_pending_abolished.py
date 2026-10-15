#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the add_task status gate (pending abolition).

``add_task`` (``src/scitex_cards/_store_add.py``) refused ``status=None``
and the ``""`` sentinel but wrote any other caller-supplied status with NO
enum check: the save path only WARNS on bad values (operator ruling
2026-07-10 — a status value must never cost someone their card), so
``add_task(status="pending")`` minted a row in a status abolished
2026-07-10 that no live view serves. Measured 2026-10-14: the probe row
reached the live board, warning only (closed afterwards with reason +
comments preserved). Same flaw class as the update/upsert/reopen/restore
holes and the same source-strict fix: a ``status`` outside
``VALID_STATUSES`` raises ``TaskValidationError`` BEFORE the store guard,
so a rejected create never touches the store. Sibling create surfaces
already strict: the CLI ``add`` takes ``--status`` from a closed click
enum, the board ``handle_create`` 400s via ``crud._check_status``, and
the project-board form validates against ``canonical_statuses`` — so
only direct Python/MCP callers meet the refusal.

These pins call the verb with NO store fixture and NO ``postgres_dsn``:
the rejection fires before the store guard, so they run in containers
where the store-backed suite refuses via the fail-LOUD guard.

STRICT scitex doctrine: no unittest.mock, no monkeypatch, AAA markers each
on its own line, one assertion per test. No store is touched — every test is
pure (the invalid-status calls raise before the guard; the valid-status
side is pinned via source order + enum mirror, never a live write).
"""

from __future__ import annotations

import inspect

import pytest

from scitex_cards._store_add import add_task
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES, TaskValidationError


def test_abolished_pending_raises_before_the_store():
    # Arrange
    # Act
    def act():
        add_task(None, id="add-pin-1", title="x", status="pending",
                 assignee="agent:pin", created_by="agent:pin")

    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_rejection_names_pending_and_the_abolition():
    # Arrange
    # Act
    def act():
        add_task(None, id="add-pin-2", title="x", status="pending",
                 assignee="agent:pin", created_by="agent:pin")

    # Assert
    with pytest.raises(TaskValidationError, match="pending"):
        act()


def test_rejection_points_at_the_abolition_date():
    # Arrange
    # Act
    def act():
        add_task(None, id="add-pin-3", title="x", status="pending",
                 assignee="agent:pin", created_by="agent:pin")

    # Assert
    with pytest.raises(TaskValidationError, match="abolished"):
        act()


def test_unknown_status_also_raises_before_the_store():
    # Arrange
    # Act
    def act():
        add_task(None, id="add-pin-4", title="x", status="archived",
                 assignee="agent:pin", created_by="agent:pin")

    # Assert
    with pytest.raises(TaskValidationError, match="archived"):
        act()


def test_gate_sits_before_the_store_guard():
    # Arrange
    source = inspect.getsource(add_task)
    # Act
    gate_at = source.find("VALID_STATUSES")
    guard_at = source.find("refuse_ineffective_store")
    # Assert
    assert gate_at != -1 and guard_at != -1 and gate_at < guard_at


def test_docstring_records_the_strict_contract():
    # Arrange
    doc = add_task.__doc__ or ""
    # Act
    # Assert
    assert "TaskValidationError" in doc


def test_docstring_names_the_abolished_status():
    # Arrange
    doc = add_task.__doc__ or ""
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
