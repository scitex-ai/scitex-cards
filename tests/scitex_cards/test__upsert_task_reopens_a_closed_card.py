#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``upsert_task`` makes a card exist in a STATED state, whatever state it is in.

The defect this verb exists for: an alerter owning a fixed card id had only
``add_task`` (refuses a duplicate) and ``comment_task`` (succeeds and MUTES), so
the hand-rolled ``add || comment`` fallback dropped an outage notice onto a
``done`` card, where no queue shows it. Measured 2026-09-07 on compute-03 —
a replica down ~8 h, the alert fired correctly into a place nothing reads.

The reopen case is therefore the point of the verb, not an edge of it.
"""

from __future__ import annotations

import pytest

from scitex_cards._store import add_task, complete_task, delete_task, get_task
from scitex_cards._store_upsert import upsert_task


def _mk(card_id: str, status: str = "deferred") -> None:
    add_task(id=card_id, title="seed", status=status)


# ---------------------------------------------------------------- create


def test__upsert_creates_the_card_when_it_does_not_exist():
    # Arrange
    card_id = "upsert-create-1"
    # Act
    upsert_task(id=card_id, title="fresh", status="in_progress")
    # Assert
    assert get_task(task_id=card_id)["id"] == card_id


def test__upsert_reports_created_for_a_new_card():
    # Arrange
    card_id = "upsert-create-2"
    # Act
    result = upsert_task(id=card_id, title="fresh", status="in_progress")
    # Assert
    assert result["_upsert_action"] == "created"


# ---------------------------------------------------------------- reopen


def test__upsert_reopens_a_done_card_to_the_requested_status():
    """THE WHOLE POINT. add_task refuses this id; comment_task would mute."""
    # Arrange
    card_id = "upsert-reopen-1"
    _mk(card_id)
    complete_task(task_id=card_id)
    # Act
    upsert_task(id=card_id, title="recurred", status="in_progress")
    # Assert
    assert get_task(task_id=card_id)["status"] == "in_progress"


def test__upsert_reports_reopened_when_the_status_changed():
    """A recurrence is different news from a first occurrence."""
    # Arrange
    card_id = "upsert-reopen-2"
    _mk(card_id)
    complete_task(task_id=card_id)
    # Act
    result = upsert_task(id=card_id, title="recurred", status="in_progress")
    # Assert
    assert result["_upsert_action"] == "reopened"


def test__upsert_does_not_duplicate_the_card_on_reopen():
    # Arrange
    card_id = "upsert-reopen-3"
    _mk(card_id)
    complete_task(task_id=card_id)
    # Act
    upsert_task(id=card_id, title="recurred", status="in_progress")
    # Assert — exactly one card carries this id
    from scitex_cards import list_tasks

    assert len([t for t in list_tasks() if t.get("id") == card_id]) == 1


def test__upsert_refreshes_the_title_on_an_existing_card():
    # Arrange
    card_id = "upsert-reopen-4"
    _mk(card_id)
    # Act
    upsert_task(id=card_id, title="second wording", status="in_progress")
    # Assert
    assert get_task(task_id=card_id)["title"] == "second wording"


# ---------------------------------------------------------------- blocker


def test__upsert_clears_the_blocker_when_leaving_a_blocked_state():
    """A card carrying a blocker while not blocked fails WHOLE-document
    validation, so one such card stops every other write — the failure
    complete_task learned in 2026-08."""
    # Arrange
    card_id = "upsert-blocker-1"
    add_task(id=card_id, title="seed", status="blocked", blocker="dependency")
    # Act
    upsert_task(id=card_id, title="seed", status="in_progress")
    # Assert
    assert get_task(task_id=card_id).get("blocker") is None


# ---------------------------------------------------------------- refusals


def test__upsert_refuses_to_resurrect_a_tombstoned_card():
    """A deleted card must not come back through the upsert door."""
    # Arrange
    card_id = "upsert-tombstone-1"
    _mk(card_id)
    delete_task(task_id=card_id)

    # Act
    def act():
        upsert_task(id=card_id, title="zombie", status="in_progress")

    # Assert
    with pytest.raises(ValueError, match="DELETED"):
        act()


def test__upsert_requires_a_status_rather_than_guessing_one():
    """There is no safe default: the queue surfaces only some statuses, so a
    guessed one can mute the very card this verb exists to make visible."""
    # Arrange
    card_id = "upsert-status-1"

    # Act
    def act():
        upsert_task(id=card_id, title="x", status="")

    # Assert
    with pytest.raises(ValueError, match="status"):
        act()


def test__upsert_requires_a_non_empty_id():
    # Arrange
    blank = "   "

    # Act
    def act():
        upsert_task(id=blank, title="x", status="in_progress")

    # Assert
    with pytest.raises(ValueError, match="id"):
        act()


def test__upsert_requires_a_non_empty_title():
    # Arrange
    card_id = "upsert-title-1"

    # Act
    def act():
        upsert_task(id=card_id, title="", status="in_progress")

    # Assert
    with pytest.raises(ValueError, match="title"):
        act()


# EOF
