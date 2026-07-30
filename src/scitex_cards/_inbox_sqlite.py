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

logger = logging.getLogger(__name__)

#: Env override for the inbox DB path (full path to the ``.db`` file). Default
#: is ``<store_dir>/runtime/todo.db`` (see :func:`inbox_db_path`). Mirrors the
#: ``SCITEX_TODO_INDEX_PATH`` override on :mod:`scitex_cards._index`.
ENV_INBOX_DB = "SCITEX_TODO_INBOX_DB"

#: Runtime-DB filename. ``todo`` is this package's short name (constitution:
#: ``<proj-root>/.scitex/<pkg-short>/runtime/<pkg-short>.db``).
_DB_FILENAME = "todo.db"

#: Schema version. Bump when the column set / indexes change.
SCHEMA_VERSION = 1

#: ``meta`` key set ONCE after the YAML ``inboxes:`` records have been copied
#: into this DB (the lazy auto-migration guard). Its presence is the cheap,
#: indexed PK read that lets the steady-state hot poll path skip YAML entirely.
_MIGRATED_FLAG = "migrated_from_yaml"


def inbox_db_path(store: str | Path | None = None) -> Path:
    """Resolved on-disk path for the inbox SQLite DB.

    ``SCITEX_TODO_INBOX_DB`` wins outright; otherwise the DB lives at
    ``runtime_dir(store)/todo.db`` — the runtime dir tracks whichever scope the
    task store resolved to, so a per-test ``store=`` isolates its own DB.
    """
    override = os.environ.get(ENV_INBOX_DB)
    if override:
        return Path(override).expanduser()
    from ._paths import runtime_dir

    return runtime_dir(store, create=True) / _DB_FILENAME


@contextmanager
def open_connection(path: Optional[Path] = None):
    """Open the inbox DB (WAL, autocommit isolation, ``Row`` factory).

    Caller-managed: closes on context exit. Creates the parent dir if missing
    (first-run friendly). Mirrors :func:`scitex_cards._index.open_connection`.
    """
    target = Path(path) if path is not None else inbox_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def _ensure_msg_id(conn: sqlite3.Connection) -> None:
    """Add ``inbox.msg_id`` if this DB predates it. Idempotent + race-safe.

    ~21 agents share one ``todo.db``, so two can reach the ``ALTER`` at the
    same instant and the loser sees ``duplicate column name``. That is the
    winner having done our job, not a failure — swallowing anything broader
    would let a real schema fault masquerade as a race.

    Nullable on purpose. A row enqueued before the id was carried has no
    message id, and "no message id" is the true answer for it; a backfill
    would have to invent one from the lossy ``(card_id, ts, actor)`` join that
    ``_dm_receipt_state`` measured collapsing two distinct messages into one.
    """
    try:
        have = {row[1] for row in conn.execute("PRAGMA table_info(inbox)")}
    except sqlite3.Error:
        return
    if not have or "msg_id" in have:
        return
    try:
        conn.execute("ALTER TABLE inbox ADD COLUMN msg_id TEXT")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the ``inbox`` table + its indexes idempotently.

    Columns mirror the record dict the YAML path stores
    (``id / event_type / card_id / body / actor / ts / seen``) plus the
    ``recipient`` inbox key. ``rowid`` (implicit) preserves append order — a
    poll returns oldest-first by ``ORDER BY rowid``. The composite index on
    ``(recipient, seen)`` makes a single recipient's UNSEEN lookup — the hot
    poll path — an indexed scan rather than a full-table read.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inbox (
            id TEXT PRIMARY KEY,
            recipient TEXT NOT NULL,
            event_type TEXT,
            card_id TEXT,
            body TEXT,
            actor TEXT,
            ts TEXT,
            seen INTEGER NOT NULL DEFAULT 0,
            msg_id TEXT
        )
        """
    )
    _ensure_msg_id(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_inbox_recipient_seen ON inbox(recipient, seen)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


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


def _is_migrated(conn: sqlite3.Connection) -> bool:
    """True once the YAML ``inboxes:`` records have been copied into this DB.

    A single indexed PRIMARY-KEY probe of the ``meta`` table — the cheap check
    that lets the steady-state hot poll path skip the YAML read entirely.
    """
    row = conn.execute(
        "SELECT 1 FROM meta WHERE key = ? LIMIT 1", (_MIGRATED_FLAG,)
    ).fetchone()
    return row is not None


def _ensure_ready(conn: sqlite3.Connection, store: str | Path | None) -> None:
    """Per-connection readiness: ensure the schema, then lazily migrate
    pre-existing file-backed inbox records into SQLite EXACTLY ONCE.

    Guarded by the ``migrated_from_yaml`` meta flag: the first access on a
    fresh DB performs the one-time copy + sets the flag; every later access
    is a cheap flag probe. Concurrency-safe across the ~21 agents sharing one
    ``todo.db`` — idempotent (``INSERT OR IGNORE`` on the ``id`` PK); the
    flag is set even when there's nothing to copy, so a fresh store converges.
    """
    init_schema(conn)
    if _is_migrated(conn):
        return
    from ._inbox import _utc_now_iso

    _migrate_into_conn(conn, store)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
        (_MIGRATED_FLAG, _utc_now_iso()),
    )
    conn.commit()


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
    actor)`` (NULL-safe via the ``IS`` operator so ``actor=None`` dedups
    correctly). When ``supersede`` is set, every EXISTING UNSEEN row matching
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
    with open_connection(inbox_db_path(store)) as conn:
        _ensure_ready(conn, store)
        if supersede:
            conn.execute(
                "DELETE FROM inbox WHERE recipient = ? AND seen = 0 "
                "AND event_type IS ? AND card_id IS ?",
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
                "SELECT 1 FROM inbox WHERE recipient = ? AND msg_id IS ? LIMIT 1",
                (recipient_id, msg_id),
            ).fetchone()
        else:
            dup = conn.execute(
                "SELECT 1 FROM inbox WHERE recipient = ? AND event_type IS ? "
                "AND card_id IS ? AND ts IS ? AND actor IS ? LIMIT 1",
                (recipient_id, event_type, card_id, timestamp, actor),
            ).fetchone()
        if dup is not None:
            conn.commit()  # persist a supersede-only pass even when deduped
            return None
        record = {
            "id": _generate_notification_id(),
            "event_type": event_type,
            "card_id": card_id,
            "body": body,
            "actor": actor,
            "ts": timestamp,
            "seen": False,
            "msg_id": msg_id,
        }
        conn.execute(
            "INSERT INTO inbox(id, recipient, event_type, card_id, body, "
            "actor, ts, seen, msg_id) VALUES(?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                record["id"],
                recipient_id,
                event_type,
                card_id,
                body,
                actor,
                timestamp,
                msg_id,
            ),
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
    db = inbox_db_path(store)
    if not mark_seen:
        # Read-only fast path. This is the hot poll — an indexed
        # (recipient, seen) scan, NOT a whole-store parse. _ensure_ready is a
        # cheap indexed meta-flag probe once migrated (no YAML, no writes).
        with open_connection(db) as conn:
            _ensure_ready(conn, store)
            if unseen_only:
                rows = conn.execute(
                    "SELECT * FROM inbox WHERE recipient = ? AND seen = 0 "
                    "ORDER BY rowid",
                    (recipient_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM inbox WHERE recipient = ? ORDER BY rowid",
                    (recipient_id,),
                ).fetchall()
            return [_row_to_record(r) for r in rows]
    # mark_seen -> read-modify-write.
    with open_connection(db) as conn:
        _ensure_ready(conn, store)
        if unseen_only:
            rows = conn.execute(
                "SELECT * FROM inbox WHERE recipient = ? AND seen = 0 ORDER BY rowid",
                (recipient_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM inbox WHERE recipient = ? ORDER BY rowid",
                (recipient_id,),
            ).fetchall()
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE inbox SET seen = 1 WHERE recipient = ? AND id IN ({placeholders})",
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
    db = inbox_db_path(store)
    placeholders = ",".join("?" for _ in wanted)
    with open_connection(db) as conn:
        _ensure_ready(conn, store)
        # The ids that are currently UNSEEN among the wanted set — those are
        # the ones this call flips. Preserve append order (rowid).
        rows = conn.execute(
            f"SELECT id FROM inbox WHERE recipient = ? AND seen = 0 "
            f"AND id IN ({placeholders}) ORDER BY rowid",
            (recipient_id, *wanted),
        ).fetchall()
        flipped = [r["id"] for r in rows]
        if flipped:
            flip_placeholders = ",".join("?" for _ in flipped)
            conn.execute(
                f"UPDATE inbox SET seen = 1 WHERE recipient = ? "
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
