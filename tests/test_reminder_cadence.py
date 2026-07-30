#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A parked card must not drag the digest onto a faster clock.

Regression cover for the 2026-07-12 digest-spam incident. sac received digests
#649 / #650 / #651 inside TWELVE MINUTES, each listing 97 cards of which ~86
were deliberately deferred — cards they had already triaged and consciously
parked. The digest was re-asking, every five minutes, a question the owner had
already answered.

Cause: the digest merges two sweeps with deliberately different clocks —
detect_stale_active (2 h) and detect_pending_backlog (24 h) — into one bucket,
then took a single min() of per-card intervals across BOTH. With
DEFAULT_INTERVAL_MINUTES = 5, one actionable card put the owner's ENTIRE
backlog on a five-minute loop.

That is the same wrong as charging a deferred card against an agent's WIP: it
punishes the owner for the honesty of parking something, and a channel that
interrupts you about decided work is a channel you learn to ignore.
"""

from scitex_cards._config import DEFAULT_INTERVAL_MINUTES
from scitex_cards._reminder_cadence import (
    DEFAULT_BACKLOG_INTERVAL_MINUTES,
    backlog_interval_minutes,
    resolve_owner_interval,
)


class _Card:
    def __init__(self, cid):
        self.id = cid


def _resolve(card, cfg):
    """Stand-in for _config.resolve_interval_minutes: honours a per-card knob."""
    if isinstance(card, dict) and card.get("reminder_interval_minutes"):
        return float(card["reminder_interval_minutes"])
    return DEFAULT_INTERVAL_MINUTES


def test_active_card_sets_the_clock():
    # Arrange
    cards = [_Card("active")]
    # Act
    got = resolve_owner_interval(
        cards,
        backlog_ids=set(),
        by_id={"active": {}},
        cfg={},
        resolve_interval_minutes=_resolve,
    )
    # Assert
    assert got == DEFAULT_INTERVAL_MINUTES


def test_backlog_only_owner_rides_the_lenient_clock():
    """THE BUG: this owner used to be nagged every 5 minutes."""
    # Arrange
    cards = [_Card("parked")]
    # Act
    got = resolve_owner_interval(
        cards,
        backlog_ids={"parked"},
        by_id={"parked": {}},
        cfg={},
        resolve_interval_minutes=_resolve,
    )
    # Assert
    assert got == DEFAULT_BACKLOG_INTERVAL_MINUTES


def test_backlog_only_clock_is_much_slower_than_the_active_one():
    # Arrange
    much_slower = DEFAULT_INTERVAL_MINUTES * 100
    # Act
    backlog_clock = DEFAULT_BACKLOG_INTERVAL_MINUTES
    # Assert
    assert backlog_clock > much_slower


def test_a_parked_card_cannot_pull_the_digest_faster():
    """One active card + 86 parked ones -> the active card's clock, not a
    tighter one borrowed from the pile."""
    # Arrange
    cards = [_Card("active")] + [_Card("p%d" % i) for i in range(86)]
    parked = {"p%d" % i for i in range(86)}
    by_id = {"active": {}}
    # give every parked card an absurdly tight per-card override; under the old
    # min()-across-both-sets it would have won and set a 1-minute clock.
    for p in parked:
        by_id[p] = {"reminder_interval_minutes": 1}
    # Act
    got = resolve_owner_interval(
        cards,
        backlog_ids=parked,
        by_id=by_id,
        cfg={},
        resolve_interval_minutes=_resolve,
    )
    # Assert
    assert got == DEFAULT_INTERVAL_MINUTES


def test_an_urgent_active_card_still_pulls_the_clock_tighter():
    """The feature is preserved: an ACTIVE card may still ask for a fast nag."""
    # Arrange
    cards = [_Card("urgent"), _Card("normal")]
    by_id = {"urgent": {"reminder_interval_minutes": 2}, "normal": {}}
    # Act
    got = resolve_owner_interval(
        cards,
        backlog_ids=set(),
        by_id=by_id,
        cfg={},
        resolve_interval_minutes=_resolve,
    )
    # Assert
    assert got == 2


def test_forced_interval_wins_outright():
    # Arrange
    cards = [_Card("parked")]
    # Act
    got = resolve_owner_interval(
        cards,
        backlog_ids={"parked"},
        by_id={"parked": {}},
        cfg={},
        resolve_interval_minutes=_resolve,
        forced=7.0,
    )
    # Assert
    assert got == 7.0


def test_config_can_override_the_backlog_clock():
    # Arrange
    cfg = {"backlog_interval_minutes": 90}
    # Act
    got = backlog_interval_minutes(cfg)
    # Assert
    assert got == 90


def test_bad_config_falls_back_to_the_default():
    # Arrange
    cfg = {"backlog_interval_minutes": "nonsense"}
    # Act
    got = backlog_interval_minutes(cfg)
    # Assert
    assert got == DEFAULT_BACKLOG_INTERVAL_MINUTES
