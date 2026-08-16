#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Naive and aware datetimes must never meet un-normalised.

Regression cover for the 2026-07-12 BLANK BOARD. `/graph` returned 500 and the
operator's board rendered nothing:

    _model.py:684  next_occurrence  ->  `if base >= now:`
    TypeError: can't compare offset-naive and offset-aware datetimes

Chain: handle_graph -> _build_fleet -> overdue_count -> is_overdue ->
next_deadline_for_task -> _pick_next_dt -> Repeater.next_occurrence.

A bare-date deadline (`2026-07-20`) parses NAIVE. The board/fleet callers pass a
tz-AWARE UTC `now`. They met, and the compare raised.

There were TWO instances of the same bug, not one: the comparison in
`next_occurrence`, and the `min()` over a list of candidate deadlines in
`next_deadline_for_task` — which raises identically the moment a card carries one
recurring and one bare deadline. Fixing only the line in the traceback would have
left the second one armed.
"""

import datetime as dt

import pytest

from scitex_cards._model import (
    Repeater,
    is_overdue,
    next_deadline_for_task,
)

AWARE = dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc)
NAIVE_BASE = dt.datetime(2026, 7, 1)


def test_next_occurrence_naive_base_aware_now_does_not_raise():
    """THE crash, verbatim: naive seed vs tz-aware now."""
    # Arrange
    repeater = Repeater(n=1, unit="w", catchup=False)
    # Act
    occurrence = repeater.next_occurrence(NAIVE_BASE, now=AWARE)
    # Assert — reaching this line at all is the regression cover.
    assert occurrence is not None


def _aware(d):
    """Read a naive datetime as UTC — what the module does internally.

    The assertions below need this for the same reason the code does: the
    RETURN value keeps `base`'s original awareness (naive in, naive out — that
    contract is deliberate), so a bare `result >= AWARE` in the test would raise
    the very TypeError this file exists to prevent. CI caught exactly that.
    """
    return d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d


def test_next_occurrence_returns_a_future_occurrence():
    # Arrange
    repeater = Repeater(n=1, unit="w", catchup=False)
    # Act
    occurrence = _aware(repeater.next_occurrence(NAIVE_BASE, now=AWARE))
    # Assert
    assert occurrence >= AWARE


def test_next_occurrence_preserves_the_naive_return_contract():
    """Naive in, naive out. Silently returning tz-aware values would be a
    contract change for every existing caller, dressed up as a bug fix."""
    # Arrange
    repeater = Repeater(n=1, unit="w", catchup=False)
    # Act
    occurrence = repeater.next_occurrence(NAIVE_BASE, now=AWARE)
    # Assert
    assert occurrence.tzinfo is None


def test_is_overdue_with_aware_now_does_not_raise():
    """The /graph -> overdue_count path that 500'd."""
    # Arrange
    card = {"id": "x", "status": "in_progress", "deadline": "2026-07-01 +1w"}
    # Act
    overdue = is_overdue(card, now=AWARE)
    # Assert
    assert overdue is False


def test_mixed_recurring_and_bare_deadlines_do_not_raise():
    """The SECOND instance: min() over naive + aware candidates."""
    # Arrange
    card = {
        "id": "y",
        "status": "in_progress",
        "deadlines": ["2026-07-01 +1w", "2026-08-20"],
    }
    # Act
    deadline = next_deadline_for_task(card, now=AWARE)
    # Assert — reaching this line at all is the regression cover.
    assert deadline is not None


def test_naive_now_still_works():
    """No regression for callers that pass no `now` at all."""
    # Arrange
    card = {"id": "x", "status": "in_progress", "deadline": "2026-07-01 +1w"}
    # Act
    overdue = is_overdue(card)
    # Assert
    assert overdue is False


def test_a_past_deadline_is_still_overdue():
    """The fix must not make everything 'not overdue' — that would be silent."""
    # Arrange
    card = {"id": "z", "status": "in_progress", "deadline": "2020-01-01"}
    # Act
    overdue = is_overdue(card, now=AWARE)
    # Assert
    assert overdue is True


def test_a_done_card_is_never_overdue():
    # Arrange
    card = {"id": "z", "status": "done", "deadline": "2020-01-01"}
    # Act
    overdue = is_overdue(card, now=AWARE)
    # Assert
    assert overdue is False


@pytest.mark.parametrize("unit", ["d", "w", "m", "y"])
def test_every_repeater_unit_survives_an_aware_now(unit):
    # Arrange
    repeater = Repeater(n=1, unit=unit, catchup=False)
    # Act
    occurrence = _aware(repeater.next_occurrence(NAIVE_BASE, now=AWARE))
    # Assert
    assert occurrence >= AWARE
