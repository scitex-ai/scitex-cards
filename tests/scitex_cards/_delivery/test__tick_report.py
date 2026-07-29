#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the tick report SHAPE (:mod:`scitex_cards._delivery._tick`).

The daemon-level behaviour lives in ``test__tick_truth.py``; this module pins
the declared shape itself — the three-valued ``pending``, the validator that
refuses a malformed answer where it is built, the emitted line, and the level
escalation. Pure values, no store, no mocks.

One assertion per test (STX-TQ007).
"""

from __future__ import annotations

import logging

import pytest

from scitex_cards._delivery._tick import (
    DEFAULT_ESCALATE_AFTER,
    TickReport,
    build_report,
    fault_text,
)

FAULT = "reminder_sweep: RuntimeError: canonical store /x/cards.db does not exist"


def _idle(**kw) -> TickReport:
    """A healthy tick that found nothing to do."""
    return TickReport(tick=1, pending=0, **kw)


def _failing(*, tick=1, consecutive=1, failing_for=None) -> TickReport:
    """A tick that could not read the store: unknown pending + a fault."""
    return TickReport(
        tick=tick,
        pending=None,
        faults=(FAULT,),
        consecutive_failures=consecutive,
        failing_for_seconds=failing_for,
    )


class TestThreeValuedPending:
    def test_no_notifications_pending_reads_as_zero(self):
        # Arrange
        report = _idle()
        # Act
        text = report.pending_text
        # Assert
        assert text == "0"

    def test_could_not_determine_pending_reads_as_unknown(self):
        # Arrange
        report = _failing()
        # Act
        text = report.pending_text
        # Assert
        assert text == "unknown"

    def test_unknown_is_never_rendered_as_a_number(self):
        # Arrange
        # the whole defect: an unknown answer that prints like a
        # measurement is indistinguishable from a real zero.
        report = _failing()
        # Act
        text = report.pending_text
        # Assert
        assert not text.isdigit()


class TestVerdict:
    def test_a_tick_with_a_fault_is_failed_not_idle(self):
        # Arrange
        report = _failing()
        # Act
        status = report.status
        # Assert
        assert status == "failed"

    def test_a_clean_tick_with_no_work_is_idle(self):
        # Arrange
        report = _idle()
        # Act
        status = report.status
        # Assert
        assert status == "idle"

    def test_a_clean_tick_that_delivered_is_worked(self):
        # Arrange
        report = TickReport(tick=1, pending=1, sent=1, recorded=1)
        # Act
        status = report.status
        # Assert
        assert status == "worked"

    def test_zero_sent_with_a_fault_is_a_failure(self):
        # Arrange
        # sent=0 alone is ambiguous; sent=0 WITH a fault is not.
        report = _failing()
        # Act
        failed = report.failed_tick
        # Assert
        assert failed


class TestValidator:
    """``match=`` keeps each of these to ONE assertion (STX-TQ007).

    The raises-block and the message check are a single expectation here: not
    merely "it refused" but "it refused for the stated reason". Splitting them
    into a `raises` plus a separate `assert` on `excinfo` is two.
    """

    def test_unknown_pending_without_a_fault_is_refused(self):
        # Arrange
        # an unexplained UNKNOWN is a shape-shifting answer — it must
        # fail where it is built, not three layers downstream.
        # Act
        # Assert
        with pytest.raises(ValueError, match="must name the reason"):
            TickReport(tick=1, pending=None)

    def test_a_faulted_tick_must_count_itself_as_a_failure(self):
        # Arrange
        # Act
        # Assert
        with pytest.raises(ValueError, match="must count at least itself"):
            TickReport(tick=1, pending=0, faults=(FAULT,), consecutive_failures=0)

    def test_a_clean_tick_may_not_claim_a_failure_streak(self):
        # Arrange
        # Act
        # Assert
        with pytest.raises(ValueError, match="must be 0"):
            TickReport(tick=1, pending=0, consecutive_failures=4)

    def test_a_negative_count_is_refused(self):
        # Arrange
        # Act
        # Assert
        with pytest.raises(ValueError, match="non-negative"):
            TickReport(tick=1, pending=0, sent=-1)


class TestEmittedLine:
    def test_an_idle_line_says_ok(self):
        # Arrange
        report = _idle()
        # Act
        line = report.line()
        # Assert
        assert "IDLE" in line

    def test_an_idle_line_carries_the_zero_pending_count(self):
        # Arrange
        report = _idle()
        # Act
        line = report.line()
        # Assert
        assert "pending=0" in line

    def test_a_failing_line_says_failed(self):
        # Arrange
        report = _failing()
        # Act
        line = report.line()
        # Assert
        assert "FAILED" in line

    def test_a_failing_line_carries_the_unknown_pending_token(self):
        # Arrange
        report = _failing()
        # Act
        line = report.line()
        # Assert
        assert "pending=unknown" in line

    def test_a_failing_line_names_the_underlying_reason(self):
        # Arrange
        report = _failing()
        # Act
        line = report.line()
        # Assert
        assert "canonical store /x/cards.db does not exist" in line

    def test_a_failing_line_carries_the_consecutive_count(self):
        # Arrange
        report = _failing(consecutive=1196)
        # Act
        line = report.line()
        # Assert
        assert "consecutive_failures=1196" in line

    def test_a_failing_line_says_how_long_it_has_been_failing(self):
        # Arrange
        report = _failing(consecutive=1196, failing_for=143520.0)
        # Act
        line = report.line()
        # Assert
        assert "failing_for=39h52m" in line

    def test_an_idle_line_is_not_polluted_with_streak_noise(self):
        # Arrange
        # a healthy daemon must stay quiet AND terse, or its alarms
        # get skimmed past.
        report = _idle()
        # Act
        line = report.line()
        # Assert
        assert "consecutive_failures" not in line

    def test_the_idle_and_unknown_lines_are_not_the_same_text(self):
        # Arrange
        # THE defect: for a day these two states printed identically.
        idle, broken = _idle(), _failing()
        # Act
        same = idle.line().replace("IDLE", "") == broken.line().replace("FAILED", "")
        # Assert
        assert not same


class TestEscalation:
    def test_a_healthy_tick_stays_at_info(self):
        # Arrange
        report = _idle()
        # Act
        level = report.level(escalate_after=DEFAULT_ESCALATE_AFTER)
        # Assert
        assert level == logging.INFO

    def test_the_first_failure_is_a_warning(self):
        # Arrange
        report = _failing(consecutive=1)
        # Act
        level = report.level(escalate_after=3)
        # Assert
        assert level == logging.WARNING

    def test_the_threshold_failure_escalates_to_error(self):
        # Arrange
        report = _failing(consecutive=3)
        # Act
        level = report.level(escalate_after=3)
        # Assert
        assert level == logging.ERROR

    def test_a_long_outage_stays_at_error(self):
        # Arrange
        # louder, never quieter: 1196 ticks of silence is what this
        # whole shape exists to prevent.
        report = _failing(consecutive=1196)
        # Act
        level = report.level(escalate_after=3)
        # Assert
        assert level == logging.ERROR


class TestFaultText:
    def test_a_fault_names_the_guard_that_swallowed_it(self):
        # Arrange
        exc = RuntimeError("REFUSING TO READ /x/cards.db as the store")
        # Act
        text = fault_text(exc, where="reminder_sweep")
        # Assert
        assert text.startswith("reminder_sweep: RuntimeError: ")


class TestBuildReport:
    def test_an_empty_summary_means_pending_is_unknown(self):
        # Arrange
        # the delivery pass itself raised, so we never got to look.
        # Act
        report = build_report(
            tick=7,
            summary={},
            faults=["tick: RuntimeError: boom"],
            consecutive_failures=1,
            failing_since=None,
            now=None,
        )
        # Assert
        assert report.pending is None

    def test_a_clean_empty_summary_means_pending_is_zero(self):
        # Arrange
        # Act
        report = build_report(
            tick=7,
            summary={"pending": 0},
            faults=[],
            consecutive_failures=0,
            failing_since=None,
            now=None,
        )
        # Assert
        assert report.pending == 0


# EOF
