#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The digest LEADS with the few cards worth acting on, ranked — not a wall of N.

Operator, 2026-07-14 (verbatim, angry, and right): a digest that says "you own 98
cards" then lists 15 is unreadable, so it gets skimmed, so it stops being a
signal. "A list of 98 is a list of 0. Give me THREE. I will act on three; I
demonstrably do not act on 98."

So the digest now names the DIGEST_ACT_ON highest-priority, longest-ignored
cards and demotes the total to a footnote.
"""

from __future__ import annotations

from scitex_cards._reminder_bodies import DIGEST_ACT_ON, _digest_body, _rank_key
from scitex_cards._stale_active import StaleCard


def _c(id, *, priority=None, age=1.0, status="deferred") -> StaleCard:
    return StaleCard(id=id, title=id, status=status, age_hours=age, priority=priority)


# --------------------------------------------------------------------------
# ranking
# --------------------------------------------------------------------------
def test_p1_outranks_p2_regardless_of_age():
    # Arrange — a fresh P1 against an ancient P2; priority is the primary axis.
    p1_fresh = _c("p1", priority=1, age=1.0)
    p2_ancient = _c("p2", priority=2, age=999.0)

    # Act
    ranked = sorted([p2_ancient, p1_fresh], key=_rank_key)

    # Assert
    assert ranked[0].id == "p1"


def test_within_a_priority_oldest_sorts_first():
    # Arrange
    old = _c("old", priority=1, age=100.0)
    new = _c("new", priority=1, age=2.0)

    # Act
    ranked = sorted([new, old], key=_rank_key)

    # Assert
    assert [c.id for c in ranked] == ["old", "new"]


def test_a_card_with_no_priority_sorts_last():
    # Arrange — a card with no priority must not crowd out a real P2.
    p2 = _c("p2", priority=2, age=1.0)
    none = _c("none", priority=None, age=999.0)

    # Act
    ranked = sorted([none, p2], key=_rank_key)

    # Assert
    assert ranked[0].id == "p2"


# --------------------------------------------------------------------------
# the body: leads with <= DIGEST_ACT_ON, demotes the total
# --------------------------------------------------------------------------
def _listed_lines(body: str) -> list[str]:
    return [ln for ln in body.splitlines() if ln.strip().startswith("- ")]


def _body_for_98_p2_cards() -> str:
    cards = [_c(f"c{i}", priority=2, age=float(i)) for i in range(98)]
    return _digest_body(cards, attempt=1)


def test_body_shows_at_most_DIGEST_ACT_ON_cards():
    # Arrange
    # Act
    body = _body_for_98_p2_cards()

    # Assert
    assert len(_listed_lines(body)) == DIGEST_ACT_ON


def test_body_leads_with_the_TOP_ranked_card_not_the_oldest_overall():
    # Arrange — 97 ancient P2s + one fresh P1. The old digest (oldest-first)
    # would bury the P1; the ranked digest must lead with it.
    cards = [_c(f"old{i}", priority=2, age=500.0 + i) for i in range(97)]
    cards.append(_c("the-p1", priority=1, age=1.0))

    # Act
    body = _digest_body(cards, attempt=1)

    # Assert
    assert "the-p1" in _listed_lines(body)[0]


def test_body_headline_is_the_call_to_action():
    # Arrange
    # Act
    body = _body_for_98_p2_cards()

    # Assert — the headline is "ACT ON THESE 3".
    assert "ACT ON THESE" in body.splitlines()[0]


def test_body_headline_is_not_the_raw_total():
    # Arrange
    # Act
    body = _body_for_98_p2_cards()

    # Assert — "a list of 98 is a list of 0"; the total must not lead.
    assert "you own 98" not in body


def test_body_keeps_the_total_as_a_footnote_so_nothing_is_hidden():
    # Arrange
    # Act
    body = _body_for_98_p2_cards()

    # Assert
    assert "98" in body and "more open" in body


def test_a_small_backlog_has_no_footnote():
    # Arrange — 2 cards: show both, no "+more" noise.
    cards = [_c("a", priority=1), _c("b", priority=2)]

    # Act
    body = _digest_body(cards, attempt=1)

    # Assert
    assert "more open" not in body


def test_a_small_backlog_lists_every_card():
    # Arrange — 2 cards, below the DIGEST_ACT_ON cutoff.
    cards = [_c("a", priority=1), _c("b", priority=2)]

    # Act
    body = _digest_body(cards, attempt=1)

    # Assert
    assert len(_listed_lines(body)) == 2
