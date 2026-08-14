#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The reconciliation decision, tested without a database.

Every case here is one the live fleet actually produced on 2026-08-10 while
two card stores were reconciled by hand. The two same-timestamp completions
in particular are not hypothetical: each host had completed a DIFFERENT
card, and last-writer-wins was blind to both because completion does not
bump ``last_activity``.

The UNRESOLVED tests matter most. A reconciler that always returns a side
looks decisive and silently reverts somebody's work.
"""

from __future__ import annotations

from scitex_cards.cardsync import Side, decide


def _card(**over):
    base = {"id": "c1", "status": "open", "last_activity": "2026-08-10T10:00:00Z"}
    base.update(over)
    return base


def _completed(at: str, **over):
    return _card(status="done", _log_meta={"completed_at": at}, **over)


# -- absence is never deletion -------------------------------------------
def test_absent_on_a_copies_from_b():
    # Arrange
    b = _card()
    # Act
    verdict = decide(None, b)
    # Assert
    assert verdict.side is Side.B


def test_absent_on_b_copies_from_a():
    # Arrange
    a = _card()
    # Act
    verdict = decide(a, None)
    # Assert
    assert verdict.side is Side.A


def test_the_absent_side_reason_names_the_rule():
    # Arrange — the 2026-07 wipe came from reading absence as deletion
    b = _card()
    # Act
    verdict = decide(None, b)
    # Assert
    assert "absence is not deletion" in verdict.reason


def test_absent_from_both_is_neither():
    # Arrange
    nothing = None
    # Act
    verdict = decide(nothing, nothing)
    # Assert
    assert verdict.side is Side.NEITHER


# -- agreement -----------------------------------------------------------
def test_identical_cards_need_no_action():
    # Arrange
    a = _card()
    # Act
    verdict = decide(a, dict(a))
    # Assert
    assert verdict.side is Side.NEITHER


def test_neither_is_not_actionable():
    # Arrange
    a = _card()
    # Act
    verdict = decide(a, dict(a))
    # Assert
    assert verdict.actionable is False


# -- last_activity ordering ----------------------------------------------
def test_later_last_activity_wins_for_a():
    # Arrange
    a = _card(last_activity="2026-08-10T12:00:00Z", status="blocked")
    b = _card(last_activity="2026-08-10T10:00:00Z")
    # Act
    verdict = decide(a, b)
    # Assert
    assert verdict.side is Side.A


def test_later_last_activity_wins_for_b():
    # Arrange
    a = _card(last_activity="2026-08-10T10:00:00Z")
    b = _card(last_activity="2026-08-10T12:00:00Z", status="blocked")
    # Act
    verdict = decide(a, b)
    # Assert
    assert verdict.side is Side.B


# -- the completion tiebreak, from the live incident ---------------------
def test_completion_breaks_a_last_activity_tie_for_a():
    # Arrange — real case: laptop had completed it, 04 still showed blocked
    a = _completed("2026-08-10T13:48:29Z")
    b = _card(status="blocked", blocker="operator-decision")
    # Act
    verdict = decide(a, b)
    # Assert
    assert verdict.side is Side.A


def test_completion_breaks_a_last_activity_tie_for_b():
    # Arrange — the mirrored real case, completed on the other host
    a = _card(status="blocked", blocker="operator-decision")
    b = _completed("2026-08-10T12:50:45Z")
    # Act
    verdict = decide(a, b)
    # Assert
    assert verdict.side is Side.B


def test_the_completion_reason_cites_the_stamp():
    # Arrange
    a = _completed("2026-08-10T13:48:29Z")
    b = _card(status="blocked")
    # Act
    verdict = decide(a, b)
    # Assert
    assert "2026-08-10T13:48:29Z" in verdict.reason


# -- UNRESOLVED must stay unresolved -------------------------------------
def test_a_tie_with_no_completion_is_unresolved():
    # Arrange — same timestamp, both incomplete, genuinely ambiguous
    a = _card(status="blocked")
    b = _card(status="deferred")
    # Act
    verdict = decide(a, b)
    # Assert
    assert verdict.side is Side.UNRESOLVED


def test_a_tie_where_BOTH_completed_is_unresolved():
    # Arrange — two completions, no basis to prefer one
    a = _completed("2026-08-10T13:00:00Z", note="from laptop")
    b = _completed("2026-08-10T13:00:00Z", note="from 04")
    # Act
    verdict = decide(a, b)
    # Assert
    assert verdict.side is Side.UNRESOLVED


def test_unresolved_is_not_actionable():
    # Arrange — the guard against a caller treating it as "pick A"
    a = _card(status="blocked")
    b = _card(status="deferred")
    # Act
    verdict = decide(a, b)
    # Assert
    assert verdict.actionable is False


def test_unresolved_names_the_differing_fields():
    # Arrange — a human has to settle these, so say what differs
    a = _card(status="blocked")
    b = _card(status="deferred")
    # Act
    verdict = decide(a, b)
    # Assert
    assert "status" in verdict.reason


# -- the function must not mutate its inputs -----------------------------
def test_decide_does_not_mutate_a():
    # Arrange
    a = _card(status="blocked")
    before = dict(a)
    # Act
    decide(a, _card(status="deferred"))
    # Assert
    assert a == before

# EOF
