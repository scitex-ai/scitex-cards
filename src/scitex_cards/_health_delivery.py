#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Were the notifications we PUSHED ever CONFIRMED to have arrived?

The one question every other channel check leaves unasked. ``channel_capable``
asks "can we push at all", ``channel_drain`` asks "is the inbox being consumed",
``channel_reaches_session`` asks "does this session's launch line name us". All
three were GREEN for weeks while this agent's operator DMs were destroyed on
arrival: the spec allowlisted ``server:scitex-cards`` while ``.mcp.json``
registers the server as ``scitex-cards``, so Claude Code read each push and
dropped it. The inbox held 228 rows for the agent and ZERO unseen — enqueued,
then consumed, then gone. Nothing was ever red.

The reason it could not be red is that the drain ack'd on ``send()`` RETURNING.
A JSON-RPC notification has no reply (see :mod:`scitex_cards._inbox_receipt` for
the measurement), so "the transport call returned" was being stored as "the
recipient received it" — a claim the transport cannot support. Once those two
facts are stored separately, the outage has a signature you can see:
``pushed_at`` set on hundreds of rows, ``confirmed_at`` set on none.

THREE-VALUED ON PURPOSE
-----------------------
"nothing is unconfirmed" and "I cannot tell" are DIFFERENT answers and this
check refuses to collapse them into ``ok=True``:

* ``ok=True``  — no push has gone unconfirmed past the grace window.
* ``ok=False`` — at least one has. That is the incident shape.
* ``ok=None``  — UNKNOWN. The drain has never recorded a push for this agent, or
  the inbox could not be read. There is no evidence either way, and a check that
  cannot measure must say so rather than pass.

:func:`scitex_cards._health.health` keeps ``None`` out of its failure count (an
unknown is not a fault) while naming it in the summary, so an operator sees
"unknown: delivery_confirmed" instead of a green line that measured nothing.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from ._inbox_receipt import CONFIRMED_AT, PUSHED_AT, receipts

#: How long a pushed notification may sit unconfirmed before it is a fault.
#: Generous on purpose — a healthy consumer confirms within one poll interval
#: (5 s), so 15 minutes cannot fire on ordinary lag; the incident ran for WEEKS.
PUSH_CONFIRM_GRACE_SECONDS = 900

#: Ids listed verbatim in the failure detail before it switches to a count.
_SAMPLE_IDS = 5

#: Event types the producer RE-SENDS on a schedule. An unconfirmed one of
#: these is superseded within a sweep interval, so losing it costs a delay;
#: everything else is a ONE-SHOT and losing it is permanent.
#:
#: THE TWO ARE WORTH COUNTING SEPARATELY BECAUSE THEY DEMAND DIFFERENT
#: URGENCY. A backlog of unconfirmed digests says the consumer is not
#: confirming — a real fault, but one where the CONTENT survives, because the
#: next digest carries the same facts. A single unconfirmed `dm` says a
#: message a human or a peer sent is gone, and no later notification will
#: reproduce it. Reporting one number for both invites reading the common,
#: recoverable case and concluding the rare, unrecoverable one is fine too.
_REPEATING_EVENTS = frozenset(
    {"reminder", "stale-active", "pending-backlog", "blocked-check"}
)

#: The remediation. Names BOTH causes because the reader cannot tell them apart
#: from the symptom, and says how to CHECK each one — a failing check that only
#: states what broke is half-written.
_UNCONFIRMED_HINT = (
    "Nothing is confirming arrival, and the most likely reason is that the "
    "client is DISCARDING these pushes. Claude Code surfaces a channel push "
    "only from a server named on its own launch line "
    "(--dangerously-load-development-channels server:<name>), matched against "
    "the key this MCP server is registered under in .mcp.json; the agent spec's "
    "channels: list is what produces that flag. A rename leaves the two "
    "disagreeing and every push is dropped on arrival with no error anywhere. "
    "CHECK IT: run `scitex-cards health` and read the channel_reaches_session "
    "line, which compares those two name sets directly; if it reports a "
    "mismatch, add the registered name to this agent's channels: list (keep the "
    "pre-rename name during a migration) and RESTART the session — the "
    "allowlist is read at launch, so editing it alone changes nothing. IF THE "
    "NAMES AGREE, the other cause is a consumer that never confirms: delivery "
    "is only proven by ack_notifications(agent, ids=[...]) called by the "
    "recipient after it has actually delivered each record."
)


def _parse_stamp(value: Any) -> _dt.datetime | None:
    """Parse an inbox timestamp (``2026-07-29T07:00:00Z``), or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _age_seconds(stamp: Any, now: _dt.datetime) -> float | None:
    """Seconds since ``stamp``, or ``None`` when it cannot be read."""
    parsed = _parse_stamp(stamp)
    if parsed is None:
        return None
    return (now - parsed).total_seconds()


def _collect(agent_id: str, store: str | Path | None) -> list[dict]:
    """Every receipt across BOTH inbox keys this agent's records can live under.

    A producer enqueues under the stable ``u_*`` id for a registered agent and
    under the raw name otherwise, so reading one key would silently miss half
    the evidence — the same fan-out ``channel_drain`` and the drain itself do.
    """
    from ._mcp_channel import recipient_keys

    out: list[dict] = []
    for key in recipient_keys(agent_id, store=store):
        out.extend(receipts(key, store=store))
    return out


def check_delivery_confirmed(
    agent_id: str | None,
    store: str | Path | None = None,
    *,
    grace_seconds: int = PUSH_CONFIRM_GRACE_SECONDS,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """Report pushes that were never confirmed — three-valued, never silent.

    Returns the standard ``{ok, detail, hint}`` record where ``ok`` is ``True``
    (nothing overdue), ``False`` (pushed-but-unconfirmed past ``grace_seconds``)
    or ``None`` (UNKNOWN: no push recorded, or the inbox is unreadable).
    """
    if not agent_id:
        return {
            "ok": None,
            "detail": (
                "unknown: agent id unresolved, so there is no inbox to audit — "
                "delivery confirmation unevaluated"
            ),
            "hint": (
                "set SCITEX_CARDS_AGENT_ID=<your-agent-id> so this agent's inbox "
                "can be identified, then re-run the doctor"
            ),
        }

    try:
        records = _collect(agent_id, store)
    except Exception as exc:  # noqa: BLE001 — unreadable is UNKNOWN, not ok
        return {
            "ok": None,
            "detail": (
                f"unknown: this agent's inbox could not be read "
                f"({type(exc).__name__}: {exc}) — delivery confirmation "
                f"unevaluated"
            ),
            "hint": (
                "make the inbox readable and re-run `scitex-cards health`; until "
                "it reads, nobody can tell whether pushed notifications are "
                "arriving or being discarded"
            ),
        }

    pushed = [r for r in records if r.get(PUSHED_AT)]
    confirmed = [r for r in records if r.get(CONFIRMED_AT)]
    if not pushed:
        return {
            "ok": None,
            "detail": (
                f"unknown: no channel push has ever been recorded for "
                f"{agent_id} ({len(records)} inbox record(s), 0 with a push "
                f"receipt) — the drain has not run against this inbox, so "
                f"there is no evidence either way"
            ),
            "hint": (
                "start the channel server for this agent (`scitex-cards mcp "
                "start` with SCITEX_CARDS_AGENT_ID set) and re-run the doctor; "
                "records enqueued before this version carry no receipt and "
                "stay unknown until new ones are pushed"
            ),
        }

    moment = now or _dt.datetime.now(_dt.timezone.utc)
    overdue = [
        r
        for r in pushed
        if not r.get(CONFIRMED_AT)
        and (_age_seconds(r.get(PUSHED_AT), moment) or float("inf")) > grace_seconds
    ]
    counts = (
        f"pushed={len(pushed)} confirmed={len(confirmed)} "
        f"unconfirmed_over_{grace_seconds}s={len(overdue)}"
    )
    if not overdue:
        return {
            "ok": True,
            "detail": f"delivery is being confirmed: {counts}",
            "hint": None,
        }

    ages = [_age_seconds(r.get(PUSHED_AT), moment) for r in overdue]
    oldest = max((a for a in ages if a is not None), default=None)
    oldest_text = f"{int(oldest)}s" if oldest is not None else "unknown"
    # ONE-SHOTS FIRST, in the count AND in the sample. A digest that was never
    # confirmed is replaced by the next sweep; a `dm` that was never confirmed
    # is gone. When the ids have to be truncated it must be the recoverable
    # ones that fall off the end.
    one_shot = [r for r in overdue if r.get("event_type") not in _REPEATING_EVENTS]
    repeating = len(overdue) - len(one_shot)
    sample = [str(r.get("id")) for r in (one_shot + overdue)[:_SAMPLE_IDS]]
    return {
        "ok": False,
        "detail": (
            f"{len(overdue)} notification(s) were PUSHED and never CONFIRMED "
            f"— {len(one_shot)} ONE-SHOT (unrecoverable: a dm/comment/event "
            f"nothing will re-send) and {repeating} repeating (a later sweep "
            f"carries the same facts) ({counts}); oldest unconfirmed push "
            f"{oldest_text} ago; ids {sample}"
            f"{' ...' if len(overdue) > len(sample) else ''}. The transport "
            f"accepted them, which proves only that our own stdout writer took "
            f"the bytes — nobody has said they arrived."
        ),
        "hint": _UNCONFIRMED_HINT,
    }


__all__ = [
    "PUSH_CONFIRM_GRACE_SECONDS",
    "check_delivery_confirmed",
]

# EOF
