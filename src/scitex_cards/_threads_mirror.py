#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The side rails of a DM write: durable inbox dispatch, sidecar, receipts.

Split out of :mod:`scitex_cards._threads` when the DM-into-the-store dual write
pushed that module past its size budget. The seam is a real one and worth
naming: everything here is a MIRROR — work a DM write does IN ADDITION to
committing the message, none of which may fail the write.

Legacy dispatches, the sidecar, and receipts remain fail-soft after the DM is
durable. A responder-issued ``exchange_id`` changes the inbox's role: that row
is the durable handoff SAC consumes, so its failure is raised and HTTP 202 is
withheld. The exception does not claim the already-written DM vanished; the
exchange error carries its uncertainty and tells the caller to inspect by id.

The polarity is the load-bearing detail. Before schema v5 the SIDECAR was the
store of record and everything else was best-effort; now the DATABASE is, and
the sidecar has joined the mirrors. See ``docs/design/dm-into-cards-db.md``.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def dispatch_to_inbox(record: dict, store: str | Path | None) -> "dict | None":
    """Enqueue the DM; responder-issued exchanges make this handoff strict.

    Keyed exactly like ``poll_notifications``: the recipient name resolves to
    its stable ``u_*`` user id when registered, else the raw name is the key.
    A legacy record has no delivery exchange and retains fail-soft behaviour.
    For a new responder-issued exchange the inbox row is the durable handoff
    to SAC, so failure propagates and prevents an inaccurate HTTP 202.
    """
    try:
        from ._users import resolve_user

        to = record["to"]
        try:
            user = resolve_user(to, store=store)
        except Exception:  # noqa: BLE001 — unresolvable ⇒ raw-name key
            user = None
        recipient_id = user.id if user is not None else to
        if record.get("exchange_id"):
            # New delivery exchanges are PostgreSQL-only. Selecting the legacy
            # inbox backend here would create a private second queue that SAC
            # cannot observe, so the canonical door is named directly.
            from ._inbox_postgres import enqueue
        else:
            from ._inbox import enqueue

        return enqueue(
            recipient_id,
            event_type="dm",
            card_id=record["thread"],
            body=record["body"],
            actor=record["from"],
            ts=record["ts"],
            # THE POINT OF THIS WHOLE CHAIN. The message id was always right
            # here (the failure log below already prints it) but never reached
            # the inbox, so a confirmed notification could not be joined back
            # to the message it delivered — which is why the operator's read
            # lamp never lit for a channel-delivered agent, and why `queued`
            # was left uncomputable (_dm_receipt_state.py:43-48).
            msg_id=record.get("id"),
            exchange_id=record.get("exchange_id"),
            store=store,
        )
    except Exception:  # noqa: BLE001 — delivery accelerator, not the SSOT
        if record.get("exchange_id"):
            raise
        logger.warning(
            "dm-dispatch: inbox enqueue failed for %r (message %s already "
            "committed to the database)",
            record.get("to"),
            record.get("id"),
            exc_info=True,
        )
        return None


def mirror_to_sidecar(record: dict, key: str, store: str | Path | None) -> None:
    """Append the record to ``threads.json`` too. Best-effort, never fatal.

    The sidecar is no longer the store of record, but it is still the READ path
    and it is the ROLLBACK STATE, so it is kept complete: rolling this
    migration back must be redeploying the previous version, not restoring a
    backup. That only holds while this file stays a faithful copy.
    """
    try:
        from ._threads import _load_threads, threads_path
        from ._threads_io import _save_threads_unlocked, _threads_lock

        path = threads_path(store)
        with _threads_lock(path):
            threads = _load_threads(path)
            threads.setdefault(key, []).append(record)
            _save_threads_unlocked(threads, path)
    except Exception:  # noqa: BLE001 — mirror, not the SSOT
        logger.warning(
            "dm sidecar mirror failed for message %s (already committed to "
            "the database, which is the store of record)",
            record.get("id"),
            exc_info=True,
        )


def mirror_receipts(
    message_ids: list[str], reader: str, store: str | Path | None
) -> None:
    """Record the same reads as ``dm_receipts`` rows. Best-effort, never fatal.

    Read state has to travel WITH the messages or every already-read DM pops
    unread for everyone the moment reads flip to the database. A receipt is
    INSERT-ONLY and keyed ``(message_id, reader)``, so mirroring is idempotent
    and re-flipping an already-read message costs nothing.
    """
    if not message_ids:
        return
    try:
        from ._dm.write import mark_read

        mark_read(message_ids, reader, store=store)
    except Exception:  # noqa: BLE001 — mirror, not the SSOT
        logger.warning(
            "dm receipt mirror failed for reader %r (%d ids)",
            reader,
            len(message_ids),
            exc_info=True,
        )


__all__ = ["dispatch_to_inbox", "mirror_receipts", "mirror_to_sidecar"]

# EOF
