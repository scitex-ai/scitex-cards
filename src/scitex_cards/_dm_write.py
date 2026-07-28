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

from ._dm_ids import (
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


def _open(db, store) -> sqlite3.Connection:
    from ._db import open_db

    return open_db(resolve_dm_db(db, store=store))


def _dumps(payload: dict) -> str:
    """Serialise a verbatim payload. Key ORDER is preserved, never sorted."""
    return json.dumps(payload, ensure_ascii=False)


def ensure_thread(
    conn: sqlite3.Connection,
    thread_id: str,
    *,
    kind: str = "pair",
    title: str | None = None,
    created_at: str | None = None,
    created_by: str | None = None,
    host: str | None = None,
    record: dict | None = None,
) -> bool:
    """``INSERT OR IGNORE`` the thread row. True iff this call created it.

    Idempotent by primary key, so it is safe on every append — which is what
    lets a pair thread come into existence from its first message without a
    separate "create thread" step, exactly as the sidecar's
    ``setdefault(key, [])`` did.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO dm_threads"
        "(id, kind, title, created_at, created_by, origin_host, record_json)"
        " VALUES(?, ?, ?, ?, ?, ?, ?)",
        (
            thread_id,
            kind,
            title,
            created_at or utc_now_iso(),
            created_by,
            host or origin_host(),
            _dumps(record if record is not None else {}),
        ),
    )
    return cur.rowcount > 0


def record_member_event(
    conn: sqlite3.Connection,
    thread_id: str,
    member: str,
    action: str,
    *,
    ts: str | None = None,
    actor: str | None = None,
    host: str | None = None,
    event_id: str | None = None,
) -> bool:
    """Append one membership event. True iff it was new.

    ``action`` is ``'join'`` or ``'leave'``. Removing a member is a LEAVE ROW,
    never a deleted one: the fold then answers "not a member now" while the
    record of having been one survives, which is what keeps an old message's
    audience answerable after the fact.

    THE ``seq`` IS NOT DECORATION. Timestamps here are second-resolution, so a
    join and a leave in the same second are indistinguishable by ``ts`` and the
    fold's tie-break fell through to the content-hash id — a coin flip that let
    a departed member keep receiving messages in 17 of 60 measured runs. This
    counter is what makes "the latest event" a fact rather than a hash race.
    """
    stamp = ts or utc_now_iso()
    cur = conn.execute(
        "INSERT OR IGNORE INTO dm_thread_member_events"
        "(id, thread_id, member, action, ts, seq, actor, origin_host,"
        " record_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id or derived_member_event_id(thread_id, member, action, stamp),
            thread_id,
            member,
            action,
            stamp,
            next_member_seq(conn, thread_id, member),
            actor,
            host or origin_host(),
            _dumps({}),
        ),
    )
    return cur.rowcount > 0


def next_member_seq(conn: sqlite3.Connection, thread_id: str, member: str) -> int:
    """``1 + MAX(seq)`` for this ``(thread, member)`` — the membership counter.

    Read inside the caller's ``BEGIN IMMEDIATE``, so two writers cannot observe
    the same maximum. Across hosts two offline writers legitimately can, which
    is why the fold keeps ``ts, origin_host, id`` behind it to stay total.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM dm_thread_member_events"
        " WHERE thread_id = ? AND member = ?",
        (thread_id, member),
    ).fetchone()
    return int(row[0]) + 1


def next_seq(conn: sqlite3.Connection, thread_id: str) -> int:
    """``1 + MAX(seq)`` for the thread — the per-thread logical counter.

    Read inside the caller's ``BEGIN IMMEDIATE`` so two appenders cannot both
    observe the same maximum. A collision is not a correctness bug (``seq`` is
    a SORT HINT and the order is total without it) but it would reorder a
    conversation, which users notice.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM dm_messages WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()
    return int(row[0]) + 1


def insert_message(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    thread_id: str,
    sender: str,
    body: str,
    ts: str,
    seq: int,
    host: str,
    record: dict,
) -> bool:
    """``INSERT OR IGNORE`` one message row. True iff it was new."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO dm_messages"
        "(id, thread_id, sender, body, ts, seq, origin_host, record_json)"
        " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (message_id, thread_id, sender, body, ts, seq, host, _dumps(record)),
    )
    return cur.rowcount > 0


def insert_receipt(
    conn: sqlite3.Connection,
    message_id: str,
    reader: str,
    *,
    read_at: str | None = None,
    host: str | None = None,
    source: str = "live",
) -> bool:
    """``INSERT OR IGNORE`` a read receipt. True iff it was new.

    A receipt is MONOTONE: it is never removed and "unread again" is not
    expressible. That is deliberate — it is what makes the receipts table
    merge across hosts by union with no arbitration.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO dm_receipts"
        "(message_id, reader, read_at, origin_host, source) VALUES(?, ?, ?, ?, ?)",
        (message_id, reader, read_at or utc_now_iso(), host or origin_host(), source),
    )
    return cur.rowcount > 0


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
    from its opaque id. The whole append runs in one ``BEGIN IMMEDIATE`` so
    the ``seq`` read and the insert cannot interleave with another appender.
    """
    if not thread_id:
        raise ValueError("append requires a thread_id")
    if not sender:
        raise ValueError("append requires a sender")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("append requires a non-empty body")
    stamp = ts or utc_now_iso()
    host = origin_host()
    message_id = msg_id or new_message_id()
    conn = _open(db, store)
    try:
        conn.execute("BEGIN IMMEDIATE")
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
        conn.execute("BEGIN IMMEDIATE")
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
        conn.execute("BEGIN IMMEDIATE")
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
        conn.execute("BEGIN IMMEDIATE")
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
