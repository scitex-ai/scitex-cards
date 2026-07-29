#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The DECLARED SHAPE of one notifyd tick — and the line it emits.

Why this module exists (the silent-outage incident, 2026-07-28/29)
------------------------------------------------------------------
notifyd could not read the store on EVERY tick for roughly a day. The
exception was caught and logged ("notifyd reminder sweep raised; continuing
to delivery") but never COUNTED, so the one line an operator actually reads
stayed::

    notifyd tick 1196: sent=0 failed=0 skipped=0 failed_terminal=0 (0 recorded)

That is character-for-character what a HEALTHY IDLE daemon prints. The
operator's DMs — including the answer an agent was blocked on — never
arrived, and the log said everything was fine.

The defect was the COUNTER, not the outage. Any next outage will have a
different cause and must not be silent, so this module makes three things
structural rather than incidental (constitution §2, "answer in a fixed,
declared shape"):

1. **A swallowed exception is a FAILED tick.** ``sent=0`` WITH a fault is a
   failure, never an idle. Every guard inside the tick contributes a fault
   string to :attr:`TickReport.faults`, and a non-empty ``faults`` IS the
   failure — there is no way to log a fault without the summary showing it.
2. **``pending`` is THREE-VALUED.** "no notifications pending" is ``0``;
   "could not determine what is pending" is ``None`` and prints
   ``pending=unknown``. Collapsing unknown into either pole is named in the
   constitution as the most common bug we ship, and reporting ``0`` for
   "the store would not open" is exactly that bug.
3. **Consecutive failures ESCALATE.** The line gets LOUDER, not quieter: a
   healthy tick is INFO, a failing tick is WARNING, and once the failures
   pile up past :data:`DEFAULT_ESCALATE_AFTER` it is ERROR carrying the
   count, how long it has been failing, and the underlying reason.

An idle daemon stays QUIET on purpose. Making a healthy tick noisy is how
alarms get ignored, which would reproduce the same outage by a different
route.

The validator (:meth:`TickReport.__post_init__`) refuses a malformed answer
where it is BUILT, not three layers downstream — in particular it refuses an
unexplained ``pending=None`` (unknown must always name a fault) and refuses
a fault count that disagrees with the failure bookkeeping.
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass, field

#: Consecutive failing ticks after which the summary escalates to ERROR.
#: 3 lets a single transient blip pass at WARNING while a genuine outage —
#: which on 2026-07-28 ran to 1196 ticks — starts screaming within minutes.
DEFAULT_ESCALATE_AFTER = 3

#: What ``pending`` prints when the answer is UNKNOWN. A distinct token, never
#: a number: any digit here would read as a measurement we did not make.
PENDING_UNKNOWN_TEXT = "unknown"


def fault_text(exc: BaseException, *, where: str) -> str:
    """Render one swallowed exception as a single-line fault string.

    ``where`` names the guard that swallowed it (``reminder_sweep``,
    ``inbox_read``, …) so the emitted line says WHICH part of the tick broke,
    not merely that something did.
    """
    return f"{where}: {type(exc).__name__}: {exc}"


def _humanize_seconds(seconds: float) -> str:
    """Render a duration as ``39h52m`` / ``7m12s`` / ``4s`` (no dependencies)."""
    total = int(max(seconds, 0))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


@dataclass(frozen=True)
class TickReport:
    """One tick's outcome in a FIXED shape, with every signal a named field.

    Parameters
    ----------
    tick : int
        1-based iteration number of this tick.
    pending : int | None
        How many notifications were found waiting. ``None`` means UNKNOWN —
        the inbox could not be enumerated — and is NEVER interchangeable with
        ``0``. An unknown value must be explained by at least one fault.
    sent, failed, skipped, failed_terminal, recorded : int
        The delivery-pass tallies (unchanged meanings).
    faults : tuple[str, ...]
        Every exception swallowed inside this tick, already rendered by
        :func:`fault_text`. NON-EMPTY MEANS THE TICK FAILED.
    consecutive_failures : int
        How many ticks in a row have failed, THIS one included. ``0`` on a
        healthy tick.
    failing_for_seconds : float | None
        How long the current failing streak has lasted. ``None`` when the tick
        is healthy or when the streak's start is not known.
    """

    tick: int
    pending: int | None
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    failed_terminal: int = 0
    recorded: int = 0
    faults: tuple[str, ...] = field(default_factory=tuple)
    consecutive_failures: int = 0
    failing_for_seconds: float | None = None

    def __post_init__(self) -> None:
        """Refuse a malformed report AT THE POINT IT IS BUILT.

        A shape-shifting answer is how "I could not tell" silently becomes
        "yes", so the invariants that make the emitted line trustworthy are
        enforced here rather than trusted downstream.
        """
        if self.tick < 1:
            raise ValueError(f"tick must be 1-based, got {self.tick!r}")
        for name in ("sent", "failed", "skipped", "failed_terminal", "recorded"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative int, got {value!r}")
        if self.pending is not None and (
            not isinstance(self.pending, int) or self.pending < 0
        ):
            raise ValueError(
                f"pending must be a non-negative int or None (UNKNOWN), "
                f"got {self.pending!r}"
            )
        if self.pending is None and not self.faults:
            raise ValueError(
                "pending=UNKNOWN with no fault: an unknown answer must name the "
                "reason it is unknown, otherwise it is indistinguishable from a "
                "measurement nobody took"
            )
        if self.consecutive_failures < 0:
            raise ValueError(
                f"consecutive_failures must be >= 0, got {self.consecutive_failures}"
            )
        if self.faults and self.consecutive_failures < 1:
            raise ValueError(
                "a tick with faults is a FAILED tick and must count at least "
                "itself in consecutive_failures"
            )
        if not self.faults and self.consecutive_failures:
            raise ValueError(
                "a tick with no faults is not a failure; consecutive_failures must be 0"
            )

    @property
    def failed_tick(self) -> bool:
        """True when this tick swallowed at least one exception."""
        return bool(self.faults)

    @property
    def status(self) -> str:
        """``failed`` | ``worked`` | ``idle`` — the tick's verdict in one word.

        ``idle`` is claimed ONLY when the tick actually determined there was
        nothing to do. A tick that could not tell is ``failed``, never idle.
        """
        if self.faults:
            return "failed"
        if self.recorded or self.sent:
            return "worked"
        return "idle"

    @property
    def pending_text(self) -> str:
        """``pending`` rendered for humans: a count, or the UNKNOWN token."""
        return PENDING_UNKNOWN_TEXT if self.pending is None else str(self.pending)

    def level(self, *, escalate_after: int = DEFAULT_ESCALATE_AFTER) -> int:
        """The logging level this tick deserves — LOUDER as failures pile up.

        Healthy → ``INFO`` (an idle daemon must stay quiet, or its alarms get
        ignored). Failing → ``WARNING``, then ``ERROR`` once the streak reaches
        ``escalate_after``. ``escalate_after <= 0`` escalates immediately.
        """
        if not self.faults:
            return logging.INFO
        if self.consecutive_failures >= max(escalate_after, 1):
            return logging.ERROR
        return logging.WARNING

    def line(self) -> str:
        """The one line the daemon emits for this tick.

        Healthy ticks keep the long-standing wording so existing eyes and
        greps still work; a failing tick prepends ``FAILED`` and appends the
        streak, its duration and the underlying reason.
        """
        head = (
            f"notifyd tick {self.tick}: {self.status.upper()} "
            f"pending={self.pending_text} sent={self.sent} failed={self.failed} "
            f"skipped={self.skipped} failed_terminal={self.failed_terminal} "
            f"({self.recorded} recorded)"
        )
        if not self.faults:
            return head
        parts = [head, f"consecutive_failures={self.consecutive_failures}"]
        if self.failing_for_seconds is not None:
            parts.append(f"failing_for={_humanize_seconds(self.failing_for_seconds)}")
        parts.append(f"faults={len(self.faults)}")
        parts.append(f"reason={self.faults[0]}")
        if len(self.faults) > 1:
            parts.append(f"also={'; '.join(self.faults[1:])}")
        return " ".join(parts)


def build_report(
    *,
    tick: int,
    summary: dict,
    faults: "list[str] | tuple[str, ...]",
    consecutive_failures: int,
    failing_since: _dt.datetime | None,
    now: _dt.datetime,
) -> TickReport:
    """Assemble the tick's :class:`TickReport` from the delivery-pass summary.

    ``summary`` is whatever :func:`scitex_cards._delivery.deliver_pending`
    returned — possibly ``{}`` when the pass itself raised, in which case every
    tally is 0 and ``pending`` is UNKNOWN (we did not get to look).
    """
    faults = tuple(faults)
    pending = summary.get("pending", None if faults else 0)
    failing_for = None
    if faults and failing_since is not None:
        failing_for = max((now - failing_since).total_seconds(), 0.0)
    return TickReport(
        tick=tick,
        pending=pending,
        sent=int(summary.get("sent", 0)),
        failed=int(summary.get("failed", 0)),
        skipped=int(summary.get("skipped", 0)),
        failed_terminal=int(summary.get("failed_terminal", 0)),
        recorded=len(summary.get("outcomes", ())),
        faults=faults,
        consecutive_failures=consecutive_failures if faults else 0,
        failing_for_seconds=failing_for,
    )


__all__ = [
    "DEFAULT_ESCALATE_AFTER",
    "PENDING_UNKNOWN_TEXT",
    "TickReport",
    "build_report",
    "fault_text",
]

# EOF
