#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sweep thresholds — *how long is too long*, and the env knobs that move it.

Extracted from :mod:`scitex_cards._stale_active` (which re-exports every public
name here, so no importer moves). One responsibility: turn an optional explicit
argument, an environment variable, and a default into a number of hours.

The three clocks are deliberately NOT the same length, and the asymmetry is the
whole design:

* **stale-active, 2 h** — asks "why have you abandoned this?" The owner can act,
  so a tight clock is a real signal.
* **blocked-check, 24 h** — asks "has your blocker cleared?" The owner is
  legitimately waiting and cannot advance the card, so nudging them on the tight
  clock is 12 identical messages a day about work they are powerless to move.
  That is not a signal, it is training to ignore the channel.
* **backlog, 24 h** — asks "start or triage this." Consciously-not-begun work
  only becomes worth a nudge after a full day of no triage.

Pure: every read is at CALL time, so a test or a cron can flip a knob
per-invocation, and a non-numeric value degrades to the default rather than
raising into the sweep.
"""

from __future__ import annotations

import os

__all__ = [
    "ENV_STALE_ACTIVE_HOURS",
    "DEFAULT_STALE_ACTIVE_HOURS",
    "ENV_BLOCKED_NUDGE_HOURS",
    "DEFAULT_BLOCKED_NUDGE_HOURS",
    "ENV_BACKLOG_NUDGE_HOURS",
    "ENV_PENDING_NUDGE_HOURS",
    "DEFAULT_BACKLOG_NUDGE_HOURS",
    "DEFAULT_PENDING_NUDGE_HOURS",
    "_resolve_hours",
    "_stale_active_hours",
    "_pending_nudge_hours",
    "_blocked_nudge_hours",
]

#: Env override + default for the staleness threshold (hours). 2 h is
#: tight on purpose: an in_progress/blocked card untouched for >2 h is
#: very likely forgotten, not mid-keystroke.
ENV_STALE_ACTIVE_HOURS = "SCITEX_TODO_STALE_ACTIVE_HOURS"
DEFAULT_STALE_ACTIVE_HOURS = 2.0

#: Env override + default for the EXTERNALLY-BLOCKED re-check (hours).
#:
#: Deliberately as lenient as the backlog clock: the owner is legitimately
#: waiting, so the only thing worth asking is a periodic "is your blocker
#: still real?" — blockers DO go stale silently (the dependency shipped, the
#: compute job died, the operator answered elsewhere), and a card can rot for
#: weeks behind a blocker that cleared long ago. A daily check catches that
#: rot without the alert fatigue of the 2 h clock.
ENV_BLOCKED_NUDGE_HOURS = "SCITEX_TODO_BLOCKED_NUDGE_HOURS"
DEFAULT_BLOCKED_NUDGE_HOURS = 24.0

#: Env override + default for the BACKLOG threshold (hours). 24 h is
#: deliberately MUCH more lenient than the 2 h stale-active clock: a deferred
#: card is work the owner consciously has not begun, so a forgotten one only
#: becomes worth a nudge after a full day of no triage / no start.
ENV_BACKLOG_NUDGE_HOURS = "SCITEX_TODO_BACKLOG_NUDGE_HOURS"
#: Deprecated alias for the env knob. Both names are honoured (see
#: :func:`_pending_nudge_hours`) so existing crontabs keep working.
ENV_PENDING_NUDGE_HOURS = "SCITEX_TODO_PENDING_NUDGE_HOURS"
DEFAULT_PENDING_NUDGE_HOURS = 24.0
DEFAULT_BACKLOG_NUDGE_HOURS = DEFAULT_PENDING_NUDGE_HOURS


def _resolve_hours(explicit: float | None, env_name: str, default: float) -> float:
    """Resolve a threshold: explicit arg > env override > default.

    The env override is read at CALL time (not import time) so a test or
    cron can flip it per-invocation. A non-numeric env value falls back
    to the default rather than raising into the sweep.
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _stale_active_hours(stale_hours: float | None) -> float:
    """Resolve the stale-active threshold, honoring the env override."""
    return _resolve_hours(
        stale_hours, ENV_STALE_ACTIVE_HOURS, DEFAULT_STALE_ACTIVE_HOURS
    )


def _pending_nudge_hours(pending_hours: float | None) -> float:
    """Resolve the backlog threshold, honoring either env override.

    ``SCITEX_TODO_BACKLOG_NUDGE_HOURS`` is the current name;
    ``SCITEX_TODO_PENDING_NUDGE_HOURS`` still works so live crontabs written
    against the old name do not silently revert to the 24 h default.
    """
    if pending_hours is not None:
        return pending_hours
    if os.environ.get(ENV_BACKLOG_NUDGE_HOURS) is not None:
        return _resolve_hours(
            None, ENV_BACKLOG_NUDGE_HOURS, DEFAULT_BACKLOG_NUDGE_HOURS
        )
    return _resolve_hours(None, ENV_PENDING_NUDGE_HOURS, DEFAULT_BACKLOG_NUDGE_HOURS)


def _blocked_nudge_hours(blocked_hours: float | None) -> float:
    """Resolve the externally-blocked re-check threshold (env-overridable)."""
    return _resolve_hours(
        blocked_hours, ENV_BLOCKED_NUDGE_HOURS, DEFAULT_BLOCKED_NUDGE_HOURS
    )


# EOF
