#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Delivery LIVENESS — "is anything actually being delivered?", persisted.

Distinct from :mod:`scitex_cards._delivery._pidfile`, and deliberately so:

* ``_pidfile`` answers **is the PROCESS alive?** (a heartbeat stamp).
* this module answers **is DELIVERY working?** (last successful delivery,
  consecutive failing ticks, the underlying reason).

On 2026-07-28/29 the first was GREEN for a whole day while the second was
catastrophically false: notifyd ticked happily every 120 s, stamping fresh
heartbeats, while every tick failed to read the store and delivered nothing.
A liveness signal that only proves the loop is spinning is not a liveness
signal for the thing the loop exists to do.

The record lives at ``<store_dir>/runtime/notifyd-liveness.json`` — beside the
pidfile and the delivery ledger, under whichever scope the store resolved to —
so a DIFFERENT process (``scitex-cards health``, run inside an agent container
that shares the store by bind-mount) can read it. An in-memory counter would be
invisible exactly where a human looks.

Every read is THREE-VALUED (constitution §2). No record at all is
``unknown`` — notifyd may simply never have run here — and is never reported
as "failing": inventing a failure from an absent measurement is the same class
of lie as reporting ``pending=0`` when the store would not open.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ._tick import DEFAULT_ESCALATE_AFTER, _humanize_seconds

#: Filename of the delivery-liveness record inside the store's ``runtime/`` dir.
LIVENESS_FILENAME = "notifyd-liveness.json"

_RESTART_HINT = (
    "delivery is failing — read the underlying reason above, then on the host "
    "that runs notifyd: `journalctl --user -u scitex-todo-notifyd -n 200` and "
    "`systemctl --user restart scitex-todo-notifyd`. `scitex-cards deliver` "
    "runs ONE pass in the foreground to reproduce the fault directly."
)


def liveness_path(store: str | Path | None = None) -> Path:
    """Resolve ``<store_dir>/runtime/notifyd-liveness.json``."""
    from .._paths import runtime_dir

    return runtime_dir(store) / LIVENESS_FILENAME


def _parse_stamp(raw: object) -> _dt.datetime | None:
    """Parse an ISO stamp, returning ``None`` rather than raising."""
    if not raw:
        return None
    try:
        stamp = _dt.datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return stamp.replace(tzinfo=_dt.timezone.utc) if stamp.tzinfo is None else stamp


def _iso(stamp: _dt.datetime | None) -> str | None:
    return stamp.isoformat() if stamp is not None else None


@dataclass(frozen=True)
class DeliveryLiveness:
    """The daemon's delivery-truth across ticks, in one fixed shape.

    Parameters
    ----------
    last_ok_at : datetime | None
        End of the last tick that swallowed NO exception. ``None`` = never (or
        not since this record was created).
    last_delivery_at : datetime | None
        End of the last tick that actually SENT at least one notification. A
        daemon with nothing to send is healthy, so this may lag ``last_ok_at``
        indefinitely without being a fault.
    consecutive_failures : int
        Failing ticks in a row, current streak.
    failing_since : datetime | None
        When the current streak began. ``None`` when not failing.
    last_fault : str | None
        The most recent swallowed exception, already rendered.
    updated_at : datetime | None
        When this record was last written (any tick, healthy or not).
    """

    last_ok_at: _dt.datetime | None = None
    last_delivery_at: _dt.datetime | None = None
    consecutive_failures: int = 0
    failing_since: _dt.datetime | None = None
    last_fault: str | None = None
    updated_at: _dt.datetime | None = None

    def observe(
        self,
        *,
        faults: "list[str] | tuple[str, ...]",
        sent: int,
        now: _dt.datetime,
    ) -> "DeliveryLiveness":
        """Fold ONE tick's outcome in; returns the NEW state (never mutates).

        A tick with faults extends the failing streak (starting it if needed);
        a clean tick ends the streak and refreshes ``last_ok_at``. A tick that
        sent something also refreshes ``last_delivery_at``.
        """
        if faults:
            return replace(
                self,
                consecutive_failures=self.consecutive_failures + 1,
                failing_since=self.failing_since or now,
                last_fault=faults[0],
                updated_at=now,
            )
        return replace(
            self,
            last_ok_at=now,
            last_delivery_at=now if sent else self.last_delivery_at,
            consecutive_failures=0,
            failing_since=None,
            updated_at=now,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the on-disk JSON shape."""
        return {
            "last_ok_at": _iso(self.last_ok_at),
            "last_delivery_at": _iso(self.last_delivery_at),
            "consecutive_failures": self.consecutive_failures,
            "failing_since": _iso(self.failing_since),
            "last_fault": self.last_fault,
            "updated_at": _iso(self.updated_at),
        }

    @classmethod
    def from_dict(cls, data: object) -> "DeliveryLiveness":
        """Rebuild from the on-disk shape; a malformed record degrades to empty."""
        if not isinstance(data, dict):
            return cls()
        try:
            failures = int(data.get("consecutive_failures", 0) or 0)
        except (TypeError, ValueError):
            failures = 0
        fault = data.get("last_fault")
        return cls(
            last_ok_at=_parse_stamp(data.get("last_ok_at")),
            last_delivery_at=_parse_stamp(data.get("last_delivery_at")),
            consecutive_failures=max(failures, 0),
            failing_since=_parse_stamp(data.get("failing_since")),
            last_fault=str(fault) if fault else None,
            updated_at=_parse_stamp(data.get("updated_at")),
        )


def read_liveness(store: str | Path | None = None) -> DeliveryLiveness:
    """Read the persisted record; an absent/unreadable one yields an EMPTY state.

    An empty state is honestly "nothing measured yet" — every stamp ``None``
    and zero failures — which :func:`assess_delivery` reports as ``unknown``,
    not as healthy and not as failing.
    """
    path = liveness_path(store)
    if not path.exists():
        return DeliveryLiveness()
    try:
        return DeliveryLiveness.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return DeliveryLiveness()


def write_liveness(
    state: DeliveryLiveness, store: str | Path | None = None
) -> Path | None:
    """Persist ``state`` atomically; returns the path, or ``None`` on failure.

    Best-effort by design: this is the OBSERVABILITY record, and failing to
    write it must never cost the tick its delivery. The caller counts the
    failure as a fault, so a write outage still shows up in the emitted line
    rather than disappearing.
    """
    path = liveness_path(store)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        return None
    return path


def assess_delivery(
    store: str | Path | None = None,
    *,
    now: _dt.datetime | None = None,
    escalate_after: int = DEFAULT_ESCALATE_AFTER,
) -> dict[str, Any]:
    """The health-check verdict on DELIVERY (not on the process being alive).

    Returns the standard ``{ok, state, detail, hint}`` record where ``state``
    is three-valued: ``delivering`` / ``failing`` / ``unknown``. ``unknown``
    (no record on disk) is reported ``ok=True`` with an explicit detail — the
    ``notifyd_alive`` check already owns "is the daemon running at all", and
    manufacturing a second failure out of an absent measurement would double-
    alarm on a fact we did not observe.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    state = read_liveness(store)
    if state.updated_at is None:
        return {
            "ok": True,
            "state": "unknown",
            "detail": (
                f"no delivery-liveness record at {liveness_path(store)} — notifyd "
                "has not completed a tick here (or predates this record)"
            ),
            "hint": None,
        }
    last_ok = _iso(state.last_ok_at) or "never"
    last_delivery = _iso(state.last_delivery_at) or "never"
    if state.consecutive_failures <= 0:
        return {
            "ok": True,
            "state": "delivering",
            "detail": (
                f"delivery healthy: last ok tick {last_ok}, last successful "
                f"delivery {last_delivery}, 0 consecutive failures"
            ),
            "hint": None,
        }
    failing_for = (
        _humanize_seconds((now - state.failing_since).total_seconds())
        if state.failing_since is not None
        else "unknown"
    )
    return {
        "ok": state.consecutive_failures < max(escalate_after, 1),
        "state": "failing",
        "detail": (
            f"delivery FAILING: {state.consecutive_failures} consecutive failing "
            f"tick(s) for {failing_for}; last ok tick {last_ok}, last successful "
            f"delivery {last_delivery}; reason={state.last_fault}"
        ),
        "hint": _RESTART_HINT,
    }


__all__ = [
    "LIVENESS_FILENAME",
    "DeliveryLiveness",
    "assess_delivery",
    "liveness_path",
    "read_liveness",
    "write_liveness",
]

# EOF
