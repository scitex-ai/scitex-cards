#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standing TERMINAL comm-misses — the throttled re-surfacing scan.

A notification whose retry budget is exhausted is a permanent comm-miss: it
was never delivered and never will be without operator action. The delivery
loop surfaces each one loudly exactly ONCE, when it first turns terminal — and
that stderr line then scrolls away. This module owns the periodic re-scan that
keeps the standing set visible without spamming every tick.

Extracted from :mod:`scitex_cards._delivery._daemon` (which was at its file
budget) along the responsibility seam the daemon docstring already names:

* ``_daemon``  = the LOOP (single-instance lock, heartbeat, signals, ticks).
* ``_terminal`` = "what did we give up on, and is the operator being told?"

THE IMPORT SURFACE DOES NOT MOVE: ``_daemon`` re-exports
:func:`report_terminal_misses`, so every existing
``from scitex_cards._delivery._daemon import report_terminal_misses`` keeps
resolving to this same object. A split that breaks its callers is a rename
with extra steps.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ._ledger import _KEY_SEP, TERMINAL_STATUS, Ledger

logger = logging.getLogger("scitex_cards.delivery.notifyd")

#: Default cadence (in ticks) for the throttled terminal-miss re-report.
DEFAULT_TERMINAL_REPORT_EVERY = 10


def report_terminal_misses(store: str | Path | None = None) -> list[dict]:
    """Scan the ledger for ALL current ``failed_terminal`` entries.

    A terminal entry is a permanent comm-miss: the retry budget was exhausted
    and the notification was never delivered. The loop surfaces each one LOUDLY
    exactly once when it first turns terminal, but that stderr line scrolls
    past. This scan lets the daemon re-surface the standing set periodically so
    a long-undeliverable user is never silently forgotten.

    Returns a list of ``{recipient, note_id, channel, attempts, last_ts,
    detail}`` dicts, one per outstanding terminal entry, sorted for stable
    output. Reads the ledger fresh off disk (it is the sole delivery truth).
    """
    ledger = Ledger.load(store)
    out: list[dict] = []
    for key, entry in ledger._entries.items():  # noqa: SLF001 — same package.
        if entry.get("status") != TERMINAL_STATUS:
            continue
        parts = key.split(_KEY_SEP)
        if len(parts) != 3:
            # Defensive: a hand-mangled key — surface it rather than drop it.
            recipient, note_id, channel = key, "", ""
        else:
            recipient, note_id, channel = parts
        out.append(
            {
                "recipient": recipient,
                "note_id": note_id,
                "channel": channel,
                "attempts": int(entry.get("attempts", 0)),
                "last_ts": entry.get("last_ts"),
                "detail": entry.get("detail"),
            }
        )
    out.sort(key=lambda d: (d["recipient"], d["note_id"], d["channel"]))
    return out


def report_terminal_if_due(*, tick: int, every: int, store) -> None:
    """Every ``every`` ticks, re-surface the standing terminal comm-misses.

    THROTTLED on purpose: re-surfacing every tick would bury the live per-tick
    summaries and train the operator to ignore the warning. Re-surfacing on a
    cadence keeps a long-undeliverable user visible without spam.
    """
    if every <= 0:
        return
    if tick % every != 0:
        return
    misses = report_terminal_misses(store)
    if not misses:
        return
    logger.warning(
        "notifyd: %d OUTSTANDING terminal comm-miss(es) still undelivered "
        "(re-surfaced every %d ticks) — operator must fix the channel/address:",
        len(misses),
        every,
    )
    for m in misses:
        logger.warning(
            "  comm-miss: %s note=%s via %s (attempts=%d, last=%s, detail=%s)",
            m["recipient"],
            m["note_id"],
            m["channel"],
            m["attempts"],
            m["last_ts"],
            m["detail"],
        )


__all__ = [
    "DEFAULT_TERMINAL_REPORT_EVERY",
    "report_terminal_if_due",
    "report_terminal_misses",
]

# EOF
