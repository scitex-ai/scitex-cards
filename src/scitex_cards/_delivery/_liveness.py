#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Does the delivery daemon TELL THE TRUTH about its own ticks?

Why this module exists (the 2026-07-28 silent outage)
-----------------------------------------------------
notifyd could not read the store on EVERY tick for roughly a day. The store
read raised inside :func:`scitex_cards._delivery._sweeps._run_reminder_sweep`,
whose guard logged the traceback and moved on — but the guard did not COUNT
anything, so the only line anyone reads stayed::

    notifyd tick 1196: sent=0 failed=0 skipped=0 failed_terminal=0 (0 recorded)

That is bit-for-bit what a HEALTHY IDLE daemon prints. The operator's DMs never
reached the agent, and the log said everything was fine. The store bug was
fixed separately; **the defect this module fixes is the COUNTER**, because the
next delivery outage will have a different cause and must not be silent.

The contract (constitution §2 — "answer in a fixed, declared shape")
-------------------------------------------------------------------
* :class:`TickHealth` is the ONE shape a tick reports, with every signal as its
  own named field and a validator that rejects a malformed answer where it is
  built rather than three layers downstream.
* :class:`TickState` is THREE-VALUED, not two. ``IDLE`` ("nothing was pending")
  and ``FAILED`` ("could not determine what was pending") are DIFFERENT states
  and are distinguishable in the emitted line — ``pending=0`` versus
  ``pending=unknown``. Collapsing unknown into either pole is named in the
  constitution as the most common bug we ship, and is exactly what happened.
* :class:`DeliveryLiveness` makes consecutive failures ESCALATE: past
  :data:`ESCALATE_AFTER_FAILURES` the loop logs at ERROR with the count, the
  underlying reason, and HOW LONG it has been failing. A daemon that has
  delivered nothing for an hour screams; an idle one stays quiet, because an
  alarm that fires on healthy idleness is an alarm everyone learns to ignore.
* The tracker also persists a small JSON record next to the pidfile so
  ``scitex-cards health`` can answer "when did delivery last work?" without
  reading a log.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("scitex_cards.delivery.notifyd")

#: Consecutive failing ticks after which the loop escalates to ERROR. Three
#: ticks at the default 120 s interval is ~6 minutes of total delivery silence.
ESCALATE_AFTER_FAILURES = 3

#: Filename of the delivery-liveness record, a sibling of the notifyd pidfile.
LIVENESS_NAME = "notifyd-liveness.json"

_OUTAGE_HINT = (
    "notifyd is ticking but delivery is FAILING — read the reason above, then "
    "on the host that runs it: `journalctl --user -u scitex-todo-notifyd -n 50` "
    "and `scitex-cards health` (a store the daemon cannot read is the usual "
    "cause; `scitex-cards db path` shows what it resolves)"
)

_NO_RECORD_HINT = (
    "no delivery-liveness record yet — either notifyd has not completed a tick "
    "since it started, or it predates this record (restart it: `systemctl "
    "--user restart scitex-todo-notifyd`)"
)


class TickState(str, Enum):
    """What ONE notifyd tick learned. Three-valued on purpose.

    ``ACTIVE``  the tick determined what was pending and acted on at least one
                item (a ledger write happened: sent / failed / skipped).
    ``IDLE``    the tick determined that NOTHING was pending. Healthy + quiet.
    ``FAILED``  the tick could NOT determine what was pending — a store/inbox
                read raised. ``sent=0`` here is ignorance, not idleness.
    """

    ACTIVE = "active"
    IDLE = "idle"
    FAILED = "failed"


@dataclass(frozen=True)
class SweepOutcome:
    """The fixed answer a guarded in-tick sweep returns — never a bare ``None``.

    A sweep that swallows its exception must still SAY it failed, or the tick
    that contains it reports zero work and looks healthy. ``faults`` renders the
    reason for the tick line.
    """

    name: str
    ok: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("SweepOutcome.name must be non-empty")
        if self.ok and self.reason:
            raise ValueError(
                f"SweepOutcome({self.name!r}) is ok yet carries a reason "
                f"{self.reason!r} — an ok answer has nothing to explain"
            )
        if not self.ok and not self.reason:
            raise ValueError(
                f"SweepOutcome({self.name!r}) failed without a reason — a "
                "failure with no stated cause is the silence this module exists "
                "to remove"
            )

    @property
    def faults(self) -> tuple[str, ...]:
        """``()`` when ok, else a one-element ``("<name>: <reason>",)``."""
        return () if self.ok else (f"{self.name}: {self.reason}",)

    @classmethod
    def success(cls, name: str) -> "SweepOutcome":
        return cls(name=name, ok=True)

    @classmethod
    def failure(cls, name: str, exc: BaseException) -> "SweepOutcome":
        return cls(name=name, ok=False, reason=f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True)
class TickHealth:
    """The fixed, validated shape ONE tick reports.

    ``pending`` is the three-valued signal: an int when the tick could count
    what delivery still owed, and ``None`` when it could NOT — never ``0`` as a
    stand-in for "I don't know".
    """

    state: TickState
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    failed_terminal: int = 0
    recorded: int = 0
    pending: int | None = None
    faults: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.state, TickState):
            raise ValueError(
                f"TickHealth.state must be a TickState, got {self.state!r}"
            )
        for name in ("sent", "failed", "skipped", "failed_terminal", "recorded"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"TickHealth.{name} must be a non-negative int")
        failing = self.state is TickState.FAILED
        if failing != bool(self.faults):
            raise ValueError(
                "TickHealth: state=FAILED and a non-empty faults tuple must "
                f"agree (state={self.state.value}, faults={self.faults!r})"
            )
        if failing != (self.pending is None):
            raise ValueError(
                "TickHealth: a FAILED tick cannot know what is pending and a "
                f"non-failed tick must (state={self.state.value}, "
                f"pending={self.pending!r})"
            )
        if not failing and self.pending is not None and self.pending < 0:
            raise ValueError("TickHealth.pending must be non-negative")
        if self.state is TickState.IDLE and self.recorded:
            raise ValueError("TickHealth: an IDLE tick recorded no ledger writes")

    @property
    def pending_text(self) -> str:
        """``"unknown"`` when the tick could not tell, else the count."""
        return "unknown" if self.pending is None else str(self.pending)

    @property
    def delivered(self) -> bool:
        """True when this tick actually pushed at least one notification out."""
        return self.sent > 0


def tick_health(summary: dict | None, *, faults) -> TickHealth:
    """Build the tick's answer from a delivery summary + the tick's faults.

    ``summary is None`` means the delivery pass never returned (it raised), and
    a fault MUST have been recorded for it — the validator enforces that pair.
    """
    faults = tuple(faults)
    data = summary or {}
    faults += tuple(data.get("faults") or ())
    counts = {
        name: int(data.get(name, 0) or 0)
        for name in ("sent", "failed", "skipped", "failed_terminal")
    }
    recorded = len(data.get("outcomes") or ())
    if faults:
        return TickHealth(
            state=TickState.FAILED,
            recorded=recorded,
            pending=None,
            faults=faults,
            **counts,
        )
    pending = data.get("pending")
    pending = 0 if pending is None else int(pending)
    state = TickState.ACTIVE if recorded else TickState.IDLE
    return TickHealth(state=state, recorded=recorded, pending=pending, **counts)


def liveness_path(store: str | Path | None = None) -> Path:
    """``<store_dir>/runtime/notifyd-liveness.json`` — beside the pidfile."""
    from .._paths import runtime_dir

    return runtime_dir(store) / LIVENESS_NAME


@dataclass
class DeliveryLiveness:
    """Rolling delivery liveness across ticks — the thing that ESCALATES.

    One instance lives for the daemon's run. :meth:`record` is the single call
    the loop makes per tick: it updates the counters, emits the tick line at a
    level that gets LOUDER the longer delivery has been broken, and persists a
    snapshot for ``scitex-cards health``.
    """

    escalate_after: int = ESCALATE_AFTER_FAILURES
    consecutive_failures: int = 0
    failing_since: _dt.datetime | None = None
    last_healthy_at: _dt.datetime | None = None
    last_delivery_at: _dt.datetime | None = None
    last_fault: str | None = None
    last_state: str | None = None
    active_ticks: int = 0
    idle_ticks: int = 0
    failed_ticks: int = 0
    updated_at: _dt.datetime | None = None
    _escalating: bool = field(default=False, repr=False)

    @property
    def escalating(self) -> bool:
        """True once the consecutive-failure threshold has been crossed."""
        return self._escalating

    def failing_for(self, now: _dt.datetime) -> _dt.timedelta | None:
        """How long delivery has been failing, or ``None`` when it is not."""
        if self.failing_since is None:
            return None
        return now - self.failing_since

    def observe(self, health: TickHealth, *, now: _dt.datetime) -> None:
        """Fold ONE tick into the rolling state (no logging, no IO)."""
        self.updated_at = now
        self.last_state = health.state.value
        if health.state is TickState.FAILED:
            self.failed_ticks += 1
            self.consecutive_failures += 1
            if self.failing_since is None:
                self.failing_since = now
            self.last_fault = health.faults[0] if health.faults else None
            self._escalating = self.consecutive_failures >= self.escalate_after
            return
        if health.state is TickState.ACTIVE:
            self.active_ticks += 1
        else:
            self.idle_ticks += 1
        if health.delivered:
            self.last_delivery_at = now
        self.last_healthy_at = now
        self.consecutive_failures = 0
        self.failing_since = None
        self.last_fault = None
        self._escalating = False

    def snapshot(self) -> dict[str, Any]:
        """The persisted / returned record — plain JSON-safe values."""

        def _stamp(value: _dt.datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        return {
            "state": self.last_state,
            "consecutive_failures": self.consecutive_failures,
            "escalating": self._escalating,
            "failing_since": _stamp(self.failing_since),
            "last_healthy_at": _stamp(self.last_healthy_at),
            "last_delivery_at": _stamp(self.last_delivery_at),
            "last_fault": self.last_fault,
            "active_ticks": self.active_ticks,
            "idle_ticks": self.idle_ticks,
            "failed_ticks": self.failed_ticks,
            "updated_at": _stamp(self.updated_at),
        }

    def persist(self, path: Path | None) -> None:
        """Write the snapshot for ``health`` to read. Best-effort, never silent."""
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.snapshot(), indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "notifyd could not write the delivery-liveness record %s (%s: %s)",
                path,
                type(exc).__name__,
                exc,
            )

    def record(
        self,
        *,
        tick: int,
        health: TickHealth,
        now: _dt.datetime,
        path: Path | None = None,
    ) -> TickHealth:
        """Observe + LOG + persist one tick. The loop's single per-tick call."""
        self.observe(health, now=now)
        self._log_tick(tick, health)
        if self._escalating:
            self._log_escalation(now)
        self.persist(path)
        return health

    def _log_tick(self, tick: int, health: TickHealth) -> None:
        """The per-tick line. Quiet when healthy; a WARNING when ignorant."""
        line = (
            "notifyd tick %d: state=%s pending=%s sent=%d failed=%d skipped=%d "
            "failed_terminal=%d (%d recorded)"
        )
        args = (
            tick,
            health.state.value,
            health.pending_text,
            health.sent,
            health.failed,
            health.skipped,
            health.failed_terminal,
            health.recorded,
        )
        if health.state is not TickState.FAILED:
            logger.info(line, *args)
            return
        logger.warning(
            line + " consecutive_failures=%d fault=%s",
            *args,
            self.consecutive_failures,
            self.last_fault,
        )

    def _log_escalation(self, now: _dt.datetime) -> None:
        """The louder-not-quieter ERROR, repeated every tick while broken."""
        elapsed = self.failing_for(now)
        last_ok = (
            self.last_delivery_at.isoformat()
            if self.last_delivery_at is not None
            else "NEVER since this daemon started"
        )
        logger.error(
            "notifyd DELIVERY OUTAGE: %d consecutive failing tick(s) over %s "
            "(failing since %s); last successful delivery %s; reason: %s",
            self.consecutive_failures,
            elapsed if elapsed is not None else "an unknown period",
            self.failing_since.isoformat() if self.failing_since else "unknown",
            last_ok,
            self.last_fault,
        )


def read_liveness(path: Path) -> dict[str, Any] | None:
    """Load the persisted record, or ``None`` when it is absent/unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def assess_delivery_liveness(
    store: str | Path | None = None,
    *,
    escalate_after: int = ESCALATE_AFTER_FAILURES,
) -> dict[str, Any]:
    """The ``health`` verdict on DELIVERY (not on the daemon's mere existence).

    ``notifyd_alive`` answers "is the process ticking?". This answers "are the
    ticks doing anything?" — the exact question that had no answer while the
    daemon ticked 1196 times without reading the store. Returns the standard
    ``{ok, state, detail, hint}`` record.
    """
    record = read_liveness(liveness_path(store))
    if record is None:
        return {
            "ok": True,
            "state": "unknown",
            "detail": "no notifyd delivery-liveness record found",
            "hint": _NO_RECORD_HINT,
        }
    failures = int(record.get("consecutive_failures") or 0)
    last_delivery = record.get("last_delivery_at") or "never"
    tail = (
        f"last successful delivery {last_delivery}; "
        f"last tick {record.get('updated_at')} (state={record.get('state')})"
    )
    if failures >= escalate_after:
        return {
            "ok": False,
            "state": "failing",
            "detail": (
                f"notifyd DELIVERY OUTAGE: {failures} consecutive failing ticks "
                f"since {record.get('failing_since')}; reason: "
                f"{record.get('last_fault')}; {tail}"
            ),
            "hint": _OUTAGE_HINT,
        }
    if failures:
        return {
            "ok": True,
            "state": "degraded",
            "detail": (
                f"notifyd delivery degraded: {failures} consecutive failing "
                f"tick(s) (escalates at {escalate_after}); reason: "
                f"{record.get('last_fault')}; {tail}"
            ),
            "hint": _OUTAGE_HINT,
        }
    return {
        "ok": True,
        "state": "delivering",
        "detail": f"notifyd delivery healthy: {tail}",
        "hint": None,
    }


__all__ = [
    "ESCALATE_AFTER_FAILURES",
    "LIVENESS_NAME",
    "DeliveryLiveness",
    "SweepOutcome",
    "TickHealth",
    "TickState",
    "assess_delivery_liveness",
    "liveness_path",
    "read_liveness",
    "tick_health",
]

# EOF
