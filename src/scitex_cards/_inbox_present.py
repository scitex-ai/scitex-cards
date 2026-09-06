#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRESENTATION — put the message in front of the agent, then ask for the ack.

THE SECOND DELIVERY RAIL. Delivery had exactly ONE rail: the MCP channel push.
When the agent spec whitelisted ``server:scitex-cards`` while ``.mcp.json``
registered the server as ``scitex-cards`` (renamed during the migration), Claude
Code SILENTLY DISCARDED every push. ``send()`` returned normally, the drain
acked on that success, and the message was gone. Measured on this agent: 228
inbox rows, ZERO unseen, roughly three weeks of operator DMs that never arrived.
sac later found the same hazard armed on ~96 spec entries fleet-wide.

Fixing that one spec is not a fix for the class. A single rail with nothing
independent checking it fails again, for some other reason, and it fails
SILENTLY — because "the transport returned" is not "the recipient received".
So this module is the rail that does not depend on the push: the agent's own
turn-end hook READS the store directly and shows the agent what is waiting.

THE ORDER OF OPERATIONS IS THE SAFETY PROPERTY (operator, 2026-07-29)
--------------------------------------------------------------------
A hook that merely blocked on unacked messages would have DEADLOCKED every
agent the morning of the outage: the messages were queued and had NEVER been
shown, so no agent COULD have acked them. Blocking an actor where the actor
cannot remediate is forbidden. Hence:

1. PULL the pending messages — a pure read, cursor untouched;
2. PUT THEM IN FRONT OF THE AGENT — the rendered text IS the delivery;
3. only THEN require the ack before the turn may finish;
4. ack via ``ack_notifications(agent, ids)`` — per id, idempotent.

The agent can always comply, because the hook itself just delivered the thing
it demands acknowledgement of.

THE INVARIANT THAT MAKES (2) TRUE RATHER THAN INTENDED
------------------------------------------------------
:func:`present` returns ``(text, presented_ids)``, and ``presented_ids`` holds
EXACTLY the ids whose content is in ``text``. It is built by appending an id at
the same moment its block is appended to the text, so the two cannot drift.
Callers demand acks for ``presented_ids`` and for nothing else — which is the
deadlock guard expressed as code rather than as care. A record that did not fit
the budget is simply not demanded: it stays unconfirmed, so the next poll
returns it and the next turn presents it.

BUDGET. Claude Code caps injected hook context at 10,000 characters and spills
the overflow to a file. A presentation that gets spilled has not been presented,
so the budget here is deliberately well under the cap and bodies are excerpted
rather than dumped.

ZERO external-runtime imports — this sits under the standalone delivery rail.
No scitex_agent_container, no claude-code-telegrammer, no sac-managed
environment assumed.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from ._inbox import poll_inbox
from ._inbox_confirm import recipient_keys

#: Claude Code's hard cap on injected hook context. Over-cap output is spilled
#: to a file and replaced by a preview — i.e. NOT presented. Documented here so
#: the budget below is traceable to the number it respects.
INJECTED_CONTEXT_CAP = 10_000

#: Character budget for the message section. The remainder of the cap is left
#: for the header, the ack instruction, and the runnable-card list that shares
#: the same reason string.
PRESENT_BUDGET_CHARS = 5_000

#: Per-message body excerpt length. Long enough to carry an operator question
#: in full, short enough that ten messages still fit the budget.
BODY_EXCERPT_CHARS = 400

#: Hard ceiling on messages shown in one turn, independent of the budget. Ten
#: instructions is already more than a turn can act on.
MAX_PRESENTED = 10


def pending(agent: str, store: str | Path | None = None) -> list[dict]:
    """UNCONFIRMED notifications for ``agent`` across every key they live under.

    NOT "unseen", and the difference is the whole point. ``seen`` was a
    delivery cursor until the channel drain began advancing it as it PUSHES —
    ``record_push(advance_cursor=True)`` sets ``seen`` in the same statement
    that writes ``pushed_at`` — after which a record handed to a session that
    then died was seen, unconfirmed, and invisible to every later poll. This
    rail exists to catch exactly that outage and was reading the field the
    outage moves. ``confirmed_at`` is the only evidence a consumer acted, as
    :func:`scitex_cards._inbox_receipt.is_confirmed` has said all along.

    Records pushed within :data:`_health_delivery.PUSH_CONFIRM_GRACE_SECONDS`
    are held back: a consumer still working on what it was just handed does not
    need it presented twice, and presenting it would be a duplicate rather than
    a recovery. A record never pushed at all has no such stamp and is presented
    immediately, exactly as before.

    A PURE READ: ``mark_seen=False`` on every call, so the cursor never moves
    here. Advancing it is :func:`scitex_cards._inbox_confirm.
    confirm_notifications`' job alone, and only for ids a consumer says it
    actually delivered.

    BOTH KEYS, deliberately. A producer enqueues under whatever
    ``_notify.resolve_recipients`` returned — the stable ``u_*`` id for a
    registered agent, the raw name otherwise. Reading only the raw name is the
    same silent-miss shape as the outage this rail exists to catch: the
    messages are in the store, readable, and the reader looks in the other
    drawer. ``recipient_keys`` is the shared answer to "which drawers".

    Returns records oldest-first, de-duplicated by id (the two keys can resolve
    to the same inbox).
    """
    from ._health_delivery import (  # noqa: PLC0415 -- import cycle
        PUSH_CONFIRM_GRACE_SECONDS,
        _age_seconds,
    )
    from ._inbox_receipt import is_confirmed  # noqa: PLC0415 -- import cycle

    now = _dt.datetime.now(_dt.timezone.utc)
    seen_ids: set[str] = set()
    out: list[dict] = []
    for key in recipient_keys(agent, store):
        for record in poll_inbox(key, unseen_only=False, mark_seen=False, store=store):
            # UNCONFIRMED, NOT UNSEEN. `seen` stopped meaning "the consumer
            # got it" the day the channel drain began advancing the cursor as
            # it pushes: `record_push(advance_cursor=True)` sets `seen = 1` in
            # the SAME statement that writes `pushed_at`. So a record pushed to
            # a session that then died is seen, unconfirmed, and — under the
            # old `unseen_only=True` — never presented again. `is_confirmed`
            # already said so in its own docstring while this rail read the
            # other field; the two are reconciled here.
            if is_confirmed(record):
                continue
            # A RECENT PUSH IS NOT A LOST ONE. Without this the record would be
            # presented while the consumer it was just handed to is still
            # acting on it, which is a duplicate rather than a recovery. The
            # grace is the delivery health check's own constant, so the two
            # surfaces cannot drift into disagreeing about what "overdue" is.
            age = _age_seconds(record.get("pushed_at"), now)
            if age is not None and age < PUSH_CONFIRM_GRACE_SECONDS:
                continue
            nid = record.get("id")
            if nid and nid in seen_ids:
                continue
            if nid:
                seen_ids.add(nid)
            out.append(dict(record))
    out.sort(key=lambda r: str(r.get("ts") or ""))
    return out


def _excerpt(body) -> str:
    """Whitespace-collapsed body, truncated with an explicit marker."""
    text = " ".join(str(body or "").split())
    if len(text) <= BODY_EXCERPT_CHARS:
        return text
    return text[:BODY_EXCERPT_CHARS] + " …[truncated, full text via poll_notifications]"


def _render_one(index: int, record: dict) -> str:
    """One message as the agent sees it — sender, when, id, text."""
    who = str(record.get("actor") or "").strip() or "unknown sender"
    when = str(record.get("ts") or "").strip() or "unknown time"
    kind = str(record.get("event_type") or "").strip() or "notification"
    card = str(record.get("card_id") or "").strip()
    where = f" on card {card}" if card and card != "(inbox)" else ""
    text = _excerpt(record.get("body")) or "(empty body)"
    return (
        f"  [{index}] from {who} at {when} ({kind}{where})\n"
        f"      id: {record.get('id') or '(no id)'}\n"
        f"      {text}"
    )


def present(
    records: list[dict],
    *,
    budget: int = PRESENT_BUDGET_CHARS,
    max_presented: int = MAX_PRESENTED,
) -> "tuple[str, list[str]]":
    """Render ``records`` as delivered text plus the ids that text contains.

    THE CONTRACT, and the reason this returns a pair instead of a string: the
    second element is EXACTLY the ids whose content is in the first. A caller
    may demand an ack for those ids and must not demand one for any other,
    because an ack demanded for a message the agent was never shown is the
    deadlock this whole design exists to avoid.

    Records that do not fit ``budget`` (or fall past ``max_presented``) are
    counted in a trailing line and NOT listed as presented. Nothing is lost:
    an unconfirmed record stays unseen and comes back on the next poll.

    Returns ``("", [])`` for an empty input — a caller must then not block.
    """
    usable = [r for r in (records or []) if r.get("id")]
    if not usable:
        return "", []
    blocks: list[str] = []
    presented: list[str] = []
    used = 0
    for record in usable:
        if len(presented) >= max_presented:
            break
        block = _render_one(len(presented) + 1, record)
        if used + len(block) > budget and presented:
            break
        if used + len(block) > budget and not presented:
            # Never return an empty presentation just because the FIRST body is
            # huge — show it, hard-truncated, so the turn can still make
            # progress. An unshown message is the failure mode; an ugly one
            # is not.
            block = block[:budget]
        blocks.append(block)
        presented.append(str(record.get("id")))
        used += len(block)
    withheld = len(usable) - len(presented)
    if withheld > 0:
        blocks.append(
            f"  (+{withheld} more unread — NOT shown here and NOT being asked "
            f"for: they stay unconfirmed and are redelivered next turn)"
        )
    return "\n".join(blocks), presented


__all__ = [
    "BODY_EXCERPT_CHARS",
    "INJECTED_CONTEXT_CAP",
    "MAX_PRESENTED",
    "PRESENT_BUDGET_CHARS",
    "pending",
    "present",
]

# EOF
