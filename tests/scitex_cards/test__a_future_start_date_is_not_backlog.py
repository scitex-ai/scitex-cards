#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A deferred card whose START DATE is still ahead is not backlog.

THE ALARM WAS FIRING ON CORRECT STATES. The backlog nudge ages a card by
``deferred_at`` and never read ``scheduled``, so a card deliberately dated for
next week was reported exactly like one nobody got to. Measured on the live
fleet 2026-09-06: five BACKLOG digests between 01:10Z and 05:20Z naming 29, 30,
31, 33 and 35 cards, and the named set included cards scheduled 09-07 through
09-12 — work whose owner had already said when it starts.

The cost is not noise. ``scheduled`` is the field that says "not yet, and
when", and a rail that ignores it leaves the owner one way to be quiet: park
the card. Park is load-bearing (it also suppresses the triage report's expiry
proposal), so an alarm that can only be answered by parking teaches its reader
to park by reflex, and the reflex lands on something that matters.

THE EXEMPTION IS NARROW ON PURPOSE, which is what most of this file pins:
only a STRICTLY future stamp exempts. No stamp, a past stamp, an unparseable
stamp and a stamp of TODAY all keep nudging. Today especially — today is the
day the owner said they would start, so "start it or triage it" is exactly the
right thing to say, and it matches ``_may_stop``'s own "scheduled time
reached" rule.

WHY THIS IS SAFE AGAINST ITSELF: ``scheduled`` is a bare date and can be
pushed forward forever, unlike ``parked`` which demands a written reason. That
would be a mute button if the anti-rot clock moved with it — it does not.
``deferred_at`` keeps running underneath, so ``_backlog_triage.is_expired``
still proposes cancellation at the horizon however far the start date is
pushed. The exemption quiets the nudge; it does not stop the card ageing.
"""

import datetime as _dt

from scitex_cards._stale import active as _active
from scitex_cards._stale.active import detect_pending_backlog

#: Frozen at tonight's last digest, so the ages below are the real ones.
NOW = _dt.datetime(2026, 9, 6, 5, 20, 0, tzinfo=_dt.timezone.utc)

#: Ten days into the backlog — far past the 24 h line, on any clock.
DEFERRED_AT = "2026-08-27T00:00:00Z"

OWNER = "someone"


def _card(**extra) -> dict:
    """A deferred card that IS backlog, plus whatever the test varies."""
    base = {
        "id": "c1",
        "title": "c1",
        "status": "deferred",
        "agent": OWNER,
        "deferred_at": DEFERRED_AT,
    }
    base.update(extra)
    return base


def test_a_card_with_no_start_date_is_backlog():
    """THE POSITIVE CONTROL, and it runs first for a reason.

    Every "is exempt" assertion below is an ABSENCE, and an absence proves
    nothing unless the same arrangement can produce a presence. This test
    passes before and after the fix; if it ever fails, every exemption result
    in this file is an artefact of a card that was never backlog to begin
    with.
    """
    # Arrange
    tasks = [_card()]
    # Act
    result = detect_pending_backlog(tasks, now=NOW)
    # Assert
    assert list(result) == [OWNER]


def test_the_control_card_is_genuinely_overdue_for_a_nudge():
    """The control is past the threshold, not merely present.

    Pinned separately from the test above because "the owner appears" and "the
    owner appears BECAUSE the card is old" are different claims, and only the
    second makes the exemption tests meaningful.
    """
    # Arrange
    tasks = [_card()]
    # Act
    aged = detect_pending_backlog(tasks, now=NOW)[OWNER][0].age_hours
    # Assert
    assert aged > 24.0


def test_a_future_start_date_is_not_backlog():
    """THE DEFECT. Red before the fix: the card is reported as backlog."""
    # Arrange
    tasks = [_card(scheduled="2026-09-12")]
    # Act
    result = detect_pending_backlog(tasks, now=NOW)
    # Assert
    assert result == {}


def test_a_start_date_of_today_is_still_backlog():
    """Today is the day you said you would start — so you are asked.

    This is the test that pins the answer to the boundary question. A fix
    written with ``>=`` or at date granularity passes every other test in this
    file and fails this one, which is the whole reason it exists.
    """
    # Arrange
    tasks = [_card(scheduled="2026-09-06")]
    # Act
    result = detect_pending_backlog(tasks, now=NOW)
    # Assert
    assert list(result) == [OWNER]


def test_a_past_start_date_is_still_backlog():
    """A start date that has come and gone is the strongest backlog signal."""
    # Arrange
    tasks = [_card(scheduled="2026-09-01")]
    # Act
    result = detect_pending_backlog(tasks, now=NOW)
    # Assert
    assert list(result) == [OWNER]


def test_an_unparseable_start_date_is_still_backlog():
    """FAIL TOWARD FIRING, deliberately.

    ``scheduled`` may legally carry an org repeater (``2026-09-01 +1w``), which
    the ISO parser cannot read. An unreadable stamp therefore does NOT exempt:
    a rail that fell silent on input it did not understand would be silenced by
    a typo. Widening the parser is a separate, deliberate change.
    """
    # Arrange
    tasks = [_card(scheduled="next tuesday")]
    # Act
    result = detect_pending_backlog(tasks, now=NOW)
    # Assert
    assert list(result) == [OWNER]


def test_the_exemption_does_not_leak_to_another_owners_card():
    """One owner's future date must not quiet another owner's real backlog."""
    # Arrange
    tasks = [_card(scheduled="2026-09-12"), _card(id="c2", agent="other")]
    # Act
    result = detect_pending_backlog(tasks, now=NOW)
    # Assert
    assert list(result) == ["other"]


def test_the_detector_consults_the_start_date_at_its_call_site():
    """THE WIRING PIN, and it is not redundant with the behaviour above.

    Every behavioural test in this file would keep passing if the helper were
    later moved behind another predicate that happened to agree on these six
    inputs. This names the call site, so a refactor that drops the check is a
    red test rather than a silent return of the defect — the same anti-drift
    pin the entry-clock test already applies to this function.
    """
    # Arrange
    import inspect

    # Act
    source = inspect.getsource(_active.detect_pending_backlog)
    # Assert
    assert "_scheduled_ahead" in source


# EOF
