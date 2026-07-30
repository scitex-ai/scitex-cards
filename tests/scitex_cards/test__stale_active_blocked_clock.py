#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The blocked-check must not be silenceable by TYPING.

Regression cover for the 2026-07-30 finding (grant): ``detect_blocked_external``
measured ``last_activity`` — "when was this touched" — while the question it asks
is "has your blocker cleared?". A comment moves ``last_activity`` and clears
nothing, so annotating a stuck card hid it for another 24 h. Of five cards that
dropped off three consecutive sweeps, three had genuinely been reclassified and
TWO had merely been commented on, the awaited transition still unposted.

That inverted the incentive, which is what made it worse than a plain miss:
recording evidence on a stuck card is the behaviour we want, and doing it hid the
card. grant ended up deliberately NOT commenting on seven genuine external waits
to keep them visible.

Note this is NOT a narrowed set — every card was inspected and answered. The
defect was that the clock measured a DIFFERENT QUANTITY than the question asked,
so no amount of fixing identity/scope filters would have touched it.

THE LOAD-BEARING CASE is the ``_commented_stuck_card`` group: a card whose
``(status, blocker)`` is UNCHANGED but which has a FRESH comment must STILL
appear. "A reclassified card drops off" passes for the broken version too, so it
proves nothing on its own — it is here only as the negative control.
"""

from __future__ import annotations

import datetime as _dt

from scitex_cards._stale_active import (
    FIELD_BLOCKED_AT,
    detect_blocked_external,
    detect_stale_active,
)
from scitex_cards._store_mutate import _stamp_blocked_at

NOW = _dt.datetime(2026, 7, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)
#: 30 h before NOW — past the 24 h lenient threshold on any reading.
PAIR_SET_LONG_AGO = "2026-07-29T06:00:00Z"
#: 1 minute before NOW — a comment that just landed.
JUST_COMMENTED = "2026-07-30T11:59:00Z"


def _blocked(cid: str, **kw) -> dict:
    """An externally-blocked card owned by grant.

    ``agent-wait`` is a REAL member of ``EXTERNAL_BLOCKERS``; an invented value
    like ``"agent"`` is filtered out by ``is_externally_blocked`` before any
    clock runs, so the test would pass/fail for the wrong reason.
    """
    t = {
        "id": cid,
        "title": f"card {cid}",
        "status": "blocked",
        "blocker": "agent-wait",
        "agent": "grant",
    }
    t.update(kw)
    return t


def _commented_stuck_card() -> dict:
    """grant's exact shape: pair unchanged for 30 h, commented 1 minute ago.

    Against the ``last_activity`` clock this card reads as 1 minute old and is
    silently dropped. That is the bug.
    """
    return _blocked(
        "still-waiting",
        **{FIELD_BLOCKED_AT: PAIR_SET_LONG_AGO},
        last_activity=JUST_COMMENTED,
    )


def test_a_fresh_comment_does_not_hide_an_uncleared_blocker():
    """THE regression: a commented-on stuck card must not vanish from the sweep."""
    # Arrange
    card = _commented_stuck_card()

    # Act
    out = detect_blocked_external([card], now=NOW)

    # Assert
    assert "grant" in out


def test_the_commented_card_is_the_one_reported():
    """Not merely "something fired" — the specific stuck card is named."""
    # Arrange
    card = _commented_stuck_card()

    # Act
    out = detect_blocked_external([card], now=NOW)

    # Assert
    assert [c.id for c in out["grant"]] == ["still-waiting"]


def test_the_reported_age_is_the_pairs_not_the_comments():
    """30 h (the pair) rather than 1 min (the comment) — the quantity changed."""
    # Arrange
    card = _commented_stuck_card()

    # Act
    out = detect_blocked_external([card], now=NOW)

    # Assert
    assert out["grant"][0].age_hours == 30.0


def test_a_genuinely_recent_block_does_not_fire():
    """The negative control: the clock must not simply always fire.

    Without this, a hardcoded "report everything" would pass every test above.
    """
    # Arrange
    card = _blocked("just-blocked", **{FIELD_BLOCKED_AT: "2026-07-30T11:00:00Z"})

    # Act
    out = detect_blocked_external([card], now=NOW)

    # Assert
    assert out == {}


def test_an_unstamped_legacy_card_still_fires():
    """The existing population: no stamp + fresh comment must still be reported.

    Cards blocked before ``blocked_at`` shipped carry no stamp. Falling back to
    ``last_activity`` would reproduce the defect for every one of them — the
    cards that motivated the fix would stay silenced BY the fix.
    """
    # Arrange
    card = _blocked(
        "legacy", created_at=PAIR_SET_LONG_AGO, last_activity=JUST_COMMENTED
    )

    # Act
    out = detect_blocked_external([card], now=NOW)

    # Assert
    assert "grant" in out


def test_an_unstamped_legacy_cards_age_comes_from_created_at():
    """created_at, not last_activity — so it reads maximally stale, not fresh."""
    # Arrange
    card = _blocked(
        "legacy", created_at=PAIR_SET_LONG_AGO, last_activity=JUST_COMMENTED
    )

    # Act
    out = detect_blocked_external([card], now=NOW)

    # Assert
    assert out["grant"][0].age_hours == 30.0


def test_stale_active_still_measures_the_last_touch():
    """The other sweep must NOT change: there, a comment legitimately IS acting.

    ``detect_stale_active`` asks "why haven't you acted?", so a fresh comment is
    a real answer. Only the blocked-check needed the pair clock.
    """
    # Arrange
    card = {
        "id": "in-flight",
        "title": "in flight",
        "status": "in_progress",
        "agent": "grant",
        "last_activity": JUST_COMMENTED,
        FIELD_BLOCKED_AT: PAIR_SET_LONG_AGO,  # must be ignored by this sweep
    }

    # Act
    out = detect_stale_active([card], now=NOW)

    # Assert
    assert out == {}


class TestStampBlockedAt:
    """``_stamp_blocked_at`` moves only when the (status, blocker) pair moves."""

    def test_entering_blocked_stamps(self):
        # Arrange
        task = {"status": "blocked", "blocker": "agent-wait"}

        # Act
        _stamp_blocked_at(task, "in_progress", None)

        # Assert
        assert task.get(FIELD_BLOCKED_AT)

    def test_a_comment_leaves_the_stamp_alone(self):
        """The whole point: a passing mutation must not reset the clock."""
        # Arrange
        task = {
            "status": "blocked",
            "blocker": "agent-wait",
            FIELD_BLOCKED_AT: PAIR_SET_LONG_AGO,
        }

        # Act
        _stamp_blocked_at(task, "blocked", "agent-wait")

        # Assert
        assert task[FIELD_BLOCKED_AT] == PAIR_SET_LONG_AGO

    def test_changing_the_blocker_restamps(self):
        """A different blocker is genuinely a NEW wait, so the clock restarts."""
        # Arrange
        task = {
            "status": "blocked",
            "blocker": "compute",
            FIELD_BLOCKED_AT: PAIR_SET_LONG_AGO,
        }

        # Act
        _stamp_blocked_at(task, "blocked", "agent-wait")

        # Assert
        assert task[FIELD_BLOCKED_AT] != PAIR_SET_LONG_AGO

    def test_a_non_blocked_card_is_never_stamped(self):
        # Arrange
        task = {"status": "in_progress"}

        # Act
        _stamp_blocked_at(task, "blocked", "agent-wait")

        # Assert
        assert FIELD_BLOCKED_AT not in task


# EOF
