#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board refuses the ABOLISHED status before any writer runs.

``create_card`` / ``update_card``
(``src/scitex_cards/_django/handlers/project_board.py``) check the form's
status against ``canonical_statuses()`` — the store's own ``VALID_STATUSES``,
asked for rather than restated — and refuse with a reason when it is outside
the vocabulary. ``'pending'`` was abolished 2026-07-10, so a crafted form
posting ``status='pending'`` must be refused at this layer, before the store
writer is even called (the store gates too — ``add_task`` / ``update_task``
raise ``TaskValidationError`` — but the page owes its reader a fixable answer,
not a save failure).

Every test below is hermetic: the writer seam takes an injectable callable, so
no store is opened and no PostgreSQL is needed. Each refusal test asserts the
writer was NEVER called — the refusal fires before resolution.

Non-vacuity: delete the ``status not in canonical_statuses()`` check in either
function and tests 1, 2 and 6 go RED (the recorder gets the call, ``ok`` flips
to True); widen ``canonical_statuses()`` to include ``'pending'`` and test 3
goes RED. Tests 4 and 5 pin the other direction: canonical values still flow.
"""

from __future__ import annotations

from scitex_cards._django.handlers import project_board as pb


class _Recorder:
    """A hand-written stand-in for the store writer, recording what it was sent."""

    def __init__(self):
        self.calls = []

    def __call__(self, **fields):
        self.calls.append(fields)
        return {"id": fields.get("id", fields.get("task_id"))}


def _card(status="in_progress"):
    return {
        "id": "alice-a",
        "title": "Alice A",
        "status": status,
        "project": "proj-alpha",
        "assignee": "alice",
        "agent": "alice",
        "created_by": "alice",
        "priority": 2,
    }


def _ready(rows=()):
    return pb.BoardState(
        state=pb.READY,
        principal="alice",
        is_staff=False,
        project="proj-alpha",
        rows=rows,
    )


# --- the abolished value never reaches a writer -------------------------------


def test_create_refuses_abolished_pending_before_the_writer():
    """A crafted create form posting the abolished status is refused, writer silent."""
    add = _Recorder()
    result = pb.create_card(
        _ready(), {"title": "Sneaky", "status": "pending"}, add=add
    )
    assert result.ok is False
    assert "pending" in result.error
    assert add.calls == []


def test_update_refuses_abolished_pending_before_the_writer():
    """A crafted update form posting the abolished status is refused, writer silent."""
    update = _Recorder()
    result = pb.update_card(
        _ready(rows=(_card(),)),
        {"card_id": "alice-a", "status": "pending"},
        update=update,
    )
    assert result.ok is False
    assert "pending" in result.error
    assert update.calls == []


def test_the_status_vocabulary_is_the_stores_own_and_excludes_the_abolished():
    """A page-local list would drift from the validator that gates the write;
    a widened list would re-admit the abolished value this file refuses."""
    from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

    assert pb.canonical_statuses() == tuple(VALID_STATUSES)
    for abolished in ABOLISHED_STATUSES:
        assert abolished not in pb.canonical_statuses()


# --- canonical values still flow (the pins are not a blanket refusal) ---------


def test_create_still_files_a_canonical_status():
    add = _Recorder()
    result = pb.create_card(
        _ready(), {"title": "Legit", "status": "deferred"}, add=add
    )
    assert result.ok is True
    assert len(add.calls) == 1
    assert add.calls[0]["status"] == "deferred"


def test_update_still_moves_a_card_to_a_canonical_status():
    update = _Recorder()
    result = pb.update_card(
        _ready(rows=(_card(),)),
        {"card_id": "alice-a", "status": "deferred"},
        update=update,
    )
    assert result.ok is True
    assert len(update.calls) == 1
    assert update.calls[0]["status"] == "deferred"


def test_whitespace_padded_abolished_value_is_refused_not_passed_through():
    """The form strips before checking, so padding must not smuggle it past."""
    add = _Recorder()
    result = pb.create_card(
        _ready(), {"title": "Padded", "status": "  pending  "}, add=add
    )
    assert result.ok is False
    assert "pending" in result.error
    assert add.calls == []
