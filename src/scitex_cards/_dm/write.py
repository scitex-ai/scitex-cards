#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Writing DMs into the store — the path that makes the database the SSOT.

DESIGN: ``docs/design/dm-into-cards-db.md`` §3 / §4 and
``docs/design/dm-into-cards-db-migration.md`` M3.

WHAT CHANGES HERE, IN ONE SENTENCE: appending a DM stops being a 3 MB
read-modify-write of a JSON document and becomes a single ``INSERT``.

That is not a performance note, it is the correctness argument. Whole-document
rewrite is the amplifier behind every wipe this package has survived: a writer
that restates rows it did not author can lose rows it never saw. A writer that
only inserts its own row cannot — two concurrent appends both land, and a lost
update stops being expressible. Everything else (WAL, ``busy_timeout``) is the
store's existing machinery finally covering DMs too.

MIRRORS ``claude-code-telegrammer`` (operator instruction: do it the same way
as ccd), whose SQLite message log this follows deliberately:

* schema re-applied idempotently at EVERY open, ``CREATE ... IF NOT EXISTS``;
* ``INSERT OR IGNORE`` against a stable key as the dedup mechanism, with
  ``cursor.rowcount == 0`` meaning "already had it" rather than an error;
* WAL + an explicit per-connection ``busy_timeout`` on every handle;
* provenance columns stamped on every row;
* messages are NEVER deleted, and the only in-place updates are narrow state
  columns — ccd flips ``read_at``/``replied_at``, we set ``deleted_at``.

ONE DELIBERATE DEVIATION, stated because it is not an oversight: ccd models
read state as a nullable ``read_at`` column ON the message row. We use a
separate ``dm_receipts`` table. ccd can use a column because a ccd message has
exactly one reader; a scalar cannot say "Bob read it, Carol did not". Keeping
read state off the row is also what leaves ``dm_messages`` immutable, which is
what makes a cross-host merge a pure union.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .ids import (
    derived_member_event_id,
    is_pair_thread,
    new_group_thread_id,
    new_message_id,
    origin_host,
    pair_thread_id,
    peers_of_pair,
    resolve_dm_db,
    utc_now_iso,
)
from .storable import to_storable

# ---------------------------------------------------------------------------
# Row primitives live next door (`_dm_write_rows`). This module was 515 lines
# against a 512 cap, and the two halves were already separate: single-row
# writes below the line, composed DM verbs above it.
#
# RE-EXPORTED, NOT RELOCATED. 43 test files and every fleet agent import these
# names FROM HERE; each object below is the same one it always was, defined in
# the sibling module. Same contract as the `_model` / `_store_write` splits.
# ---------------------------------------------------------------------------
from .write_rows import (  # noqa: E402,F401
    _dumps,
    _open,
    ensure_thread,
    insert_message,
    insert_receipt,
    next_member_seq,
    next_seq,
    record_member_event,
)

# Shape-agnostic row access. psycopg's dict_row is a real dict and raises
# KeyError on a positional index, and since #693 open_db can hand this
# module a PostgreSQL connection. _schema_probe imports nothing from this
# package, so a module-level import here cannot cycle.
from .._schema_probe import _sole_value
from .._store_tx import begin_write_transaction


def append(
    thread_id: str,
    sender: str,
    body: str,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
    msg_id: str | None = None,
    ts: str | None = None,
    record: dict | None = None,
) -> dict:
    """Append one message to ``thread_id``. Returns the stored row.

    A pair thread is materialised on demand (with both peers joined); a group
    thread must already exist, because a group's membership cannot be inferred
    from its opaque id. The whole append runs in one write transaction (see
    :func:`~scitex_cards._store_tx.begin_write_transaction`) so the ``seq`` read
    and the insert cannot interleave with another appender — on either backend.
    """
    if not thread_id:
        raise ValueError("append requires a thread_id")
    if not sender:
        raise ValueError("append requires a sender")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("append requires a non-empty body")
    body, unstorable = to_storable(body)
    if unstorable:
        record = {**(record or {}), "unstorable_offsets": unstorable}
    stamp = ts or utc_now_iso()
    host = origin_host()
    message_id = msg_id or new_message_id()
    conn = _open(db, store)
    try:
        begin_write_transaction(conn)
        if is_pair_thread(thread_id):
            ensure_thread(conn, thread_id, kind="pair", created_at=stamp, host=host)
            for peer in peers_of_pair(thread_id):
                record_member_event(
                    conn, thread_id, peer, "join", ts=stamp, actor=sender, host=host
                )
        elif not conn.execute(
            "SELECT 1 FROM dm_threads WHERE id = ?", (thread_id,)
        ).fetchone():
            raise KeyError(f"unknown group thread {thread_id!r}: create it first")
        seq = next_seq(conn, thread_id)
        insert_message(
            conn,
            message_id=message_id,
            thread_id=thread_id,
            sender=sender,
            body=body,
            ts=stamp,
            seq=seq,
            host=host,
            record=record if record is not None else {},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "id": message_id,
        "thread_id": thread_id,
        "sender": sender,
        "body": body,
        "ts": stamp,
        "seq": seq,
        "origin_host": host,
    }


def append_pair(
    from_: str,
    to: str,
    body: str,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
    msg_id: str | None = None,
    ts: str | None = None,
    record: dict | None = None,
) -> dict:
    """Append a two-peer DM, addressed the legacy way. Returns the stored row."""
    return append(
        pair_thread_id(from_, to),
        from_,
        body,
        db=db,
        store=store,
        msg_id=msg_id,
        ts=ts,
        record=record,
    )


def create_group_thread(
    title: str,
    members: list[str],
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
    created_by: str | None = None,
) -> dict:
    """Mint a group thread with an OPAQUE id and join every founding member.

    The id is never derived from the member set: a derived key would change on
    every join, which would either orphan the history or force every message's
    ``thread_id`` to be rewritten — a delete-and-insert, which append-only
    forbids.
    """
    if not members:
        raise ValueError("create_group_thread requires at least one member")
    thread_id = new_group_thread_id()
    stamp = utc_now_iso()
    host = origin_host()
    conn = _open(db, store)
    try:
        begin_write_transaction(conn)
        ensure_thread(
            conn,
            thread_id,
            kind="group",
            title=title,
            created_at=stamp,
            created_by=created_by,
            host=host,
            record={"title": title, "members": list(members)},
        )
        for member in members:
            record_member_event(
                conn, thread_id, member, "join", ts=stamp, actor=created_by, host=host
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "id": thread_id,
        "kind": "group",
        "title": title,
        "created_at": stamp,
        "members": sorted(members),
    }


def _member_change(thread_id, who, action, db, store, actor):
    conn = _open(db, store)
    try:
        begin_write_transaction(conn)
        if not conn.execute(
            "SELECT 1 FROM dm_threads WHERE id = ?", (thread_id,)
        ).fetchone():
            raise KeyError(f"unknown thread {thread_id!r}")
        changed = record_member_event(conn, thread_id, who, action, actor=actor)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"thread_id": thread_id, "member": who, "action": action, "changed": changed}


def add_member(
    thread_id: str,
    who: str,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
    actor: str | None = None,
) -> dict:
    """Join ``who`` to ``thread_id``. The thread id does NOT move."""
    return _member_change(thread_id, who, "join", db, store, actor)


def remove_member(
    thread_id: str,
    who: str,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
    actor: str | None = None,
) -> dict:
    """Record that ``who`` LEFT ``thread_id``. Nothing is removed."""
    return _member_change(thread_id, who, "leave", db, store, actor)


def _known_messages(conn: sqlite3.Connection, message_ids: list[str]) -> list[str]:
    """Those of ``message_ids`` the store actually holds, order preserved."""
    placeholders = ", ".join("?" for _ in message_ids)
    rows = conn.execute(
        f"SELECT id FROM dm_messages WHERE id IN ({placeholders})", list(message_ids)
    ).fetchall()
    present = {r["id"] for r in rows}
    return [m for m in message_ids if m in present]


def mark_read(
    message_ids: list[str],
    reader: str,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
    source: str = "live",
) -> int:
    """Record ``reader``'s receipt for each id. Returns how many were new.

    Idempotent: a second call inserts nothing and returns 0, because the
    receipt's primary key is ``(message_id, reader)``.

    Ids the store does not hold are SKIPPED rather than attempted. A receipt
    carries a foreign key onto ``dm_messages`` and SQLite's ``OR IGNORE`` does
    NOT cover foreign-key violations — it raises. During the migration window
    the sidecar still holds messages the backfill has not carried across yet,
    so without this filter every read of an old message would raise. Skipping
    loses nothing recoverable: the message's own ``read: true`` is still in the
    sidecar and the backfill turns it into a receipt when the message lands.
    """
    if not message_ids:
        return 0
    stamp = utc_now_iso()
    host = origin_host()
    inserted = 0
    conn = _open(db, store)
    try:
        begin_write_transaction(conn)
        for message_id in _known_messages(conn, message_ids):
            inserted += int(
                insert_receipt(
                    conn, message_id, reader, read_at=stamp, host=host, source=source
                )
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return inserted


def tombstone(
    message_id: str,
    *,
    by: str | None = None,
    db: str | Path | None = None,
    store: str | Path | None = None,
) -> bool:
    """Mark a message deleted IN PLACE. The row, body and receipts survive.

    Deleting a DM costs zero rows, exactly as deleting a card does. Setting an
    already-set ``deleted_at`` is a no-op, so the FIRST deletion time is the
    one that is kept — which is also what makes the tombstone commutative
    under a cross-host merge.
    """
    conn = _open(db, store)
    try:
        cur = conn.execute(
            "UPDATE dm_messages SET deleted_at = ?, deleted_by = ?"
            " WHERE id = ? AND deleted_at IS NULL",
            (utc_now_iso(), by, message_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


__all__ = [
    "add_member",
    "append",
    "append_pair",
    "create_group_thread",
    "ensure_thread",
    "insert_message",
    "insert_receipt",
    "mark_read",
    "next_member_seq",
    "next_seq",
    "record_member_event",
    "remove_member",
    "tombstone",
]

# EOF
