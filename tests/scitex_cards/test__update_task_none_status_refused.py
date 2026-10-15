#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the update_task explicit-None status gate.

``update_task`` (``src/scitex_cards/_store_mutate.py``) deletes any field
passed as ``None`` — but ``status`` is the card's DECISION, not an optional
label, so popping it mints a status-less row with no lane on the board
(the exact state the verb's own contract forbids: "status cannot be
cleared"). The board handler (``crud.handle_update``) already 400s an
explicit ``{"status": null}`` before the store lock, and ``status=""`` is
already refused by ``_resolve_enum_clears`` — but a direct Python caller
passing ``status=None`` sailed through both and reached the pop loop,
silently deleting the key. Same must-carry-a-decision class as the
pending-abolition (2026-07-10) holes the reopen / restore / upsert /
create gates close.

These pins call the verb with NO store fixture: the refusal fires before
resolution, so they run in containers where the store-backed suite refuses
via the fail-LOUD guard. The full round-trip (a rejected update leaves the
row intact) stays store-backed by design.

STRICT scitex doctrine: no unittest.mock, no monkeypatch, AAA markers each
on its own line, one assertion per test. No store is touched — every test is
pure (the refused calls raise before the lock; the absent-status side is
pinned via source order, never a live write).
"""

from __future__ import annotations

import inspect

import pytest

from scitex_cards._store_mutate import update_task
from scitex_cards._task import TaskValidationError


def test_explicit_none_status_raises_before_the_store():
    # Arrange
    # Act
    def act():
        update_task(None, "update-pin-1", status=None)

    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_rejection_names_the_decision():
    # Arrange
    # Act
    def act():
        update_task(None, "update-pin-2", status=None)

    # Assert
    with pytest.raises(TaskValidationError, match="cannot be cleared"):
        act()


def test_rejection_says_what_to_do_instead():
    # Arrange
    # Act
    def act():
        update_task(None, "update-pin-3", status=None)

    # Assert
    with pytest.raises(TaskValidationError, match="decision"):
        act()


def test_bogus_store_path_still_raises_the_gate():
    # Arrange — a path that does not exist, so TaskValidationError proves
    # the decision was made without touching the store.
    # Act
    def act():
        update_task("/nonexistent/store-identity", "update-pin-4", status=None)

    # Assert
    with pytest.raises(TaskValidationError, match="cannot be cleared"):
        act()


def test_gate_sits_before_store_resolution_and_lock():
    # Arrange
    source = inspect.getsource(update_task)
    # Act
    gate_at = source.index('fields["status"] is None')
    resolve_at = source.index("_resolved_store")
    lock_at = source.index("_store_lock")
    # Assert
    assert gate_at < resolve_at < lock_at


def test_gate_only_fires_on_an_explicit_none():
    # Arrange — absent status must pass the gate (the patch leaves the row
    # untouched); pin via source so no live write is attempted.
    source = inspect.getsource(update_task)
    # Act
    present = '"status" in fields and fields["status"] is None' in source
    # Assert
    assert present


def test_docstring_records_the_none_refusal():
    # Arrange
    doc = update_task.__doc__ or ""
    # Act
    flat = " ".join(doc.split())
    # Assert
    assert "status=None" in flat


def test_empty_string_still_refused_without_a_store():
    # Arrange — the pre-existing "" refusal keeps working through the same
    # PG-free path (resolve_enum_clears, before the lock).
    # Act
    def act():
        update_task(None, "update-pin-5", status="")

    # Assert
    with pytest.raises(TaskValidationError, match="cannot clear"):
        act()


def test_detector_is_not_vacuous():
    # Arrange — the ordering check in the source pin must be capable of
    # finding the shape it looks for.
    fixture = 'fields["status"] is None\n_resolved_store\n_store_lock'
    # Act
    gate_at = fixture.index('fields["status"] is None')
    # Assert
    assert gate_at < fixture.index("_resolved_store") < fixture.index("_store_lock")
