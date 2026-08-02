#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where the rail's rows physically live, and the DDL that puts them there.

Split out of :mod:`scitex_cards._inbox_sqlite` because the two halves have
OPPOSITE lifetimes, and keeping them in one file made that invisible.

Everything here owns a FILE: the ``todo.db`` path, the ``CREATE TABLE`` for
``inbox``, the ``ALTER`` that adds ``msg_id`` to a DB predating it, and the
one-time copy of the legacy YAML ``inboxes:`` records. All of it is SQLite-and-
file specific, and all of it is DELETED when the rail finishes moving onto the
canonical store. The query half -- ``enqueue`` / ``poll_inbox`` / ``ack`` --
survives that move; it already reads its table, recipient column and ordering
from :func:`scitex_cards._inbox_shape.shape_for` and its NULL-safe operator
from :func:`scitex_cards._sql_null_safe.null_safe_eq_for`, so it is backend-
agnostic already.

Cutting on that line makes the retirement a file deletion rather than surgery
inside a live module.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = [
    "ENV_INBOX_DB",
    "SCHEMA_VERSION",
    "inbox_db_path",
    "init_schema",
    "open_connection",
]

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
    from ._paths import runtime_dir  # noqa: PLC0415 -- import cycle

    return runtime_dir(store, create=True) / _DB_FILENAME


@contextmanager
def open_connection(target: "Optional[Path | str]" = None):
    """Open the rail's store through the ONE function every other opener uses.

    Caller-managed: closes on context exit.

    WHY THIS STOPPED HAND-ROLLING ``sqlite3.connect``. The rail was the only
    part of this package opening its own database directly, which made it the
    only part that could not be handed a PostgreSQL target. A DSN reaching
    ``Path(...)`` does not raise -- it yields a plausible relative path, and
    ``mkdir`` + ``sqlite3.connect`` then MANUFACTURE a SQLite file named after
    the DSN, one that accepts writes and answers queries while the real server
    sits untouched. That file was created and observed while testing #682.
    :func:`scitex_cards._db.connect` dispatches on the target BEFORE any path
    handling for exactly that reason, so routing through it is what makes the
    rail's move to the canonical store expressible at all.

    Three things come along, none of which change what this returns for a file:

    * the S0 PRAGMAs rather than ``journal_mode`` alone -- notably
      ``busy_timeout=300000``, which matters on a store this many writers share
    * the min-client-version gate, a no-op here because ``todo.db`` stamps no
      floor (it carries a ``meta`` table, not ``schema_meta``)
    * ``row_factory = sqlite3.Row``, which this function already set

    ``target`` still defaults to :func:`inbox_db_path`, so this step moves no
    rows: same file, same contents, opened through the shared door.
    """
    from ._db import connect as _connect  # noqa: PLC0415 -- import cycle

    resolved = target if target is not None else inbox_db_path()
    conn = _connect(resolved)
    try:
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
    from ._inbox import _utc_now_iso  # noqa: PLC0415 -- import cycle
    from ._inbox_migrate import _migrate_into_conn  # noqa: PLC0415 -- import cycle

    _migrate_into_conn(conn, store)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
        (_MIGRATED_FLAG, _utc_now_iso()),
    )
    conn.commit()


# EOF
