#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A deadline is a VIEW, never a notifier — pin the contract.

scitex-todo accepts a `deadline` with an org-style recurring repeater
(`+1d` / `+1w` / `+1m` / `+1y`), and the repeater genuinely rolls the next
occurrence forward. Agents therefore read "recurring deadline" as "this will
remind me periodically". IT WILL NOT. Two separate truths, both pinned here
because both are DOC-level promises a doc could silently break:

1. NO DEADLINE EVER NOTIFIES. The notification surface never reads a
   deadline. An owned card whose deadline has passed but whose
   ``last_activity`` is fresh yields NO nudge; the same card gone quiet DOES.
   Nudges track INACTIVITY. Deleting the deadline outright does not change
   the sweep's output at all — it is not an input.

2. A RECURRING DEADLINE IS NEVER EVEN *OVERDUE*. Precisely because the
   repeater rolls forward, the next occurrence is always in the FUTURE — so
   ``overdue=True`` NEVER matches a recurring card, at any ``now``, for
   either the `+` or the `++` form. Only a NON-recurring deadline can go
   overdue. So a recurring deadline drives neither rail: it notifies nobody
   AND it never surfaces in the overdue filter. It is a date-pill.

The structural guard at the bottom fails if a delivery module ever starts
reading a deadline (a real, queued proposal — card
``todo-recurring-deadline-does-not-nudge-docs-lie-20260712``), forcing the
docs to be corrected in the same breath.

No mocks — real dicts + a frozen ``now``, per STX-NM / PA-306.
"""

from __future__ import annotations

import datetime as _dt
import importlib
import inspect
import re

from scitex_cards._model import is_overdue, next_deadline_for_task
from scitex_cards._stale_active import (
    detect_pending_backlog,
    detect_stale_active,
)


def _utc(*args):
    return _dt.datetime(*args, tzinfo=_dt.timezone.utc)


#: "Now" for every case below.
NOW = _utc(2026, 7, 12, 12, 0, 0)

#: A deadline that HAS passed: yesterday, no repeater. This is the only shape
#: that `is_overdue` can ever match (see TestRecurringDeadlineIsNeverOverdue).
PAST_DEADLINE = "2026-07-11"

#: A weekly-recurring deadline seeded long ago. Timestamps are tz-aware on
#: purpose: `Repeater.next_occurrence` compares the seed against `now`, and a
#: BARE-date seed is parsed naive, which raises TypeError against an aware
#: `now` (pre-existing on develop; out of scope for this docs change).
RECURRING_DEADLINE = "2026-01-01T00:00:00+00:00 +1w"
RECURRING_DEADLINE_CATCHUP = "2026-01-01T00:00:00+00:00 ++1w"


def _card(*, status, last_activity, deadline=None):
    """An owned card. ``deadline`` omitted entirely when None."""
    task = {
        "id": "card-1",
        "title": "An ongoing responsibility",
        "status": status,
        "agent": "scitex-storage",
        "last_activity": last_activity,
    }
    if deadline is not None:
        task["deadline"] = deadline
    return task


class TestOverdueCardIsStillNeverNudged:
    """THE CONTRACT: a passed deadline notifies nobody.

    The card is genuinely overdue (asserted, so the no-nudge result is a real
    observation and not an artefact of a fixture that never expired) — and
    still no sweep says a word, because it was touched a minute ago.
    """

    def test_the_fresh_in_progress_card_really_is_overdue(self):
        # Arrange — overdue, but touched a minute ago (threshold is 2 h).
        task = _card(
            status="in_progress",
            last_activity="2026-07-12T11:59:00+00:00",
            deadline=PAST_DEADLINE,
        )
        # Act
        overdue = is_overdue(task, now=NOW)
        # Assert — the premise. Without it the no-nudge result below would be
        # an artefact of a fixture that never expired.
        assert overdue is True

    def test_stale_active_sweep_ignores_the_passed_deadline(self):
        # Arrange — overdue, but touched a minute ago (threshold is 2 h).
        task = _card(
            status="in_progress",
            last_activity="2026-07-12T11:59:00+00:00",
            deadline=PAST_DEADLINE,
        )
        # Act
        nudges = detect_stale_active([task], now=NOW)
        # Assert — overdue, yet nobody is told. Freshness is all that counts.
        assert nudges == {}

    def test_the_fresh_deferred_card_really_is_overdue(self):
        # Arrange — a deferred card, overdue, but touched an hour ago
        # (the backlog threshold is 24 h).
        task = _card(
            status="deferred",
            last_activity="2026-07-12T11:00:00+00:00",
            deadline=PAST_DEADLINE,
        )
        # Act
        overdue = is_overdue(task, now=NOW)
        # Assert — the premise for the backlog sweep's no-nudge result.
        assert overdue is True

    def test_backlog_sweep_ignores_the_passed_deadline(self):
        # Arrange — a deferred card, overdue, but touched an hour ago
        # (the backlog threshold is 24 h).
        task = _card(
            status="deferred",
            last_activity="2026-07-12T11:00:00+00:00",
            deadline=PAST_DEADLINE,
        )
        # Act
        nudges = detect_pending_backlog([task], now=NOW)
        # Assert
        assert nudges == {}


class TestInactivityIsWhatNudges:
    """The control: the sweeps DO fire — on silence, not on deadlines. Without
    this, the assertions above would pass even if the sweeps were dead."""

    def test_a_card_with_no_deadline_is_never_overdue(self):
        # Arrange — no deadline whatsoever; simply untouched for ~2 days.
        task = _card(
            status="in_progress",
            last_activity="2026-07-10T12:00:00+00:00",
        )
        # Act
        overdue = is_overdue(task, now=NOW)
        # Assert — the premise: this card is nudged despite never being overdue.
        assert overdue is False

    def test_quiet_card_with_no_deadline_at_all_nudges_its_owner(self):
        # Arrange — no deadline whatsoever; simply untouched for ~2 days.
        task = _card(
            status="in_progress",
            last_activity="2026-07-10T12:00:00+00:00",
        )
        # Act
        nudges = detect_stale_active([task], now=NOW)
        # Assert — nudged anyway; silence, not the deadline, is the trigger.
        assert list(nudges) == ["scitex-storage"]

    def test_quiet_card_with_no_deadline_at_all_is_named_in_the_nudge(self):
        # Arrange — no deadline whatsoever; simply untouched for ~2 days.
        task = _card(
            status="in_progress",
            last_activity="2026-07-10T12:00:00+00:00",
        )
        # Act
        nudges = detect_stale_active([task], now=NOW)
        # Assert
        assert [c.id for c in nudges["scitex-storage"]] == ["card-1"]


class TestSweepOutputIsIndependentOfTheDeadline:
    """The sharpest form: the deadline is not merely insufficient to nudge —
    it is not an INPUT to the sweep at all. Add it, remove it: same answer."""

    def _shape(self, nudges):
        return {
            owner: [(c.id, c.status, c.age_hours) for c in cards]
            for owner, cards in nudges.items()
        }

    def test_the_card_without_a_deadline_is_not_overdue(self):
        # Arrange — the quiet card, no deadline.
        without = _card(
            status="in_progress",
            last_activity="2026-07-10T12:00:00+00:00",
        )
        # Act
        overdue = is_overdue(without, now=NOW)
        # Assert — half the premise: the two cards DIFFER in overdue-ness.
        assert overdue is False

    def test_the_card_with_a_passed_deadline_is_overdue(self):
        # Arrange — the same quiet card, plus a passed deadline.
        with_deadline = _card(
            status="in_progress",
            last_activity="2026-07-10T12:00:00+00:00",
            deadline=PAST_DEADLINE,
        )
        # Act
        overdue = is_overdue(with_deadline, now=NOW)
        # Assert — the other half of the premise.
        assert overdue is True

    def test_adding_a_deadline_changes_the_sweep_output_not_at_all(self):
        # Arrange — the same quiet card, with and without a passed deadline.
        without = _card(
            status="in_progress",
            last_activity="2026-07-10T12:00:00+00:00",
        )
        with_deadline = _card(
            status="in_progress",
            last_activity="2026-07-10T12:00:00+00:00",
            deadline=PAST_DEADLINE,
        )
        # Act
        a = detect_stale_active([without], now=NOW)
        b = detect_stale_active([with_deadline], now=NOW)
        # Assert — identical output, though only one of the two is overdue.
        assert self._shape(a) == self._shape(b)


class TestRecurringDeadlineIsNeverOverdue:
    """Truth 2: the repeater rolls the next occurrence into the FUTURE, so a
    recurring card can never satisfy `is_overdue` — it is not merely
    un-notified, it never even reaches the `overdue=True` filter."""

    def test_next_occurrence_is_always_in_the_future(self):
        # Arrange — a weekly deadline seeded ~6 months before NOW.
        task = {"deadline": RECURRING_DEADLINE}
        # Act
        nxt = next_deadline_for_task(task, now=NOW)
        # Assert — rolled forward past today, not left in January.
        assert nxt > NOW.date().isoformat()

    def test_recurring_card_is_never_overdue_at_any_now(self):
        # Arrange — both repeater forms, sampled across a year.
        nows = [
            _utc(2026, 7, 12, 12, 0, 0),
            _utc(2026, 7, 13, 12, 0, 0),
            _utc(2026, 12, 31, 23, 0, 0),
            _utc(2027, 3, 3, 12, 0, 0),
        ]
        cases = [
            (deadline, now)
            for deadline in (RECURRING_DEADLINE, RECURRING_DEADLINE_CATCHUP)
            for now in nows
        ]
        # Act — never overdue, however long it is left.
        overdue_at = [
            (
                deadline,
                now,
                is_overdue(
                    _card(
                        status="in_progress",
                        last_activity="2026-07-12T11:59:00+00:00",
                        deadline=deadline,
                    ),
                    now=now,
                ),
            )
            for deadline, now in cases
        ]
        # Assert
        assert [flag for _d, _n, flag in overdue_at] == [False] * len(cases), (
            f"unexpectedly overdue: {[(d, str(n)) for d, n, f in overdue_at if f]}"
            " — if the repeater no longer rolls forward, the docs saying a "
            "recurring deadline never reaches the overdue filter are now wrong "
            "and must be updated."
        )

    def test_a_quiet_recurring_card_is_still_not_overdue(self):
        # Arrange — recurring deadline, untouched for ~2 days.
        task = _card(
            status="in_progress",
            last_activity="2026-07-10T12:00:00+00:00",
            deadline=RECURRING_DEADLINE,
        )
        # Act
        overdue = is_overdue(task, now=NOW)
        # Assert — the premise: the nudge below cannot be coming from overdue-ness.
        assert overdue is False

    def test_recurring_card_is_nudged_only_once_it_goes_quiet(self):
        # Arrange — recurring deadline, untouched for ~2 days.
        task = _card(
            status="in_progress",
            last_activity="2026-07-10T12:00:00+00:00",
            deadline=RECURRING_DEADLINE,
        )
        # Act
        nudges = detect_stale_active([task], now=NOW)
        # Assert — the nudge comes from the SILENCE, never from the repeater
        # (which is not overdue and never fires anything on its own).
        assert [c.id for c in nudges["scitex-storage"]] == ["card-1"]


#: The modules that DELIVER notifications. If a deadline ever gains the power
#: to notify, it happens in one of these — and the docs must change with it.
_DELIVERY_MODULES = (
    "scitex_cards._reminders",
    "scitex_cards._reminder_bodies",
    "scitex_cards._reminder_enqueue",
    "scitex_cards._reminder_liveness",
    "scitex_cards._stale_active",
    "scitex_cards._stale_active_nudge",
    "scitex_cards._backlog_triage",
    "scitex_cards._delivery._sweeps",
    "scitex_cards._delivery._daemon",
    "scitex_cards._delivery._loop",
    "scitex_cards._notify._dispatch",
    "scitex_cards._notify._rules",
    "scitex_cards._notify._resolver",
)

#: Every way a card's deadline can be read: the raw field keys, and the two
#: `_model` helpers that interpret them. A local variable merely NAMED
#: `deadline` (e.g. a socket timeout) is deliberately not matched.
_DEADLINE_READS = re.compile(
    r"""["']deadlines?["']|\bis_overdue\b|\bnext_deadline_for_task\b|\boverdue\b"""
)


class TestDeliverySurfaceDoesNotReadDeadlines:
    """The drift guard. Zero deadline reads across the delivery surface — the
    verified fact the documentation now states."""

    def test_no_delivery_module_reads_a_deadline(self):
        # Arrange
        offenders = {}
        # Act
        for name in _DELIVERY_MODULES:
            source = inspect.getsource(importlib.import_module(name))
            hits = _DEADLINE_READS.findall(source)
            if hits:
                offenders[name] = sorted(set(hits))
        # Assert
        assert offenders == {}, (
            "A delivery module now reads a card deadline: "
            f"{offenders}. Deadlines are documented fleet-wide as a VIEW that "
            "NEVER notifies (README, CHEATSHEET, MCP add_task / update_task / "
            "list_tasks, CLI --overdue, the _model docstrings, and the "
            "05_mcp-tools skill). If notifying on deadlines is now intended, "
            "that is a real feature — but update every one of those docs in "
            "the same PR, then update this test."
        )


# EOF
