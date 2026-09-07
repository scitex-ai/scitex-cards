#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""How often an owner's digest may re-fire.

Extracted from :mod:`scitex_cards._reminders` (which is at its size budget) —
and it earns its own module, because the cadence stopped being a one-liner the
moment we admitted the digest carries TWO sweeps with different clocks.

THE BUG THIS FIXES (2026-07-12): the digest merges ``detect_stale_active``
(actionable cards, a 2 h question) with ``detect_pending_backlog`` (deferred
cards, a 24 h question) into one bucket, then took a single ``min()`` of the
per-card intervals across BOTH. Since :data:`DEFAULT_INTERVAL_MINUTES` is 5,
an owner holding one actionable card was re-sent a digest of their ENTIRE
backlog every five minutes. sac received digests #649/#650/#651 within twelve
minutes, each listing 97 cards of which ~86 were deliberately deferred.

That is not a reminder, it is an interrupt loop — and it is the same wrong as
charging a deferred card against an agent's WIP: it punishes the owner for the
honesty of parking something, and it trains them to ignore the one channel the
fleet is supposed to trust. A parked card must not be able to drag anything
onto a faster clock.

The detectors already encode the right answer in their own thresholds. This
module just stops the merge from throwing it away.
"""

from __future__ import annotations

#: Re-nudge interval (minutes) for an owner whose ONLY stale cards are backlog
#: (deferred) ones. Nothing is in flight, so ride the backlog sweep's own 24 h
#: clock rather than the 5-minute active one. Config may override.
DEFAULT_BACKLOG_INTERVAL_MINUTES = 24 * 60.0

#: Config key for the above (under the ``reminders`` section).
CFG_BACKLOG_INTERVAL = "backlog_interval_minutes"


def backlog_interval_minutes(cfg: dict | None = None) -> float:
    """Interval for a backlog-only owner. Config wins; else 24 h."""
    if isinstance(cfg, dict):
        raw = cfg.get(CFG_BACKLOG_INTERVAL)
        try:
            val = float(raw)  # type: ignore[arg-type]
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    return DEFAULT_BACKLOG_INTERVAL_MINUTES


def resolve_owner_interval(
    cards,
    *,
    backlog_ids,
    by_id,
    cfg,
    resolve_interval_minutes,
    forced=None,
) -> float:
    """Return the re-digest interval (minutes) for one owner.

    ``forced`` (the caller's flat ``interval_minutes`` arg) wins outright.
    Otherwise the cadence is the TIGHTEST interval any ACTIVE card asks for —
    backlog cards are excluded from that ``min()``, so a parked card can never
    pull the digest onto a faster clock. An owner with ONLY backlog cards rides
    :func:`backlog_interval_minutes`.
    """
    if forced is not None:
        return forced
    active = [sc for sc in cards if sc.id not in backlog_ids]
    if active:
        return min(
            resolve_interval_minutes(by_id.get(sc.id), cfg) for sc in active
        )
    return backlog_interval_minutes(cfg)


__all__ = [
    "CFG_BACKLOG_INTERVAL",
    "DEFAULT_BACKLOG_INTERVAL_MINUTES",
    "backlog_interval_minutes",
    "resolve_owner_interval",
]

# EOF
