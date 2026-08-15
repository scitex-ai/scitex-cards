#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``update_task`` REFUSES control parameters instead of storing them as data.

THE DEFECT THIS PINS, measured on the live store 2026-08-15 across all 4,488
cards. ``update_task`` takes ``**fields`` and its docstring says "any keyword
argument becomes a field on the task". So a kwarg that is a CONTROL PARAMETER
somewhere else in the stack is not rejected and not ignored — it is written
onto the card as DATA, and the call returns success::

    key 'tasks_path'        : 1   <- card `probe-with-assignee` has carried
                                     tasks_path='/tmp/seedprobe.yaml' since
                                     2026-07-10
    key 'expected_revision' : 0

``expected_revision`` is the one that matters. ``cardsync/__init__.py`` tells
the next developer to call ``update_task(..., expected_revision=N)`` for a
compare-and-set, and PR #790 deliberately did NOT implement that: this
function is a whole-document read-modify-write, so a per-row revision guard
would assert the lock on the caller's card while overwriting every other card
from the same read — "strictly worse than the last-write-wins it was meant to
fix, because it would carry the appearance of safety".

#790 was right to refuse. What it left behind is a call that silently ACCEPTS
the request for a guard it does not provide, so the caller ends up wrong about
whether they are protected — the same failure #790 was avoiding, moved one
level up. Refusing loudly is the honest answer until the write path is
row-level (card cards-update-task-is-whole-document-rmw-blocks-row-level-
compare-and-set-20260810).

Real round-trips against the canonical store — no mocks of the thing under
test (Req STX-NM).
"""

from __future__ import annotations

import pytest

from scitex_cards import _store
from scitex_cards._store_mutate import _CONTROL_KWARGS


def _card(task_id="ctl-1"):
    """Insert a real card to mutate."""
    return _store.add_task(
        id=task_id,
        title="A card to update",
        status="in_progress",
        assignee="agent:test-suite",
    )


def _refusal_message(**kwargs) -> str:
    """Call update_task expecting refusal; return the message (or "")."""
    try:
        _store.update_task(task_id="ctl-1", **kwargs)
    except TypeError as exc:
        return str(exc)
    return ""


# --------------------------------------------------------------------------- #
# expected_revision — the compare-and-set that does not exist here            #
# --------------------------------------------------------------------------- #
def test_expected_revision_is_refused():
    # Arrange
    _card()
    # Act
    message = _refusal_message(expected_revision=5)
    # Assert
    assert "expected_revision" in message


def test_the_refusal_names_the_real_compare_and_set_path():
    # A refusal that does not say where to go leaves the caller stuck; a REAL
    # CAS exists one layer down, and the message must name it.
    # Arrange
    _card()
    # Act
    message = _refusal_message(expected_revision=5)
    # Assert
    assert "_write_card" in message


def test_a_refused_call_does_not_write_the_control_name_onto_the_card():
    # THE ACTUAL DEFECT: before this guard, the key landed in card_json.
    # Arrange
    _card()
    # Act
    _refusal_message(expected_revision=5)
    # Assert
    assert "expected_revision" not in _store.get_task(task_id="ctl-1")


def test_a_refused_call_leaves_a_sibling_field_unapplied():
    # The refusal happens BEFORE the store is read or locked, so a doomed
    # call must not half-apply the legitimate fields riding alongside it.
    # Arrange
    _card()
    # Act
    _refusal_message(status="done", expected_revision=5)
    # Assert
    assert _store.get_task(task_id="ctl-1")["status"] == "in_progress"


# --------------------------------------------------------------------------- #
# tasks_path — the backend/MCP name for this function's `store` parameter     #
# --------------------------------------------------------------------------- #
def test_tasks_path_is_refused():
    # Arrange
    _card()
    # Act
    message = _refusal_message(tasks_path="/tmp/seedprobe.yaml")
    # Assert
    assert "tasks_path" in message


def test_the_tasks_path_refusal_points_at_the_store_parameter():
    # Arrange
    _card()
    # Act
    message = _refusal_message(tasks_path="/tmp/seedprobe.yaml")
    # Assert
    assert "store" in message


def test_tasks_path_is_not_persisted_onto_the_card():
    # Pins the exact shape of the one real leak found in the live store.
    # Arrange
    _card()
    # Act
    _refusal_message(tasks_path="/tmp/seedprobe.yaml")
    # Assert
    assert "tasks_path" not in _store.get_task(task_id="ctl-1")


# --------------------------------------------------------------------------- #
# the guard must not become a general-purpose unknown-kwarg rejecter          #
# --------------------------------------------------------------------------- #
def test_an_ordinary_unknown_field_is_still_returned_as_card_data():
    # `**fields` IS the documented API — an arbitrary key is a FEATURE here
    # (card_json is truth and carries 22 keys with no typed column). This
    # guard must refuse the named control parameters ONLY, or it breaks the
    # contract it was added to protect.
    # Arrange
    _card()
    # Act
    merged = _store.update_task(task_id="ctl-1", some_new_field="hello")
    # Assert
    assert merged["some_new_field"] == "hello"


def test_an_ordinary_unknown_field_is_still_persisted():
    # Arrange
    _card()
    # Act
    _store.update_task(task_id="ctl-1", some_new_field="hello")
    # Assert
    assert _store.get_task(task_id="ctl-1")["some_new_field"] == "hello"


# --------------------------------------------------------------------------- #
# the table itself                                                            #
# --------------------------------------------------------------------------- #
def test_the_control_kwarg_table_is_not_empty():
    # Arrange
    expected_minimum = 1
    # Act
    count = len(_CONTROL_KWARGS)
    # Assert
    assert count >= expected_minimum


@pytest.mark.parametrize("name", sorted(_CONTROL_KWARGS))
def test_every_refused_name_carries_an_actionable_reason(name):
    # A bare "not accepted" sends the caller to the source; each entry must
    # say what to do instead.
    # Arrange
    minimum_useful_length = 40
    # Act
    why = _CONTROL_KWARGS[name]
    # Assert
    assert len(why.strip()) > minimum_useful_length


@pytest.mark.parametrize("name", sorted(_CONTROL_KWARGS))
def test_each_control_kwarg_is_refused(name):
    # Arrange
    _card()
    # Act
    message = _refusal_message(**{name: "x"})
    # Assert
    assert name in message

# EOF
