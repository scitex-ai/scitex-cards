#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The residual must be attributed to the tick whose work the gap contained.

THE DEFECT THIS PINS. The first version of the instrument computed
``unexplained = gap - drain(N) - interval``. The gap spans
``tick_start(N-1) -> tick_start(N)``, and what happened in between is
``drain(N-1)`` then ``sleep(interval)`` — so the term subtracted must be the
PREVIOUS tick's drain, not the current one.

With equal drain times the two agree and the bug is invisible. It appears only
when drain times VARY, and then the residual silently absorbs the difference
between two ticks' work while still reading like an unowned wait. That is
exactly the failure this instrument was built to detect, reproduced inside the
instrument itself.

Measured: a 0.30s drain followed by a 0.00s drain at interval=0.2 produces
gap=0.502 on the following tick. Correct attribution gives ~0.001s unexplained;
the buggy form gives ~0.302s of pure fiction.

WHY THE OBVIOUS ASSERTION IS NOT HERE. ``gap == drain + interval + unexplained``
is tautological — ``unexplained`` is defined as that subtraction, so asserting it
tests nothing and would be a gate that cannot fail. What is falsifiable is the
SIGN: a loop cannot return sooner than its own sleep, so a negative residual
beyond clock jitter means a term is mismeasured.
"""

from __future__ import annotations

import time

from scitex_cards._channel_tick_timing import (
    NEGATIVE_TOLERANCE_S,
    TickSpans,
    TickTimer,
    format_inconsistency,
    format_spans,
)


class TestTheFirstTickCannotBeMeasured:
    def test_gap_is_none_not_zero(self):
        """No previous iteration exists, and zero would be a claim."""
        # Arrange
        timer = TickTimer(0.1)

        # Act
        timer.start_tick()
        spans = timer.end_tick()

        # Assert
        assert spans.gap_s is None

    def test_residual_is_none_on_the_first_tick(self):
        # Arrange
        timer = TickTimer(0.1)

        # Act
        timer.start_tick()
        spans = timer.end_tick()

        # Assert
        assert spans.unexplained_s is None


class TestTheResidualUsesThePreviousDrain:
    """THE LOAD-BEARING TEST — a varying drain is what exposes the defect."""

    def test_a_slow_tick_is_charged_to_the_gap_that_contained_it(self):
        # Arrange
        interval = 0.10
        timer = TickTimer(interval)
        timer.start_tick()
        time.sleep(0.20)  # tick 1: SLOW work
        timer.end_tick()
        time.sleep(interval)

        # Act
        timer.start_tick()
        spans = timer.end_tick()  # tick 2: fast work, gap contains tick 1's

        # Assert
        assert abs(spans.unexplained_s) < 0.05

    def test_the_gap_really_did_contain_the_slow_work(self):
        """POSITIVE CONTROL — without this the test above proves nothing."""
        # Arrange
        interval = 0.10
        timer = TickTimer(interval)
        timer.start_tick()
        time.sleep(0.20)
        timer.end_tick()
        time.sleep(interval)

        # Act
        timer.start_tick()
        spans = timer.end_tick()

        # Assert
        assert spans.gap_s > 0.25

    def test_the_buggy_form_would_have_invented_a_wait(self):
        """Pins the SIZE of the error, so a regression is recognisable."""
        # Arrange
        interval = 0.10
        timer = TickTimer(interval)
        timer.start_tick()
        time.sleep(0.20)
        timer.end_tick()
        time.sleep(interval)
        timer.start_tick()
        spans = timer.end_tick()

        # Act
        buggy = spans.gap_s - spans.drain_s - spans.interval_s

        # Assert
        assert buggy > 0.15


class TestTheSignInvariantCanFail:
    def test_a_healthy_tick_is_consistent(self):
        # Arrange
        spans = TickSpans(drain_s=0.01, interval_s=0.1, gap_s=0.11, unexplained_s=0.0)

        # Act
        verdict = spans.is_inconsistent

        # Assert
        assert verdict is False

    def test_a_negative_residual_is_flagged(self):
        """The falsifiable case: the loop cannot return before its own sleep."""
        # Arrange
        spans = TickSpans(drain_s=0.01, interval_s=0.1, gap_s=0.02, unexplained_s=-0.5)

        # Act
        verdict = spans.is_inconsistent

        # Assert
        assert verdict is True

    def test_clock_jitter_is_not_flagged(self):
        """Tolerance exists for scheduler noise, not to make readings pass."""
        # Arrange
        jitter = -(NEGATIVE_TOLERANCE_S / 2)
        spans = TickSpans(
            drain_s=0.01, interval_s=0.1, gap_s=0.11, unexplained_s=jitter
        )

        # Act
        verdict = spans.is_inconsistent

        # Assert
        assert verdict is False

    def test_an_unmeasured_tick_is_not_flagged(self):
        """None is "not yet known" and must not read as a violation."""
        # Arrange
        spans = TickSpans(drain_s=0.01, interval_s=0.1, gap_s=None, unexplained_s=None)

        # Act
        verdict = spans.is_inconsistent

        # Assert
        assert verdict is False


class TestTheLogLinesCarryTheTerms:
    def test_all_three_terms_appear(self):
        """A residual alone cannot be checked against its inputs."""
        # Arrange
        spans = TickSpans(drain_s=0.01, interval_s=0.1, gap_s=0.11, unexplained_s=0.0)

        # Act
        line = format_spans(spans)

        # Assert
        assert all(k in line for k in ("drain_s", "gap_s", "interval", "unexplained_s"))

    def test_an_unmeasured_term_reads_as_na(self):
        # Arrange
        spans = TickSpans(drain_s=0.01, interval_s=0.1, gap_s=None, unexplained_s=None)

        # Act
        line = format_spans(spans)

        # Assert
        assert "n/a" in line

    def test_the_warning_says_what_not_to_trust(self):
        """An error that only states what broke is half-written."""
        # Arrange
        spans = TickSpans(drain_s=0.01, interval_s=0.1, gap_s=0.02, unexplained_s=-0.5)

        # Act
        line = format_inconsistency(spans)

        # Assert
        assert "do not trust" in line.lower()


# EOF
