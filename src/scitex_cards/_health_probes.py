#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The probes `health()` runs in-process — identity, daemon, channel.

MOVED OUT OF ``_health`` (which hit the 512-line cap) and, more to the point,
moved to match the convention the rest of the directory already follows: every
other check lives in its own ``_health_<subject>`` module, and these five were
the last ones still defined inline. That inconsistency is what made ``_health``
the largest file in the package.

THE IMPORT SURFACE DOES NOT MOVE. ``_health`` re-exports all five, so
``from scitex_cards._health import _check_agent_id`` is the SAME object it
always was, defined next door — the identical rule already recorded there for
the ``_health_store`` and ``_health_cards`` splits.

Each probe returns the standard ``{ok, detail, hint}`` and may raise; the
caller wraps them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _inbox
from ._mcp_channel import recipient_keys, resolve_agent_id

#: The exact drain-stuck remediation (kept verbatim per the cross-package
#: spec). Lives HERE, beside its only consumer, and is re-exported by
#: ``_health`` — importing it the other way round would make this module
#: depend on the aggregator that imports it.
_DRAIN_HINT = (
    "channel not draining — ensure `scitex-cards mcp start` is running for this "
    "agent with SCITEX_CARDS_AGENT_ID set (needs >=0.7.32 where the poll loop no "
    "longer starves the handshake)"
)

#: For the UNKNOWN verdict: a backlog over threshold on an inbox that HAS been
#: drained at some point. Names the reason the evidence is inconclusive rather
#: than implying a fault, because either reading may be right.
_DRAIN_UNKNOWN_HINT = (
    "cannot tell whether the drain is alive: the backlog is over threshold but "
    "this inbox has drained before, and `seen` is lifetime-cumulative — it "
    "cannot distinguish 'draining now' from 'drained last week'. Check "
    "`delivery_liveness` for the last successful tick and `delivery_confirmed` "
    "for pushes that were never acknowledged; both measure the CURRENT rail."
)


def _check_agent_id(agent_id: str | None) -> dict[str, Any]:
    """Resolve the agent identity; fail on unset / 'unknown' / bare ``$VAR``."""
    try:
        resolved = resolve_agent_id(agent_id)
    except Exception as exc:  # noqa: BLE001 — unresolved id is a reportable state
        return {
            "ok": False,
            "detail": f"agent id unresolved ({exc})",
            "hint": (
                "set SCITEX_CARDS_AGENT_ID=<your-agent-id> (not blank / 'unknown'); "
                'in .mcp.json use the brace form "${SCITEX_CARDS_AGENT_ID}" — '
                "Claude Code does not expand bare $VAR"
            ),
        }
    return {"ok": True, "detail": f"agent id resolved: {resolved}", "hint": None}


def _check_notifyd_alive(store: str | Path | None) -> dict[str, Any]:
    """Check the notifyd delivery daemon via its pidfile — NAMESPACE-AGNOSTIC.

    The daemon stamps ``<store_dir>/runtime/notifyd.pid``, holds an flock for
    its lifetime, and REWRITES the file every tick (a heartbeat).

    The pid alone is not a portable liveness signal: notifyd runs on the bare
    host while fleet agents run in CONTAINERS that share the store by
    bind-mount, and **a pid is only meaningful inside the PID namespace that
    issued it**. Probing a foreign pid with ``os.kill`` raises
    ``ProcessLookupError`` and used to be reported as a stale pidfile — a
    permanent FALSE failure on a perfectly healthy daemon, which is worse than
    no check at all (it teaches the reader to ignore the channel).

    So: same namespace ⇒ probe the pid (sharpest signal, still fail-loud).
    Different namespace ⇒ judge by HEARTBEAT FRESHNESS and never by the pid.
    See :mod:`scitex_cards._delivery._pidfile` for the verdict logic.
    """
    from ._delivery._daemon import pidfile_path
    from ._delivery._pidfile import assess_liveness

    return assess_liveness(pidfile_path(store))


def _check_delivery_liveness(store: str | Path | None) -> dict[str, Any]:
    """Is anything actually being DELIVERED? (``notifyd_alive`` is not enough.)

    ``notifyd_alive`` above answers "is the process ticking" — and on
    2026-07-28/29 it was GREEN for a full day while every one of 1196
    consecutive ticks failed to read the store and delivered nothing. A
    heartbeat only proves the loop spins, not that the loop's work happens.

    This reads the daemon's persisted delivery record (last successful
    delivery, consecutive failing ticks, the underlying reason) and is
    THREE-VALUED: ``delivering`` / ``failing`` / ``unknown``. No record is
    ``unknown`` and stays ``ok`` — ``notifyd_alive`` already owns "not running
    at all", and manufacturing a second alarm from a measurement nobody took
    is the same lie as reporting zero pending when the store would not open.
    """
    from ._delivery._liveness import assess_delivery

    return assess_delivery(store)


def _confirmation_split(
    agent_id: str, store: str | Path | None
) -> tuple[int, int] | None:
    """``(confirmed, seen_but_unconfirmed)`` across this agent's inbox keys.

    COUNTED PER ROW, never derived as ``seen - confirmed``. The subtraction
    looks equivalent and is not: it silently reports a negative as a small
    positive if the two reads disagree, and a metric about trustworthiness
    that can lie about its own arithmetic is worse than no metric.

    ``None`` means the receipts could not be read at all — the caller must
    then say "not measured" rather than print a zero, which is the shape of
    good news.
    """
    from ._inbox_receipt import CONFIRMED_AT, receipts

    confirmed = 0
    seen_unconfirmed = 0
    try:
        for key in recipient_keys(agent_id, store=store):
            for row in receipts(key, store=store):
                if row.get(CONFIRMED_AT):
                    confirmed += 1
                elif row.get("seen"):
                    seen_unconfirmed += 1
    except Exception:  # noqa: BLE001 — a doctor must not raise on a read
        return None
    return confirmed, seen_unconfirmed


def _check_channel_drain(
    agent_id: str | None, store: str | Path | None, threshold: int
) -> dict[str, Any]:
    """Report unseen vs seen inbox counts for THIS agent; flag a stuck drain.

    THREE-VALUED, because ``seen`` cannot answer the question this check is
    named for. ``record_push`` marks a row seen at PUSH time, so ``seen > 0``
    latches true on an agent's first push and never goes back — treating it
    as a pass made this check unable to fail for the rest of that agent's
    life, however large the backlog grew. It is now an UNKNOWN: real evidence
    that something once drained, and no evidence about now.

    The detail splits ``seen`` into confirmed and seen-but-unconfirmed for the
    same reason. A flat ``seen=801`` reads as 801 delivered when it means 801
    cursors advanced; on a real agent 109 of those had never been acknowledged
    by anyone while this line still said the inbox was fine.
    """
    if not agent_id:
        return {
            "ok": True,
            "detail": "agent id unresolved — channel-drain check skipped",
            "hint": None,
        }
    keys = recipient_keys(agent_id, store=store)
    unseen = 0
    total = 0
    for key in keys:
        unseen += len(
            _inbox.poll_inbox(key, unseen_only=True, mark_seen=False, store=store)
        )
        total += len(
            _inbox.poll_inbox(key, unseen_only=False, mark_seen=False, store=store)
        )
    seen = total - unseen
    split = _confirmation_split(agent_id, store)
    if split is None:
        breakdown = "confirmation not measured"
    else:
        breakdown = f"confirmed={split[0]} seen-but-unconfirmed={split[1]}"
    detail = f"unseen={unseen} seen={seen} ({breakdown}) (keys={keys})"
    # Small backlog: working, or merely busy. Either way nothing is piling up.
    if unseen <= threshold:
        return {"ok": True, "detail": detail, "hint": None}
    # Over threshold. `seen` says something drained ONCE, which is not the
    # question — see this function's docstring.
    if seen > 0:
        return {"ok": None, "detail": detail, "hint": _DRAIN_UNKNOWN_HINT}
    return {"ok": False, "detail": detail, "hint": _DRAIN_HINT}


def _check_channel_capable() -> dict[str, Any]:
    """ok when ``scitex_cards._mcp_channel`` imports and exposes ``_serve``/``_run``."""
    try:
        from . import _mcp_channel as channel
    except Exception as exc:  # noqa: BLE001 — import failure is a reportable state
        return {
            "ok": False,
            "detail": f"import scitex_cards._mcp_channel failed ({exc})",
            "hint": (
                "upgrade to scitex-cards>=0.7.32: pip install -U 'scitex-cards[all]'"
            ),
        }
    missing = [attr for attr in ("_serve", "_run") if not hasattr(channel, attr)]
    if missing:
        return {
            "ok": False,
            "detail": f"scitex_cards._mcp_channel missing {missing}",
            "hint": (
                "upgrade to scitex-cards>=0.7.32 (the unified tools+channel "
                "server): pip install -U 'scitex-cards[all]'"
            ),
        }
    return {
        "ok": True,
        "detail": "scitex_cards._mcp_channel present (_serve/_run)",
        "hint": None,
    }


__all__ = [
    "_check_agent_id",
    "_check_channel_capable",
    "_check_channel_drain",
    "_check_delivery_liveness",
    "_check_notifyd_alive",
]

# EOF
