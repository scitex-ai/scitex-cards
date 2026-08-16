#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The backlog nudge must measure ENTRY into the backlog, not the last touch.

REGRESSION FOR A MEASURED DEFECT (2026-08-16). ``detect_pending_backlog``
passed no ``clock`` and silently inherited ``_age_hours``, which reads
``last_activity``. So commenting on a rotting deferred card reset its own
backlog alarm for a day — the exact hazard ``_store_clocks`` names in the module
that WRITES ``deferred_at``: "key any of them on ``last_activity`` and the sweep
that reads it becomes SILENCEABLE BY TYPING."

WHY IT SURVIVED SO LONG. The sibling sweep already had the fix.
``detect_blocked_external`` passes ``clock=_blocked_age_hours`` and its docstring
spells out the reasoning ("annotating a stuck card hid it for another day"). The
backlog sweep sat forty lines below that paragraph, said "Mirrors
detect_stale_active", and inherited the touch clock. A fix that exists and has
not reached its second site reads as solved from every angle.

THE POSITIVE CONTROL is :func:`test_the_backlog_sweep_is_wired_to_the_entry_clock`
— it asserts the WIRING, because every behavioural test here would pass just as
well against a hand-rolled clock that nobody had connected to the sweep. That is
precisely the shape this defect had: a correct helper, written and never called.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from scitex_cards._stale.active import detect_pending_backlog
from scitex_cards._stale.active_clocks import _deferred_age_hours

NOW = _dt.datetime(2026, 8, 16, 12, 0, 0, tzinfo=_dt.timezone.utc)
LONG_AGO = "2026-07-01T00:00:00Z"
JUST_NOW = "2026-08-16T11:59:00Z"


@pytest.fixture()
def rotting_card() -> dict:
    """Deferred since July, but commented on a minute ago — the defect's shape."""
    return {
        "id": "c1",
        "status": "deferred",
        "agent": "someone",
        "deferred_at": LONG_AGO,
        "last_activity": JUST_NOW,
        "created_at": LONG_AGO,
    }


def test_a_comment_does_not_hide_a_rotting_card(rotting_card):
    # Arrange — the whole defect: fresh touch, ancient entry into the backlog.
    expected_owner = "someone"
    # Act
    result = detect_pending_backlog([rotting_card], now=NOW)
    # Assert
    assert expected_owner in result


def test_the_entry_clock_measures_from_deferred_at(rotting_card):
    # Arrange — July 1 to August 16 is well over a thousand hours.
    # Act
    age = _deferred_age_hours(rotting_card, NOW)
    # Assert
    assert age > 1000


def test_the_entry_clock_ignores_a_fresh_last_activity(rotting_card):
    # Arrange — last_activity is one minute old; the clock must not see it.
    rotting_card["last_activity"] = JUST_NOW
    # Act
    age = _deferred_age_hours(rotting_card, NOW)
    # Assert
    assert age > 1000


def test_an_unstamped_card_falls_back_to_created_at():
    # Arrange — cards deferred before the stamp shipped carry no deferred_at.
    task = {"id": "c2", "status": "deferred", "created_at": LONG_AGO}
    # Act
    age = _deferred_age_hours(task, NOW)
    # Assert
    assert age > 1000


def test_the_fallback_is_never_last_activity():
    # Arrange — no deferred_at, no created_at, only a fresh touch. Routing
    # through last_activity here would reproduce the defect for every legacy
    # card at once, which is the population the fix exists to reach.
    task = {"id": "c3", "status": "deferred", "last_activity": JUST_NOW}
    # Act
    age = _deferred_age_hours(task, NOW)
    # Assert
    assert age is None


def test_a_genuinely_new_backlog_card_is_not_nudged():
    # Arrange — entered the backlog a minute ago; nobody should hear about it.
    task = {
        "id": "c4",
        "status": "deferred",
        "agent": "someone",
        "deferred_at": JUST_NOW,
        "created_at": JUST_NOW,
    }
    # Act
    result = detect_pending_backlog([task], now=NOW)
    # Assert
    assert result == {}


def test_the_backlog_sweep_is_wired_to_the_entry_clock():
    # Arrange — THE POSITIVE CONTROL. The defect was never a wrong helper; it
    # was a correct default nobody overrode. Assert the call site, because the
    # behavioural tests above would pass against an unwired clock.
    import inspect

    from scitex_cards._stale import active

    # Act
    source = inspect.getsource(active.detect_pending_backlog)
    # Assert
    assert "clock=_deferred_age_hours" in source


def test_the_blocked_sweep_still_uses_its_own_clock():
    # Arrange — the sibling whose ruling this change applies must be untouched.
    import inspect

    from scitex_cards._stale import active

    # Act
    source = inspect.getsource(active.detect_blocked_external)
    # Assert
    assert "_blocked_age_hours" in source


# EOF
