#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-message DELIVERY STATE — durable, confirmed, or genuinely unknown.

WHAT THIS DECIDES, AND WHAT IT DOES NOT. This module answers one question per
message — did it get there? — and never draws anything. ``chat_receipts.js``
owns the presentation (three dots filling sent -> queued -> read, per the
operator 2026-07-29 09:06); the earlier plan to reuse
claude-code-telegrammer's LIGHTNING/EYE emoji verbatim was withdrawn by the
operator at 09:03 as unkind to a first-time reader. THE MEANINGS ARE STILL
cct's, which is the part that mattered:

    sent  cct stage 1 (``RECEIPT_DELIVERED_EMOJI``) — durable in the store.
    read  cct stage 2 AS ORIGINALLY SPECIFIED (#14) — the recipient confirmed.

ONE DIVERGENCE FROM cct's CURRENT BEHAVIOUR, STATED RATHER THAN SMUGGLED. cct
today fires stage 2 UNCONDITIONALLY the instant the bridge takes ownership
(``receipts.ts`` stage 2, operator revision #41), so there it means "the relay
has it"; the agent-reached signal was moved up to stage 3. It was moved because
cct had no recipient-sourced evidence to hang it on. Cards does —
``dm_receipts`` is written BY the reader — so ``read`` here keeps the original
#14 promise. cct's own comment anticipates exactly this (``receipts.ts`` lines
157-159): "If sac ever splits enqueue-ack from completed-turn, an explicit
'agent received' stage can be reintroduced". Same meaning, stricter evidence.

THE RULE THAT MUST NOT BEND: ``read`` MEANS CONFIRMED BY THE RECIPIENT. It is
never derived from a transport call returning. A ``send()`` that returned is
precisely what lied to the operator for weeks — DMs were acked at handover and
the agent never saw one of them. So it has exactly one source: a
``dm_receipts`` row whose ``reader`` is a recipient of that message. No receipt,
no confirmation. An absent mark is honest; a wrong mark is the bug this feature
exists to expose.

WHY THIS JOINS ON ``message_id`` AND NOTHING ELSE. The tempting shortcut is to
match a stored message to its delivery notification by ``(thread, ts, actor)``.
That tuple is ALSO the inbox's dedupe key, and DM timestamps are
second-resolution, so the mapping is many-to-one BY CONSTRUCTION — measured on
the live store, two distinct durable messages collapsed onto one notification.
Scoring off that join marks a message that was never delivered at all, which is
the precise failure this feature exists to detect. ``dm_receipts`` carries the
message id natively, so the join is exact and the shortcut is simply not needed.

WHAT IS NOT HERE: ``queued``. The operator's final spec has three steps, and the
middle one — a row exists in the recipient's inbox — is NOT computed by this
module, because ``dispatch_to_inbox`` does not carry the message id and the only
available join is the lossy tuple above. The renderer therefore draws that dot
as INDETERMINATE rather than guessing. Carrying ``msg_id`` into
``_inbox.enqueue`` is what would make it honest.

WHY CAPABILITY COMES FROM MEMBERSHIP, NOT FROM THE USER REGISTRY. "Can this
recipient confirm at all?" must be answered from the RECIPIENT's standing, never
from the absence of a receipt — decide it from absence and every genuinely
unconfirmed message quietly becomes "cannot tell", and the detector is dead.
The registry looks like the right oracle and is not: ``list_users`` parses the
store as YAML, so on the canonical SQLite store it raises, ``_registry_agents``
fail-softs to ``[]``, and the live ``/dm/threads`` reports ``kind: null`` for
every peer. Keying capability on "is registered" would therefore mark EVERY
message unknowable and hide the outage completely. Membership is the honest
oracle and the schema says so: ``dm_messages`` records who SENT, the member
event log records who can SEE (``_dm_read`` module docstring). A message whose
thread has recipients has parties who can confirm; a message with no recipients
has none, and that -- not a missing receipt -- is what "cannot tell" means.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ._dm_read import CURRENT_MEMBERS_SQL, _open

#: Durable, and no recipient has confirmed it yet. The read dot stays hollow.
STATE_PENDING = "pending"

#: Every recipient confirmed it. The ONLY state that fills the read dot.
STATE_RECEIVED = "received"

#: The message has no recipient we can name, so confirmation is not merely
#: missing — it is not expressible. Drawn as a DASHED dot: never filled, and
#: never a hollow "not yet" either, because that is also a claim.
STATE_UNKNOWABLE = "unknowable"


def recipients_of(members: set[str], sender: str) -> set[str]:
    """Who could confirm this message: the thread's current members, less its
    sender.

    A sender reading their own message is not a receipt, and this is where that
    is enforced — the sender is removed from the audience BEFORE any receipt is
    counted, so a self-receipt cannot reach the tally at all.
    """
    return {m for m in members if m != sender}


def state_for(recipients: set[str], confirmed: set[str]) -> str:
    """The state of one message, given who could confirm and who did.

    PURE, and the whole honesty rule in three branches. ``received`` requires
    EVERY recipient to have confirmed: on a pair thread that is "the recipient
    confirmed", and on a group thread it refuses to let one member's receipt
    speak for the others.
    """
    if not recipients:
        return STATE_UNKNOWABLE
    if confirmed >= recipients:
        return STATE_RECEIVED
    return STATE_PENDING


def _current_members(conn: sqlite3.Connection, thread_id: str) -> set[str]:
    """Peers currently joined to ``thread_id``, folded from the event log."""
    rows = conn.execute(
        f"SELECT member FROM ({CURRENT_MEMBERS_SQL}) "
        "WHERE thread_id = ? AND current_action = 'join'",
        (thread_id,),
    ).fetchall()
    return {str(r[0]) for r in rows}


def _readers_by_message(
    conn: sqlite3.Connection, thread_id: str
) -> dict[str, set[str]]:
    """``{message_id: {reader}}`` for one thread, joined ONLY on ``message_id``.

    One query for the whole thread rather than one per message, and the join
    predicate is the primary key — no timestamp, no actor, no thread tuple.
    """
    out: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT r.message_id, r.reader FROM dm_receipts r "
        "JOIN dm_messages m ON m.id = r.message_id "
        "WHERE m.thread_id = ?",
        (thread_id,),
    ):
        out.setdefault(str(row[0]), set()).add(str(row[1]))
    return out


def receipt_state_for_conn(conn: sqlite3.Connection, thread_id: str) -> dict[str, dict]:
    """``{message_id: {"state", "readers", "recipients"}}`` for one thread.

    Every live message of the thread appears, so a client can render a mark for
    each bubble without a second round trip and without guessing at a missing
    key. ``readers`` is the confirmed set INTERSECTED with the recipients, so a
    stray receipt from a non-member (or from the sender) is never displayed as
    a confirmation.
    """
    members = _current_members(conn, thread_id)
    readers = _readers_by_message(conn, thread_id)
    rows = conn.execute(
        "SELECT id, sender FROM dm_messages WHERE thread_id = ? AND deleted_at IS NULL",
        (thread_id,),
    ).fetchall()

    out: dict[str, dict] = {}
    for row in rows:
        message_id, sender = str(row[0]), str(row[1])
        recipients = recipients_of(members, sender)
        confirmed = readers.get(message_id, set()) & recipients
        out[message_id] = {
            "state": state_for(recipients, confirmed),
            "readers": sorted(confirmed),
            "recipients": sorted(recipients),
        }
    return out


def receipt_state_for_thread(
    thread_id: str,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
) -> dict[str, dict]:
    """Public read: the per-message delivery state of ``thread_id``."""
    conn = _open(db, store)
    try:
        return receipt_state_for_conn(conn, thread_id)
    finally:
        conn.close()


__all__ = [
    "STATE_PENDING",
    "STATE_RECEIVED",
    "STATE_UNKNOWABLE",
    "receipt_state_for_conn",
    "receipt_state_for_thread",
    "recipients_of",
    "state_for",
]

# EOF
