#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the add_task explicit-None status gate.

``add_task`` (``src/scitex_cards/_store_add.py``) passed ``status=None``
straight through ``_resolve_enum_clears`` — only ``""`` is a clear-sentinel
there — and minted ``new["status"] = None``; the save path only WARNS on
bad values per the 2026-07-10 ruling, so the write reached the live store
(measured 2026-10-14: a probe ``add_task(status=None)`` against a bogus
path resolved ambient and died only on the PostgreSQL not-null
constraint — no ``TaskValidationError``). A status-less row has no lane on
the board, so "creating" it would HIDE the card rather than create it —
the create-path twin of the ``update_task(status=None)`` hole
(``cards-update-task-refuses-none-status-20261014``), in the same
must-carry-a-decision class as the pending-abolition (2026-07-10) holes
the reopen / restore / upsert gates close. Siblings already strict: the
board ``handle_create`` defaults a falsy status to ``'deferred'``, the CLI
``add`` defaults ``--status`` to ``deferred`` via click, and
``upsert_task`` raises on any status outside ``VALID_STATUSES`` (``None``
included) — so only explicit-None direct Python callers meet the refusal.

These pins call the verb with NO store fixture: the refusal fires before
the store guard, so they run in containers where the store-backed suite
refuses via the fail-LOUD guard. The full round-trip (a rejected insert
leaves the store intact) stays store-backed by design.

STRICT scitex doctrine: no unittest.mock, no monkeypatch, AAA markers each
on its own line, one assertion per test. No store is touched — every test is
pure (the refused calls raise before the guard; the default side is pinned
via source and signature, never a live write).
"""

from __future__ import annotations

import inspect

import pytest

from scitex_cards._store_add import add_task
from scitex_cards._task import TaskValidationError


def test_explicit_none_status_raises_before_the_store():
    # Arrange
    # Act
    def act():
        add_task(None, id="add-pin-1", title="pin", status=None)

    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_rejection_names_the_refusal():
    # Arrange
    # Act
    def act():
        add_task(None, id="add-pin-2", title="pin", status=None)

    # Assert
    with pytest.raises(TaskValidationError, match="cannot be None"):
        act()


def test_rejection_says_what_to_do_instead():
    # Arrange
    # Act
    def act():
        add_task(None, id="add-pin-3", title="pin", status=None)

    # Assert
    with pytest.raises(TaskValidationError, match="decision"):
        act()


def test_bogus_store_path_still_raises_the_gate():
    # Arrange — a path that does not exist, so TaskValidationError proves
    # the decision was made without touching the store (before the fix this
    # shape resolved ambient and died on the live store's constraint).
    # Act
    def act():
        add_task(
            "/nonexistent/store-identity",
            id="add-pin-4",
            title="pin",
            status=None,
        )

    # Assert
    with pytest.raises(TaskValidationError, match="cannot be None"):
        act()


def test_gate_sits_before_the_store_guard_and_resolution():
    # Arrange
    source = inspect.getsource(add_task)
    # Act
    gate_at = source.index("if status is None:")
    guard_at = source.index("refuse_ineffective_store")
    resolve_at = source.index("_resolved_store")
    # Assert
    assert gate_at < guard_at < resolve_at


def test_gate_only_fires_on_an_explicit_none():
    # Arrange — the default must stay 'deferred' (absent status never meets
    # the gate); pin via signature so no live write is attempted.
    # Act
    default = inspect.signature(add_task).parameters["status"].default
    # Assert
    assert default == "deferred"


def test_docstring_records_the_none_refusal():
    # Arrange
    doc = add_task.__doc__ or ""
    # Act
    flat = " ".join(doc.split())
    # Assert
    assert "status=None" in flat


def test_empty_string_still_refused_without_a_store():
    # Arrange — the pre-existing "" refusal keeps working through the same
    # PG-free path (resolve_enum_clears, before the guard).
    # Act
    def act():
        add_task(None, id="add-pin-5", title="pin", status="")

    # Assert
    with pytest.raises(TaskValidationError, match="cannot clear"):
        act()


def test_whitespace_status_still_refused_without_a_store():
    # Arrange — whitespace-only is a typo'd "" per is_clear_sentinel, never
    # a legal enum member, so the same PG-free refusal must fire.
    # Act
    def act():
        add_task(None, id="add-pin-6", title="pin", status="   ")

    # Assert
    with pytest.raises(TaskValidationError, match="cannot clear"):
        act()


def test_detector_is_not_vacuous():
    # Arrange — the ordering check in the source pin must be capable of
    # finding the shape it looks for.
    fixture = (
        "if status is None:\nrefuse_ineffective_store\n_resolved_store"
    )
    # Act
    gate_at = fixture.index("if status is None:")
    # Assert
    assert gate_at < fixture.index("refuse_ineffective_store") < fixture.index(
        "_resolved_store"
    )
