#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`parked`: a deliberately-standing card is not an abandoned one.

The backlog sweep fires on ``deferred`` + untouched. That predicate cannot tell
a card nobody got to (what the sweep is FOR) from a north-star umbrella whose
real work lives in its children. For the umbrella the nudge is unanswerable by
construction, so it fires forever and teaches its reader to ignore the channel —
taking the genuinely abandoned cards down with it.

`parked` is the exemption, and it is a REASON rather than a boolean on purpose.
These tests pin the three properties that make it not-a-mute-button:

1. a park with a stated reason is skipped by the nudge AND by auto-expiry;
2. a park with NO reason (empty / whitespace / non-string) is NOT a park;
3. parking does NOT silence the stale-active guard over `in_progress` — you may
   park work you are not doing, never work you claim to BE doing.
"""

from __future__ import annotations

import datetime as _dt

from scitex_cards import _backlog_triage as bt
from scitex_cards import _stale_active as sa

NOW = _dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=_dt.timezone.utc)
LONG_AGO = "2026-01-01T00:00:00Z"  # ancient on every clock in these tests


def _card(**kw) -> dict:
    base = {
        "id": "c1",
        "title": "a card",
        "status": "deferred",
        "agent": "agent-a",
        "created_at": LONG_AGO,
        "last_activity": LONG_AGO,
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------
# 1. park_reason — what counts as a park
# --------------------------------------------------------------------------
def test_a_stated_reason_is_a_park():
    # Arrange
    task = _card(parked="north-star; the real work is in the child cards")
    # Act
    reason = bt.park_reason(task)
    # Assert
    assert reason == "north-star; the real work is in the child cards"


def test_a_card_with_no_parked_field_is_not_parked():
    # Arrange
    task = _card()
    # Act
    parked = bt.is_parked(task)
    # Assert
    assert parked is False


def test_an_empty_reason_is_not_a_park():
    # Arrange — a park with no stated reason is exactly the abandonment the
    # sweep should still catch, so it is not a park.
    task = _card(parked="")
    # Act
    parked = bt.is_parked(task)
    # Assert
    assert parked is False


def test_a_whitespace_only_reason_is_not_a_park():
    # Arrange
    task = _card(parked="   \n\t ")
    # Act
    parked = bt.is_parked(task)
    # Assert
    assert parked is False


def test_a_boolean_true_is_not_a_park():
    # Arrange — the whole design: `parked` is a REASON, not a flag. A bare
    # `True` is a mute button, and a mute button is how an alarm dies.
    task = _card(parked=True)
    # Act
    parked = bt.is_parked(task)
    # Assert
    assert parked is False


# --------------------------------------------------------------------------
# 2. the backlog NUDGE skips a parked card
# --------------------------------------------------------------------------
def test_the_backlog_nudge_reports_an_unparked_stale_card():
    # Arrange — an ancient, untouched deferred card is exactly what the sweep is for.
    tasks = [_card()]
    # Act
    by_owner = sa.detect_pending_backlog(tasks, now=NOW)
    # Assert
    assert [c.id for c in by_owner["agent-a"]] == ["c1"]


def test_the_backlog_nudge_skips_a_parked_card():
    # Arrange — same ancient card, but it says WHY it stands.
    tasks = [_card(parked="standing goal; children carry the work")]
    # Act
    by_owner = sa.detect_pending_backlog(tasks, now=NOW)
    # Assert — no owner row at all, because the only card was exempt.
    assert by_owner == {}


def test_the_backlog_nudge_still_reports_a_card_parked_with_no_reason():
    # Arrange — an empty reason must NOT buy an exemption.
    tasks = [_card(parked="  ")]
    # Act
    by_owner = sa.detect_pending_backlog(tasks, now=NOW)
    # Assert
    assert [c.id for c in by_owner["agent-a"]] == ["c1"]


# --------------------------------------------------------------------------
# 3. the pick-for-action DRAW skips a parked card
# --------------------------------------------------------------------------
def test_the_triage_draw_skips_a_parked_card():
    # Arrange
    tasks = [_card(parked="north star")]
    # Act
    pool = bt.candidates(tasks, now=NOW)
    # Assert
    assert pool == []


def test_the_triage_draw_still_offers_an_unparked_card():
    # Arrange — deferred 1 day ago: fresh enough to be drawn, not expired.
    recent = _card(deferred_at="2026-07-12T12:00:00Z")
    # Act
    pool = bt.candidates([recent], now=NOW)
    # Assert
    assert [t["id"] for t in pool] == ["c1"]


# --------------------------------------------------------------------------
# 4. EXPIRY — the exemption that matters most
# --------------------------------------------------------------------------
def test_an_ancient_unparked_card_expires():
    # Arrange — deferred long past the 30d horizon.
    task = _card(deferred_at=LONG_AGO)
    # Act
    expired = bt.is_expired(task, now=NOW)
    # Assert
    assert expired is True


def test_an_ancient_PARKED_card_never_expires():
    # Expiry proposes CANCELLATION and cancels on silence. Without this, a
    # standing north-star card would be auto-cancelled at the horizon for the
    # sole crime of standing — the exact opposite of what its owner asked for.
    # Age is a reason to discard work nobody is DOING, not a goal nobody has
    # ABANDONED.
    # Arrange
    task = _card(deferred_at=LONG_AGO, parked="north star; children carry the work")
    # Act
    expired = bt.is_expired(task, now=NOW)
    # Assert
    assert expired is False


def test_the_expiry_list_omits_a_parked_card():
    # Arrange
    tasks = [
        _card(id="live", deferred_at=LONG_AGO),
        _card(id="standing", deferred_at=LONG_AGO, parked="north star"),
    ]
    # Act
    rotten = bt.expired(tasks, now=NOW)
    # Assert
    assert [t["id"] for t in rotten] == ["live"]


# --------------------------------------------------------------------------
# 5. *** parking must NOT silence the abandonment guard ***
# --------------------------------------------------------------------------
def test_parking_does_NOT_silence_the_stale_active_guard():
    # THE ANTI-ABUSE TEST, and the reason the skip is not in the shared core.
    #
    # An `in_progress` card is work an agent CLAIMED. If `parked` silenced the
    # stale-active sweep too, an agent could park a card it says it is working
    # and go quiet — a claimed, silenced, untouched card, which is precisely the
    # abandonment incident the board exists to prevent.
    #
    # You may park work you are NOT doing. You may not park work you say you ARE.
    #
    # Arrange
    claimed = _card(status="in_progress", parked="please stop nagging me")
    # Act
    by_owner = sa.detect_stale_active([claimed], now=NOW)
    # Assert — still reported, parked or not.
    assert [c.id for c in by_owner["agent-a"]] == ["c1"]


# --------------------------------------------------------------------------
# 6. the field survives the model round-trip
# --------------------------------------------------------------------------
def test_parked_survives_the_task_dataclass_round_trip():
    # `Task.from_dict` DROPS unknown keys — a documented latent bug that already
    # bit `repo` (a row survived on disk but evaporated the moment it passed
    # through Task). So `parked` must be a first-class field, not an extra.
    from scitex_cards._task import Task

    # Arrange
    row = _card(parked="north star")
    # Act
    round_tripped = Task.from_dict(row).to_dict()
    # Assert
    assert round_tripped["parked"] == "north star"


def test_an_unparked_task_omits_the_key_entirely():
    # to_dict omits default-valued fields so the YAML stays compact.
    from scitex_cards._task import Task

    # Arrange
    row = _card()
    # Act
    round_tripped = Task.from_dict(row).to_dict()
    # Assert
    assert "parked" not in round_tripped
