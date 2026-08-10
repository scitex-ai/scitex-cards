#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CONFIRMATION as a verb of its own — the lossless-delivery seam.

HANDOVER IS NOT CONFIRMATION. ``poll_notifications(ack=True)`` marked a
notification seen at the moment it HANDED IT OVER. A consumer that read with
``ack=True`` and then failed to deliver had PERMANENTLY DESTROYED the message:
it was gone from the unseen set, so no retry could ever find it again. That
turned a TRANSIENT delivery failure into PERMANENT LOSS — and it was the
easiest call in the API to write.

MEASURED ON THE LIVE STORE, 2026-07-29. Five operator DMs were enqueued
correctly and were all readable in the store (07:17 / 07:02 / 06:52 / 06:38 /
06:25). Four of the five were marked SEEN in the agent's inbox. The agent
never saw a single one of them, and two of the four were marked after the last
manual ack, so the agent did not mark them. Earlier the same failure cost the
operator an answer to a question their agent was BLOCKED on; they asked twice,
eleven minutes apart, because nothing came back.

Constitution: "Always close the loop... Confirm arrival, not dispatch: the
recipient may be dead, and you would not know."

THE SPLIT THIS MODULE OWNS
--------------------------
* READING NEVER ADVANCES THE CURSOR. ``poll_notifications`` defaults to
  ``ack=False`` and, in that shape, is a pure read.
* :func:`confirm_notifications` is the ONLY verb that advances it, and it
  advances it PER ID — the consumer confirms exactly what it actually
  delivered.
* REDELIVERY IS THE DEFAULT. An unconfirmed notification is still unseen, so
  the next poll returns it again. A consumer that dies between read and
  confirm loses NOTHING.
* CONFIRMING IS IDEMPOTENT. Re-confirming an id is a no-op, never an error:
  a retrying consumer must not be punished for retrying.

``ack=True`` is DEPRECATED, not removed — see :func:`warn_ack_on_read` and the
reasoning in ``docs/`` / the PR: sac consumes this API today and a surprise
behaviour change to their read path would be its own outage.

ZERO external-runtime imports (this sits under the standalone delivery rail).
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

from . import _inbox

logger = logging.getLogger(__name__)

#: The safe consumer loop, in one line. Quoted verbatim by every surface that
#: reports the deprecation so the fix always travels with the complaint.
SAFE_PATTERN = (
    "poll_notifications(agent, ack=False) -> deliver each record -> "
    "ack_notifications(agent, ids=[<ids you actually delivered>])"
)

#: The full deprecation text. Carries the MEASUREMENT, not just an opinion —
#: a deprecation with no incident behind it reads as taste and gets ignored.
ACK_ON_READ_MESSAGE = (
    "ack-on-read is DEPRECATED and LOSES MESSAGES. Passing ack=True marks "
    "notifications seen at HANDOVER, so a consumer that reads them and then "
    "fails to deliver has permanently destroyed them: they are gone from the "
    "unseen set and no retry can find them. Measured on the live store "
    "2026-07-29 — five operator DMs enqueued correctly, four marked SEEN, the "
    "agent saw none of them. Use the confirm-by-id pattern instead: "
    + SAFE_PATTERN
    + ". Reading never advances the cursor, so an unconfirmed notification "
    "comes back on the next poll."
)

#: Process-level latch for the log line only. The ``warnings`` emission is NOT
#: latched — a caller that filters warnings must still be able to catch every
#: occurrence (and ``pytest.warns`` must not depend on call order).
_LOGGED_ONCE = False


def warn_ack_on_read(caller: str = "poll_notifications") -> str:
    """Announce ack-on-read loudly and return the message text.

    Three surfaces, because one is never enough: a ``DeprecationWarning`` for
    Python callers and test assertions, a WARNING log line (once per process,
    so a long-lived poll loop does not spam the log) for the operator, and the
    returned string, which the caller puts in the tool's JSON payload so the
    AGENT reading the result sees it too.
    """
    global _LOGGED_ONCE
    warnings.warn(f"{caller}: {ACK_ON_READ_MESSAGE}", DeprecationWarning, stacklevel=3)
    if not _LOGGED_ONCE:
        _LOGGED_ONCE = True
        logger.warning("%s: %s", caller, ACK_ON_READ_MESSAGE)
    return ACK_ON_READ_MESSAGE


def _normalize_ids(ids: "list[str] | str | None") -> list[str]:
    """Accept a bare id or a list; drop falsy entries, preserve caller order."""
    if ids is None:
        return []
    if isinstance(ids, str):
        ids = [ids]
    out: list[str] = []
    for nid in ids:
        if nid and nid not in out:
            out.append(nid)
    return out


def recipient_keys(agent: str, store: str | Path | None = None) -> list[str]:
    """The inbox keys ``agent``'s notifications can live under, raw name first.

    A producer enqueues under whatever :func:`scitex_cards._notify.
    resolve_recipients` returned — the stable ``u_*`` id for a registered
    agent, the raw name otherwise. Confirming must therefore try BOTH, exactly
    like :func:`scitex_cards._mcp_channel.recipient_keys` does on the read
    side; confirming only one key would silently fail to advance the cursor
    for records that live under the other.
    """
    keys = [agent] if agent else []
    try:
        from ._users import resolve_user

        user = resolve_user(agent, store=store)
    except Exception as exc:  # noqa: BLE001 — resolution must never break confirm
        logger.warning("ack_notifications: resolving %r failed: %s", agent, exc)
        user = None
    resolved = getattr(user, "id", None) if user is not None else None
    if resolved and resolved not in keys:
        keys.append(resolved)
    return keys


def _record_dm_receipts(
    message_ids: list[str],
    *,
    reader: str,
    store: str | Path | None = None,
) -> int:
    """Turn confirmed DM notifications into ``dm_receipts`` rows. Fail-soft.

    THIS IS THE ONLY HONEST PLACE TO WRITE ONE. ``_dm_receipt_state`` requires
    that ``read`` mean confirmed-by-the-recipient and never be derived from a
    transport call returning — a ``send()`` that returned is exactly what lied
    to the operator for weeks. A confirm is the recipient naming an id and
    saying it arrived, so it satisfies that rule rather than relaxing it.

    Before this, the lamp could not light for a channel-delivered agent at all.
    A receipt was only written by ``dm_list(ack=True)``, which an agent that
    receives DM bodies over the channel push never calls: measured on the live
    store, only three readers had EVER written a receipt (the operator's
    browser, plus two agents that do poll), while every other agent in the
    fleet had written zero. The operator therefore could not tell an agent
    reading them from an agent that was dead.

    Fail-soft for the same reason ``dispatch_to_inbox`` is: by the time we get
    here the cursor has already advanced and the confirmation is already
    stamped. A receipt is a MIRROR of that fact, so raising would report a
    confirmation as failed when it in fact succeeded.

    Delegates to ``_dm_write.mark_read``, which already skips ids the store
    does not hold — a receipt carries a foreign key onto ``dm_messages`` and
    ``INSERT OR IGNORE`` does NOT cover a foreign-key violation, it raises. One
    batched transaction rather than one per id.
    """
    if not message_ids or not reader:
        return 0
    try:
        from ._dm.write import mark_read

        return mark_read(message_ids, reader, store=store, source="confirm")
    except Exception as exc:  # noqa: BLE001 — a mirror must not fail a confirm
        logger.warning(
            "ack_notifications: recorded the confirmations but could not write "
            "their dm receipts (messages %s, reader %r): %s",
            message_ids,
            reader,
            exc,
        )
        return 0


def confirm_notifications(
    agent: str,
    ids: "list[str] | str | None",
    *,
    store: str | Path | None = None,
) -> dict[str, Any]:
    """Advance the cursor for exactly ``ids`` — the ONLY verb that advances it.

    Idempotent by construction: an id that is already confirmed, or that this
    agent's inbox never held, is a no-op for that id and NEVER an error. The
    return value says which is which, so a consumer can tell a successful
    retry (``already_confirmed``) from a typo (``unknown``) without either
    one raising.

    Returns
    -------
    dict
        ``{"agent", "recipient_id", "requested", "confirmed",
        "already_confirmed", "unknown"}`` — ``confirmed`` holds the ids this
        call actually flipped unseen -> seen.
    """
    from ._inbox_receipt import record_confirmation

    keys = recipient_keys(agent, store)
    primary = keys[-1] if keys else agent
    requested = _normalize_ids(ids)
    confirmed: list[str] = []
    known: set[str] = set()
    dm_messages: list[str] = []
    if requested and keys:
        for key in keys:
            for nid in _inbox.ack(key, requested, store=store):
                if nid not in confirmed:
                    confirmed.append(nid)
            # THE ONLY ARRIVAL EVIDENCE THAT EXISTS. Stamped separately from
            # the cursor and independently of whether this call is the one that
            # flipped it: the channel drain has usually already advanced `seen`
            # (it pushed the record), so keying the confirmation off that flip
            # would record nothing for exactly the records that were pushed —
            # which is every record the `delivery_confirmed` check cares about.
            record_confirmation(key, requested, store=store)
            for record in _inbox.poll_inbox(
                key, unseen_only=False, mark_seen=False, store=store
            ):
                record_id = record.get("id")
                if record_id:
                    known.add(record_id)
                if (
                    record_id in requested
                    and record.get("event_type") == "dm"
                    and record.get("msg_id")
                ):
                    dm_messages.append(record["msg_id"])
    # AFTER the loop, in one transaction. This is what finally lights the
    # operator's read lamp for a channel-delivered agent: see
    # `_record_dm_receipts` for why a confirm is the only honest trigger.
    _record_dm_receipts(dm_messages, reader=agent, store=store)
    return {
        "agent": agent,
        "recipient_id": primary,
        "requested": requested,
        "confirmed": confirmed,
        "already_confirmed": [
            nid for nid in requested if nid in known and nid not in confirmed
        ],
        "unknown": [nid for nid in requested if nid not in known],
    }


__all__ = [
    "ACK_ON_READ_MESSAGE",
    "SAFE_PATTERN",
    "confirm_notifications",
    "recipient_keys",
    "warn_ack_on_read",
]

# EOF
