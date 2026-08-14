#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite backend for the per-recipient pull-inbox (Phase 1 of the store
migration; incident card ``store-sqlite-migration-o1-writes-future-20260701``).

Why
---
The legacy task store was a single ~9 MB document holding BOTH the
``tasks:`` cards AND the ``inboxes:`` per-recipient notification
records. Every agent's digest-poll loop
(:func:`scitex_cards._inbox.poll_inbox` every 5 s) re-parsed the ENTIRE
store just to read ONE recipient's inbox — across ~21 agents the
fleet's biggest CPU sink; notifyd's per-owner enqueue also rewrote the
whole file repeatedly (a store-lock convoy).

This module moves ONLY the inbox read/write path onto SQLite so a poll
no longer parses all cards. The SciTeX runtime-DB convention places
package runtime databases at ``<store_dir>/runtime/<pkg-short>.db`` —
here ``<store_dir>/runtime/todo.db``. WAL mode lets the ~21 concurrent
pollers read without blocking the writer.

Scope
-----
INBOXES ONLY. This is now the DEFAULT backend (see
:mod:`scitex_cards._inbox`'s ``_use_sqlite``); the file-backed
break-glass backend (``SCITEX_TODO_INBOX_BACKEND=yaml``, its own
``inboxes.json`` sidecar) is the non-default fallback. Semantics —
dedup key ``(event_type, card_id, ts, actor)``, ``supersede`` dropping
UNSEEN ``(event_type, card_id)`` predecessors, ``poll_inbox(unseen_only,
mark_seen)``, and ``ack`` — are IDENTICAL across both backends so
callers cannot tell which one is active.

Connection / schema conventions mirror :mod:`scitex_cards._index` (the
existing stdlib-``sqlite3`` module): a ``@contextmanager``
``open_connection`` opening WAL + ``row_factory = sqlite3.Row``, an
idempotent ``init_schema``, and a tiny public API. NO ``scitex_db``
dependency (it is not installed).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from ._inbox_record import notification_columns, notification_record
from ._inbox_shape import shape_for
from ._inbox_sqlite_schema import (
    ENV_INBOX_DB,
    SCHEMA_VERSION,
    _ensure_msg_id,
    _ensure_ready,
    _is_migrated,
    _MIGRATED_FLAG,
    inbox_db_path,
    inbox_target,
    init_schema,
    open_connection,
)
from ._sql_null_safe import null_safe_eq_for

logger = logging.getLogger(__name__)


def _row_to_record(row: sqlite3.Row) -> dict:
    """Project a DB row back to the plain record dict the YAML path returns.

    ``seen`` is stored as an INTEGER 0/1 but the public contract returns a
    Python ``bool`` (tests assert ``rec["seen"] is False`` / ``is True``), so
    it is coerced here. Key order matches the YAML record for parity.
    """
    return {
        "id": row["id"],
        "event_type": row["event_type"],
        "card_id": row["card_id"],
        "body": row["body"],
        "actor": row["actor"],
        "ts": row["ts"],
        "seen": bool(row["seen"]),
        # ALWAYS present, None when this row predates the column or carries no
        # message (a card event, a digest). An absent key is how a consumer
        # reads `undefined`, renders nothing, and looks like it worked.
        "msg_id": row["msg_id"] if "msg_id" in row.keys() else None,
    }


# --------------------------------------------------------------------------- #
# Public inbox API (identical signatures + return shapes to _inbox.py)         #
# --------------------------------------------------------------------------- #
def enqueue(
    recipient_id: str,
    *,
    event_type: str,
    card_id: str,
    body: str,
    actor: str | None,
    ts: str | None = None,
    supersede: bool = False,
    msg_id: str | None = None,
    store: str | Path | None = None,
) -> "dict | None":
    """SQLite twin of :func:`scitex_cards._inbox.enqueue` — same contract.

    Builds ``{id, event_type, card_id, body, actor, ts, seen: False}`` and
    inserts it for ``recipient_id``. Dedups on ``(event_type, card_id, ts,
    actor)``, NULL-safe so ``actor=None`` dedups correctly.

    The null-safe operator is resolved per connection via
    :func:`scitex_cards._sql_null_safe.null_safe_eq_for`, NOT hardcoded, because
    THERE IS NO SPELLING THAT WORKS ON BOTH BACKENDS:

    * SQLite's ``IS`` is null-safe in every version that ships this module, but
      Postgres rejects ``IS $1`` outright.
    * Standard-SQL ``IS NOT DISTINCT FROM`` works on Postgres, but SQLite only
      learned it in 3.39 (2022-06).

    That second half is not hypothetical. On the live host (SQLite 3.37.2) the
    standard spelling made EVERY enqueue raise ``near "DISTINCT": syntax
    error``, the fail-soft caller swallowed it, and messages committed to the
    store while NO notification was ever delivered — for 36 hours. CI and every
    container ran a newer SQLite and parsed it happily, so the version floor,
    not the SQL, was the thing actually under test.

    Resolving from the connection is what lets this module move onto
    ``_db.connect()`` without either regressing that outage or breaking on
    Postgres. On SQLite it emits exactly the ``IS`` that is here today.

    When ``supersede`` is set, every EXISTING UNSEEN row matching
    both ``event_type`` AND ``card_id`` is deleted BEFORE the dedup/insert, so
    at most one pending digest per recipient survives (SEEN history is kept).
    Returns the enqueued record, or ``None`` for a falsy recipient / a deduped
    re-emit. See the YAML implementation for the full semantics.
    """
    if not recipient_id:
        return None
    # Reuse the YAML module's id + timestamp helpers so ids/timestamps have the
    # SAME shape on disk regardless of backend (``n_`` + 12 hex, Z-suffixed UTC).
    from ._inbox import _generate_notification_id, _utc_now_iso

    timestamp = ts if ts is not None else _utc_now_iso()
    with open_connection(inbox_target(store)) as conn:
        _ensure_ready(conn, store)
        # The null-safe operator is resolved from the LIVE connection rather
        # than hardcoded, so this SQL survives the move onto Postgres (where
        # ``IS ?`` is a syntax error). Today every one of these resolves to
        # SQLite's ``IS`` — byte-identical to what was here before — which is
        # the point of doing this step separately from the backend switch.
        ns_event_type = null_safe_eq_for(conn, "event_type")
        ns_card_id = null_safe_eq_for(conn, "card_id")
        # Same reasoning for WHERE the rows live: table and recipient column are
        # read from the live connection, not assumed. On SQLite this spells
        # exactly what was hardcoded here before.
        shape = shape_for(conn)
        if supersede:
            conn.execute(
                f"DELETE FROM {shape.table} WHERE {shape.recipient} = ? AND seen = 0 "
                f"AND {ns_event_type} "
                f"AND {ns_card_id}",
                (recipient_id, event_type, card_id),
            )
        if msg_id:
            # EXACT dedupe when the producer told us which message this is.
            # The tuple below is the ONLY key available without it, and DM
            # timestamps are second-resolution, so that key is many-to-one BY
            # CONSTRUCTION — measured on the live store, two distinct durable
            # messages collapsed onto one notification and the second was
            # never delivered. `msg_id` makes the key exact, which is a
            # correctness fix in its own right, not just plumbing.
            dup = conn.execute(
                f"SELECT 1 FROM {shape.table} WHERE {shape.recipient} = ? "
                f"AND {null_safe_eq_for(conn, 'msg_id')} LIMIT 1",
                (recipient_id, msg_id),
            ).fetchone()
        else:
            dup = conn.execute(
                f"SELECT 1 FROM {shape.table} WHERE {shape.recipient} = ? "
                f"AND {ns_event_type} "
                f"AND {ns_card_id} "
                f"AND {null_safe_eq_for(conn, 'ts')} "
                f"AND {null_safe_eq_for(conn, 'actor')} LIMIT 1",
                (recipient_id, event_type, card_id, timestamp, actor),
            ).fetchone()
        if dup is not None:
            conn.commit()  # persist a supersede-only pass even when deduped
            return None
        record = notification_record(
            id=_generate_notification_id(),
            event_type=event_type,
            card_id=card_id,
            body=body,
            actor=actor,
            ts=timestamp,
            seen=False,
            msg_id=msg_id,
        )
        # THE COLUMN LIST IS DERIVED FROM THE RECORD, and the payload column
        # comes from the SHAPE, not from an assumption about which table this
        # is. That distinction is the whole bug: this function writes the
        # SQLite `inbox` table (no payload column) OR the canonical
        # `notifications` table (a payload column the export refuses a row
        # without), and it was writing the second as if it were the first. One
        # such payload-less row made all 3556 cards unreadable fleet-wide for 20
        # minutes on 2026-08-09, while `resolve_store` and `health` stayed green
        # because the store itself was fine.
        #
        # NOTE ON THE RECIPIENT KEY, which scitex-agent-container flagged as
        # unanswerable from outside this package: the payload deliberately does
        # NOT carry the recipient. It is a column on the row, under a name that
        # DIFFERS BY BACKEND (`recipient` vs `recipient_id`), so embedding it
        # would bake one backend's spelling into a backend-agnostic blob and
        # make the two indistinguishable until something read it back. That is
        # why `notification_columns` takes the column NAME as a parameter.
        columns, values = notification_columns(
            record,
            recipient_id=recipient_id,
            recipient_column=shape.recipient,
            payload_column=shape.payload,
        )
        placeholders = ", ".join(["?"] * len(columns))
        conn.execute(
            f"INSERT INTO {shape.table}({', '.join(columns)}) "
            f"VALUES({placeholders})",
            values,
        )
        conn.commit()
        return dict(record)


def poll_inbox(
    recipient_id: str,
    *,
    unseen_only: bool = True,
    mark_seen: bool = False,
    store: str | Path | None = None,
) -> list[dict]:
    """SQLite twin of :func:`scitex_cards._inbox.poll_inbox` — same contract.

    Returns ``recipient_id``'s notifications (unseen by default), oldest-first.
    When ``mark_seen`` is set, the RETURNED rows are flipped ``seen = 1`` and
    persisted (advancing the cursor). A falsy id or an empty inbox yields ``[]``.
    """
    if not recipient_id:
        return []
    db = inbox_target(store)
    if not mark_seen:
        # Read-only fast path. This is the hot poll — an indexed
        # (recipient, seen) scan, NOT a whole-store parse. _ensure_ready is a
        # cheap indexed meta-flag probe once migrated (no YAML, no writes).
        with open_connection(db) as conn:
            _ensure_ready(conn, store)
            shape = shape_for(conn)
            if unseen_only:
                rows = conn.execute(
                    f"SELECT * FROM {shape.table} WHERE {shape.recipient} = ? "
                    f"AND seen = 0 {shape.order()}",
                    (recipient_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM {shape.table} WHERE {shape.recipient} = ? "
                    f"{shape.order()}",
                    (recipient_id,),
                ).fetchall()
            return [_row_to_record(r) for r in rows]
    # mark_seen -> read-modify-write.
    with open_connection(db) as conn:
        _ensure_ready(conn, store)
        shape = shape_for(conn)
        if unseen_only:
            rows = conn.execute(
                f"SELECT * FROM {shape.table} WHERE {shape.recipient} = ? "
                f"AND seen = 0 {shape.order()}",
                (recipient_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM {shape.table} WHERE {shape.recipient} = ? "
                f"{shape.order()}",
                (recipient_id,),
            ).fetchall()
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE {shape.table} SET seen = 1 "
            f"WHERE {shape.recipient} = ? AND id IN ({placeholders})",
            (recipient_id, *ids),
        )
        conn.commit()
        # Return the selected records reflecting the new seen=True state.
        out = [_row_to_record(r) for r in rows]
        for rec in out:
            rec["seen"] = True
        return out


def ack(
    recipient_id: str,
    notification_ids: "list[str] | str",
    store: str | Path | None = None,
) -> list[str]:
    """SQLite twin of :func:`scitex_cards._inbox.ack` — same contract.

    Marks the given notification ids seen; returns the ids ACTUALLY flipped
    from unseen -> seen (so an already-seen / unknown id is a no-op for that
    id). A falsy recipient or empty id list is a no-op.
    """
    if not recipient_id:
        return []
    if isinstance(notification_ids, str):
        notification_ids = [notification_ids]
    wanted = [nid for nid in (notification_ids or []) if nid]
    if not wanted:
        return []
    db = inbox_target(store)
    placeholders = ",".join("?" for _ in wanted)
    with open_connection(db) as conn:
        _ensure_ready(conn, store)
        shape = shape_for(conn)
        # The ids that are currently UNSEEN among the wanted set — those are
        # the ones this call flips. Preserve arrival order.
        rows = conn.execute(
            f"SELECT id FROM {shape.table} WHERE {shape.recipient} = ? "
            f"AND seen = 0 AND id IN ({placeholders}) {shape.order()}",
            (recipient_id, *wanted),
        ).fetchall()
        flipped = [r["id"] for r in rows]
        if flipped:
            flip_placeholders = ",".join("?" for _ in flipped)
            conn.execute(
                f"UPDATE {shape.table} SET seen = 1 WHERE {shape.recipient} = ? "
                f"AND id IN ({flip_placeholders})",
                (recipient_id, *flipped),
            )
            conn.commit()
    return flipped


# --------------------------------------------------------------------------- #
# Migration: legacy embedded inboxes: section -> SQLite                       #
# --------------------------------------------------------------------------- #
# EXTRACTED to _inbox_migrate.py (this module had reached its size budget and
# the msg_id column had nowhere to go). Re-exported rather than moved-and-
# forgotten: `_ensure_ready` above calls `_migrate_into_conn`, the CLI imports
# `migrate_to_sqlite` / `gather_migratable_inboxes` / `info` from HERE, and the
# YAML-path tests import `_migrate_into_conn` by that name. A rename would have
# been a silent break in four places for no gain.
#
# The seam is real: everything over there runs ONCE per store, ever, while
# everything here runs on every poll, enqueue and ack.
from ._inbox_migrate import (  # noqa: E402,F401
    _migrate_into_conn,
    gather_migratable_inboxes,
    info,
    migrate_to_sqlite,
)

__all__ = [
    "ENV_INBOX_DB",
    "SCHEMA_VERSION",
    "ack",
    "enqueue",
    "gather_migratable_inboxes",
    "inbox_db_path",
    "info",
    "init_schema",
    "migrate_to_sqlite",
    "open_connection",
    "poll_inbox",
]

# EOF
