#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Timing for one poll-loop iteration — the three spans, and a check that can fail.

WHY THIS EXISTS. DMs reach an agent 13-25s after they are written, against a 5s
interval. NINE candidates were eliminated by direct measurement — the wrong
daemon, the mtime drain gate, the burst cap, PostgreSQL write latency, the drain
work itself, an overridden interval, MCP transport backpressure (an IDLE session
measured SLOWER, 20s vs 13s), SQLite write-lock contention on the shared sidecar,
and PostgreSQL advisory-lock contention. Every component measured fast and the
composite stayed slow, which is the shape outside observation cannot resolve.

All nine are code this package owns. What is left is the space BETWEEN
components — a queue depth, a scheduler tick, a retry backoff, a wait that is
nobody's code. :attr:`TickSpans.unexplained_s` is the first number available
here that is not a component.

THE RESIDUAL USES THE **PREVIOUS** TICK'S DRAIN. The gap spans
``tick_start(N-1) -> tick_start(N)``, and what happened in between is
``drain(N-1)`` followed by ``sleep(interval)``. Subtracting ``drain(N)`` was the
first version of this code and it is wrong whenever drain times vary: the
residual silently absorbs the difference between two ticks' work and still reads
like an unowned wait. Caught before the instrument was ever believed, on
scitex-db's warning that a residual absorbs errors in the terms it derives from.

THE INVARIANT THAT CAN ACTUALLY FAIL, and why the obvious one cannot. Asserting
``gap == drain + interval + unexplained`` is a gate that cannot fail:
``unexplained`` is DEFINED as that subtraction, so the identity is tautological
and an assertion on it tests nothing. What is falsifiable is the SIGN — a loop
cannot return sooner than its own sleep, so a meaningfully negative residual
means a term is mismeasured, the clock moved, or ticks overlapped.

:data:`NEGATIVE_TOLERANCE_S` is 50ms because monotonic-clock jitter and
scheduler granularity live well under that. It is deliberately NOT tuned to make
any observed reading pass — a tolerance chosen to fit today's numbers is the
same defect one level up.

REPORTED, NEVER RAISED. A bare ``assert`` in a long-lived delivery loop raises
outside the drain's own try/except and kills the task, stopping delivery
entirely. This module already produced one such near-miss: a missing
``import time`` that a green import did not catch, because the loop is not
called at import. A diagnostic must not be able to break the thing it measures.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

#: How negative the residual may go before it is reported as inconsistent.
#: Monotonic-clock jitter and scheduler granularity sit far below this.
NEGATIVE_TOLERANCE_S = 0.050


@dataclass(frozen=True)
class TickSpans:
    """The three measured terms of one iteration, plus the residual.

    A dataclass rather than a tuple so a caller cannot silently receive a
    differently-shaped result, and so each span is named at the point of use.

    ``gap_s`` and ``unexplained_s`` are ``None`` on the FIRST tick, where there
    is no previous iteration to measure against. That is a real "not yet known",
    distinct from zero, and collapsing the two would report the first tick as
    perfectly accounted for.
    """

    drain_s: float
    interval_s: float
    gap_s: Optional[float]
    unexplained_s: Optional[float]

    @property
    def is_inconsistent(self) -> bool:
        """True when the residual is negative beyond tolerance.

        The loop cannot return sooner than its own sleep, so this means a term
        is mismeasured — and every number derived from it afterwards is wrong in
        a way that still looks like data.
        """
        return (
            self.unexplained_s is not None
            and self.unexplained_s < -NEGATIVE_TOLERANCE_S
        )


class TickTimer:
    """Carries the cross-iteration state a poll loop needs to time itself.

    Usage per iteration::

        timer.start_tick()
        ...do the work...
        spans = timer.end_tick()

    The timer owns ``previous_start`` and ``previous_drain_s`` so the loop does
    not, which is what keeps the residual attributed to the right tick.
    """

    def __init__(self, interval_s: float) -> None:
        self._interval_s = float(interval_s)
        self._tick_start: Optional[float] = None
        self._previous_start: Optional[float] = None
        self._previous_drain_s: Optional[float] = None

    def start_tick(self) -> None:
        """Mark the beginning of an iteration and close the gap on the last."""
        now = time.monotonic()
        self._gap_s = (
            None if self._previous_start is None else now - self._previous_start
        )
        self._previous_start = now
        self._tick_start = now

    def end_tick(self) -> TickSpans:
        """Close the iteration and return its spans.

        Rolls ``previous_drain_s`` forward AFTER computing the residual, so the
        residual is built from the drain the gap actually contained.
        """
        if self._tick_start is None:
            raise RuntimeError("end_tick() called before start_tick()")
        drain_s = time.monotonic() - self._tick_start
        gap_s = self._gap_s
        unexplained = (
            None
            if gap_s is None or self._previous_drain_s is None
            else gap_s - self._previous_drain_s - self._interval_s
        )
        self._previous_drain_s = drain_s
        return TickSpans(
            drain_s=drain_s,
            interval_s=self._interval_s,
            gap_s=gap_s,
            unexplained_s=unexplained,
        )


def format_spans(spans: TickSpans) -> str:
    """One log-line body. ``n/a`` for the first tick's unknowable terms."""
    gap = "n/a" if spans.gap_s is None else f"{spans.gap_s:.3f}"
    unexplained = "n/a" if spans.unexplained_s is None else f"{spans.unexplained_s:.3f}"
    return (
        f"tick drain_s={spans.drain_s:.3f} gap_s={gap} "
        f"interval={spans.interval_s:.1f} unexplained_s={unexplained}"
    )


def format_inconsistency(spans: TickSpans) -> str:
    """The warning body for a negative residual — states what not to trust."""
    return (
        f"tick timing INCONSISTENT — residual {spans.unexplained_s:.3f}s is "
        f"negative (gap={spans.gap_s:.3f} interval={spans.interval_s:.1f}). "
        "A term is mismeasured; do not trust unexplained_s until resolved."
    )


__all__ = [
    "NEGATIVE_TOLERANCE_S",
    "TickSpans",
    "TickTimer",
    "format_inconsistency",
    "format_spans",
]

# EOF
