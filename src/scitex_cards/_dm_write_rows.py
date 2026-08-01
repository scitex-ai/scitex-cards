#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-row DM write primitives — open a connection, write ONE row.

Split out of :mod:`scitex_cards._dm_write` (515 lines, cap 512). That file holds
two layers with a one-way arrow between them: these primitives, and the public
DM verbs that compose them. The verbs import these; nothing here imports a verb.

THE PUBLIC IMPORT SURFACE DOES NOT MOVE. ``_dm_write`` re-exports every name
below, so ``from scitex_cards._dm_write import insert_message`` keeps working —
same contract as the ``_model`` and ``_store_write`` splits already in the
package.

EVERY WRITE HERE IS ``ON CONFLICT DO NOTHING``, NOT ``INSERT OR IGNORE``, and
that is a portability fix rather than a style choice. ``INSERT OR IGNORE`` is
SQLite-only syntax; PostgreSQL rejects it outright with ``syntax error at or
near "OR"``. The upsert form is understood by both (SQLite since 3.24), which
keeps the package's "carry exactly ONE way in the source" doctrine intact
instead of adding a dialect-translation layer.

The dedup contract is unchanged and is what callers actually depend on:
``cursor.rowcount == 0`` means "already had it", not an error. Both forms skip
the row on a key conflict and report zero affected rows, on both backends.

WHY THESE FOUR ARE ``DO NOTHING`` AND NOT ``DO UPDATE``: every table written
here carries a ``BEFORE DELETE ... RAISE(ABORT)`` append-only guard
(``dm_threads``, ``dm_thread_member_events``, ``dm_messages``, ``dm_receipts``).
``INSERT OR REPLACE`` would have been a DELETE+INSERT and the guard would have
refused it; ``IGNORE`` never deletes, which is precisely why these tables were
written with it. Preserving "skip, never overwrite" keeps the immutability the
guards exist to enforce — a receipt is monotone, a message is immutable, and a
membership event is a fact that happened.
"""

from __future__ import annotations

import json
import sqlite3

# Shape-agnostic row access. psycopg's dict_row is a real dict and raises
# KeyError on a positional index, and since #693 open_db can hand this
# module a PostgreSQL connection. _schema_probe imports nothing from this
# package, so a module-level import here cannot cycle.
from ._dm_ids import (
    derived_member_event_id,
    origin_host,
    resolve_dm_db,
    utc_now_iso,
)
from ._schema_probe import _sole_value


def _open(db, store) -> sqlite3.Connection:
    """Open the DM store for WRITING, refusing a retired one.

    THE REFUSAL IS HERE, NOT IN ``open_db``. Card writes reach the retirement
    guard only incidentally — they are read-modify-write, so they pass through
    the canonical read, which checks. DM writes have their own path and checked
    NOTHING. Measured 2026-07-31 during the cutover: a peer's card write was
    refused at 14:16 while my own DM write LANDED in the retired store at
    14:26. Retirement was a fence for one path and a signpost for the other.

    ``open_db`` would be the tempting place and is the WRONG one: the canonical
    read and the export/snapshot paths also open through it, and a retired
    store must stay READABLE. Archaeology is what makes retirement survivable —
    recovering one means reading it. So the refusal belongs at the point of
    WRITE, and this function is the DM write funnel: all five mutating verbs
    (append, append_pair, create_group_thread, mark_read, tombstone) open here
    and nothing else does.

    Reuses ``_refuse_if_retired_on`` rather than re-deriving the condition. Its
    docstring already states why: both backends must run ONE definition of "is
    this store retired", because duplicating it per caller is how the answers
    drift — and the entire point is that exactly one store can be current.
    """
    from ._db import open_db
    from ._store_canonical_read import _refuse_if_retired_on

    conn = open_db(resolve_dm_db(db, store=store))
    try:
        _refuse_if_retired_on(conn)
    except BaseException:
        # Do not leak the handle when we refuse. The caller never sees this
        # connection, so nobody else can close it.
        conn.close()
        raise
    return conn


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
    """Insert the thread row if absent. True iff this call created it.

    Idempotent by primary key, so it is safe on every append — which is what
    lets a pair thread come into existence from its first message without a
    separate "create thread" step, exactly as the sidecar's
    ``setdefault(key, [])`` did.
    """
    cur = conn.execute(
        "INSERT INTO dm_threads"
        "(id, kind, title, created_at, created_by, origin_host, record_json)"
        " VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
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
        "INSERT INTO dm_thread_member_events"
        "(id, thread_id, member, action, ts, seq, actor, origin_host,"
        " record_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT DO NOTHING",
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
    return int(_sole_value(row)) + 1


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
    return int(_sole_value(row)) + 1


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
    """Insert one message row if absent. True iff it was new."""
    cur = conn.execute(
        "INSERT INTO dm_messages"
        "(id, thread_id, sender, body, ts, seq, origin_host, record_json)"
        " VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
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
    """Insert a read receipt if absent. True iff it was new.

    A receipt is MONOTONE: it is never removed and "unread again" is not
    expressible. That is deliberate — it is what makes the receipts table
    merge across hosts by union with no arbitration.
    """
    cur = conn.execute(
        "INSERT INTO dm_receipts"
        "(message_id, reader, read_at, origin_host, source) VALUES(?, ?, ?, ?, ?)"
        " ON CONFLICT DO NOTHING",
        (message_id, reader, read_at or utc_now_iso(), host or origin_host(), source),
    )
    return cur.rowcount > 0


__all__ = [
    "ensure_thread",
    "insert_message",
    "insert_receipt",
    "next_member_seq",
    "next_seq",
    "record_member_event",
]

# EOF
