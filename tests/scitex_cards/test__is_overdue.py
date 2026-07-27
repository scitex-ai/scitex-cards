#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for ``scitex_cards._model.is_overdue``.

todo-p6-overdue-ui — backend half (the fleet liveness payload now
exposes a per-agent `overdue_count`, and the CLI can use this helper
for a `--overdue` filter). No mocks — real dicts + frozen ``now``
arg per STX-NM / PA-306.
"""

from __future__ import annotations

import datetime as _dt

from scitex_cards._model import is_overdue, next_deadline_for_task


def _utc(*args):
    return _dt.datetime(*args, tzinfo=_dt.timezone.utc)


class TestIsOverdueAbsentDeadline:
    """A task with no deadline is NEVER overdue."""

    def test_no_deadline_returns_false(self):
        # Arrange
        task = {"id": "a", "title": "A", "status": "pending"}
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False


class TestIsOverduePastDeadline:
    """A task with a deadline before today is overdue."""

    def test_yesterday_yields_overdue(self):
        # Arrange — deadline yesterday, now today.
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadline": "2026-06-11",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is True

    def test_today_is_not_yet_overdue(self):
        # Arrange — deadline TODAY (boundary; "overdue" is strict <).
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadline": "2026-06-12",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False

    def test_tomorrow_is_not_overdue(self):
        # Arrange
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadline": "2026-06-13",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False


class TestIsOverdueTerminalStatuses:
    """A task in a terminal lifecycle (done/deferred/failed/goal) is NOT
    overdue regardless of its deadline. Closed = no longer actionable."""

    def test_done_with_past_deadline_is_not_overdue(self):
        # Arrange
        task = {
            "id": "a",
            "title": "A",
            "status": "done",
            "deadline": "2026-06-01",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False

    def test_deferred_with_past_deadline_is_overdue(self):
        # Arrange — deferred is NOT terminal (operator ruling 2026-07-10:
        # deferred は終了ではない). A parked card with a missed deadline is
        # exactly what overdue exists to surface; exempting it hid the rot.
        task = {
            "id": "a",
            "title": "A",
            "status": "deferred",
            "deadline": "2026-06-01",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is True

    def test_failed_with_past_deadline_is_not_overdue(self):
        # Arrange
        task = {
            "id": "a",
            "title": "A",
            "status": "failed",
            "deadline": "2026-06-01",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False


class TestIsOverdueMultipleDeadlines:
    """Per :func:`next_deadline_for_task`, multiple deadlines pick the
    soonest. is_overdue follows that — if the soonest is past, overdue."""

    def test_soonest_in_past_yields_overdue(self):
        # Arrange — two deadlines, the earlier is in the past.
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadlines": ["2026-06-10", "2026-06-20"],
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is True

    def test_all_future_deadlines_not_overdue(self):
        # Arrange
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadlines": ["2026-06-13", "2026-06-20"],
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False


class TestIsOverdueTimedDeadline:
    """A deadline carrying a TIME (`YYYY-MM-DDTHH:MM`) is overdue the moment
    its timestamp passes — NOT only after its whole day rolls over. This is the
    defect scitex-logging hit: `overdue=True` silently ignored the time-of-day
    (scitex-cards-overdue-filter-ignores-datetime-deadlines-20260724)."""

    def test_timed_deadline_is_overdue_after_its_time_same_day(self):
        # Arrange — deadline 09:00 today, now is 12:00 the SAME day.
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadline": "2026-06-12T09:00",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert — 09:00 < 12:00, so it is already overdue today.
        assert result is True

    def test_timed_deadline_is_not_overdue_before_its_time_same_day(self):
        # Arrange — deadline 15:00 today, now is 12:00 the SAME day.
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadline": "2026-06-12T15:00",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert — 12:00 has not yet reached 15:00.
        assert result is False

    def test_timed_deadline_days_in_the_past_is_overdue(self):
        # Arrange
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadline": "2026-06-10T09:00",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is True

    def test_timed_deadline_with_seconds_is_honoured(self):
        # Arrange — the full `HH:MM:SS` form must behave like `HH:MM`.
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadline": "2026-06-12T09:00:00",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is True


class TestIsOverdueDateOnlyGranularityPreserved:
    """A DATE-ONLY deadline keeps its day granularity: overdue only once the
    whole day has passed. The time-aware fix must not regress this — a bare
    "today" deadline is not overdue at any hour of that day."""

    def test_date_only_today_not_overdue_even_at_end_of_day(self):
        # Arrange — date-only deadline TODAY, now is 23:59 the same day.
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadline": "2026-06-12",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 23, 59, 59))
        # Assert — the day has not yet fully passed.
        assert result is False

    def test_date_only_today_is_overdue_the_next_day(self):
        # Arrange — same date-only deadline, now is just after midnight.
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadline": "2026-06-12",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 13, 0, 1, 0))
        # Assert
        assert result is True


class TestIsOverdueTimedRecurring:
    """A RECURRING deadline is never overdue — the repeater rolls the next
    occurrence into the future. Adding a time component must not break that."""

    def test_recurring_timed_deadline_is_never_overdue(self):
        # Arrange — weekly repeater seeded in the past, with a time-of-day.
        task = {
            "id": "a",
            "title": "A",
            "status": "in_progress",
            "deadline": "2026-06-01T09:00 +1w",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert — the next occurrence is in the future.
        assert result is False


class TestIsOverdueTimedMultiple:
    """With multiple deadlines, the soonest occurrence decides — and it decides
    at ITS OWN granularity (the winning candidate's has-time flag)."""

    def test_soonest_is_timed_and_past_yields_overdue(self):
        # Arrange — the earlier deadline is a timed one already past today.
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadlines": ["2026-06-12T09:00", "2026-08-01"],
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is True

    def test_soonest_is_future_date_only_not_overdue(self):
        # Arrange — soonest is a future date-only; a later timed one is ignored.
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadlines": ["2026-06-13", "2026-06-20T09:00"],
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False


class TestIsOverdueTimezoneAwareDeadline:
    """A deadline written with an explicit UTC offset compares correctly against
    an aware `now` — no naive-vs-aware TypeError (the 2026-07-12 blank-board)."""

    def test_deadline_with_offset_in_past_is_overdue(self):
        # Arrange — 09:00+09:00 == 00:00 UTC, well before 12:00 UTC now.
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadline": "2026-06-12T09:00+09:00",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is True

    def test_deadline_with_offset_in_future_is_not_overdue(self):
        # Arrange — 23:00-09:00 == 08:00 UTC next day; now is 12:00 UTC today.
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadline": "2026-06-12T23:00-09:00",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False


class TestIsOverdueUnparseableDeadlineIsLoud:
    """A stored deadline the parser cannot read must NOT silently read as
    "not overdue" — it is logged loudly (the write path validates deadlines, so
    a read-time parse failure signals corruption). It still must not raise, so
    one bad card cannot crash the fleet-wide overdue scan."""

    def test_unparseable_deadline_is_not_overdue_and_warns(self, caplog):
        # Arrange
        task = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "deadline": "not-a-real-date",
        }
        # Act
        with caplog.at_level("WARNING"):
            result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert — safe value AND a loud, greppable warning naming the value.
        assert result is False
        assert "unparseable stored deadline" in caplog.text
        assert "not-a-real-date" in caplog.text


class TestNextDeadlineForTaskFlattensToDate:
    """The date-pill contract: next_deadline_for_task always returns a bare
    `YYYY-MM-DD`, even for a timed deadline. is_overdue reads the full timestamp
    from the shared helper; the FE keeps its date string unchanged."""

    def test_timed_deadline_flattens_to_bare_date(self):
        # Arrange
        task = {"id": "a", "title": "A", "deadline": "2026-06-12T09:00"}
        # Act
        result = next_deadline_for_task(task, now=_utc(2026, 6, 1, 0, 0, 0))
        # Assert
        assert result == "2026-06-12"
