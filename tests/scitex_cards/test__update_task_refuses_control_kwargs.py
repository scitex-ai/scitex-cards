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

``expected_revision`` USED TO BE THE ONE THAT MATTERED, AND IT GRADUATED. PR
#790 refused it here because ``update_task`` was a whole-document
read-modify-write, so a per-row guard "would assert the lock on the caller's
card while overwriting every other card from the same read". #872 made the verb
declare ``touched_ids`` and the mirror intersects the write set with it, so the
write reaches exactly one row and the guard is honest. It is now a REAL
KEYWORD-ONLY PARAMETER, which is a stronger protection than refusal ever was:
a named parameter cannot be swallowed by ``**fields`` at all, so it can never
be written onto the card as data.

Its behaviour is pinned in ``test__update_task_compare_and_set.py``; what
remains here is the one property this file is about — that it is NOT a control
kwarg any more, because it is not a kwarg.

``tasks_path`` is unchanged and still refused: it is the backend/MCP name for
this function's ``store`` parameter, and card ``probe-with-assignee`` has
carried ``tasks_path='/tmp/seedprobe.yaml'`` as DATA since 2026-07-10 — the
measured instance that produced this file.

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
# expected_revision — graduated from "refused control kwarg" to real parameter #
# --------------------------------------------------------------------------- #
# The four tests that stood here asserted the REFUSAL (that the message named
# it, that it named `_write_card`, that the key never reached card_json, and
# that a sibling field did not half-apply). #872 removed the premise for that
# refusal and the parameter is now real, so those assertions were inverted
# rather than dropped: the behaviour lives in
# test__update_task_compare_and_set.py, and the ONE property belonging to THIS
# file is kept below.


def test_expected_revision_is_no_longer_a_control_kwarg():
    """It is a named parameter now, which is a STRONGER guarantee than refusal.

    This file exists because `**fields` writes any unrecognised keyword onto the
    card as data. A named keyword-only parameter is never seen by `**fields` at
    all, so the failure this file guards against is structurally impossible for
    it — not merely refused.
    """
    # Arrange
    table = _CONTROL_KWARGS
    # Act
    listed = "expected_revision" in table
    # Assert
    assert listed is False, (
        "expected_revision is back in _CONTROL_KWARGS. If the parameter was "
        "removed from update_task, say why in _db.py's revision paragraph and "
        "update test__revision_is_opt_in.py in the same change."
    )


def test_it_cannot_be_swallowed_by_the_fields_catch_all():
    """The structural version of the same claim, checked against the signature
    rather than against the refusal table."""
    # Arrange
    import inspect

    sig = inspect.signature(_store.update_task)
    # Act
    kind = sig.parameters["expected_revision"].kind
    # Assert
    assert kind is inspect.Parameter.KEYWORD_ONLY


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
