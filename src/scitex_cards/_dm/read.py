#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reading DMs out of the store: membership fold, ordering, unread.

DESIGN: ``docs/design/dm-into-cards-db.md`` §3.1 (recipients are derived),
§3.2 (receipts), §3.6 (ordering).

TWO IDEAS DO ALL THE WORK HERE.

**Recipients are DERIVED, not stored.** ``dm_messages`` records who SENT;
``dm_thread_member_events`` records who can SEE. That decoupling is the entire
group-DM unlock and it costs a pair thread nothing — "the other member" is a
one-row lookup. It also replaces the sidecar's ``list_threads`` full rescan of
every record of every thread (~0.7 s on the live 3 MB file) with an indexed
query.

**Membership is a FOLD over an append-only event log**, never a mutable list.
"Who is in this thread now" is the latest event per ``(thread_id, member)``;
"who was ever in it" survives forever. A ``leave`` is a new row, so across
hosts the log merges by union with no arbitration — where a mutable
``left_at`` column would have needed last-write-wins.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only -- no driver is imported at runtime
    from .._backend_connect import StoreConnection

# Shape-agnostic row access. psycopg's dict_row is a real dict and raises
# KeyError on a positional index, and since #693 open_db can hand this
# module a PostgreSQL connection. _schema_probe imports nothing from this
# package, so a module-level import here cannot cycle.
from .._schema_probe import _sole_value

import json
from pathlib import Path

#: Current membership: the LATEST event per ``(thread_id, member)``.
#:
#: Written as a correlated subquery rather than a window function on purpose —
#: it is the formulation that reads the same way the rule is stated ("the last
#: thing that happened to this member in this thread") and it does not depend
#: on the engine carrying window functions.
#:
#: ``seq`` LEADS THE ORDER, AND IT HAD TO. Ordering by ``ts`` first looks
#: right and is not: timestamps here are SECOND-resolution, so creating a
#: thread and removing a member in the same second gives the join and the
#: leave an identical ``ts``, and the tie fell through to ``id`` — a content
#: hash, i.e. a coin flip. MEASURED before the fix: a departed member still
#: received new messages in 17 of 60 runs. A leave that silently does not take
#: effect is a disclosure bug, not a cosmetic one, and it was invisible
#: locally (the hash happened to sort the right way) until CI ran it.
#:
#: ``seq`` is the per-``(thread, member)`` counter minted under the writer's
#: transaction, so within one database it is exactly the order the events
#: happened. ``ts, origin_host, id`` follow to keep the order TOTAL across
#: hosts, where two offline writers can legitimately mint the same ``seq``.
CURRENT_MEMBERS_SQL = """
SELECT e.thread_id AS thread_id,
       e.member    AS member,
       (SELECT x.action FROM dm_thread_member_events x
         WHERE x.thread_id = e.thread_id AND x.member = e.member
         ORDER BY x.seq DESC, x.ts DESC, x.origin_host DESC, x.id DESC
         LIMIT 1) AS current_action
  FROM dm_thread_member_events e
 GROUP BY e.thread_id, e.member
"""

#: The TOTAL order every host computes for a thread.
#:
#: ``seq`` is the per-thread logical counter and reproduces today's append
#: order within one database. ``ts`` is wall clock and is DISPLAY ONLY — it
#: never breaks a tie alone, because two hosts' clocks skew. ``origin_host``
#: and ``id`` are what make the sort total and deterministic, so two hosts
#: holding the same row set render the same conversation.
MESSAGE_ORDER_SQL = "ORDER BY seq, ts, origin_host, id"


def row_to_message(row: Mapping[str, Any]) -> dict:
    """One ``dm_messages`` row as a plain dict, payload merged back in.

    ``record_json`` is the VERBATIM payload the source carried (the v2/v3
    exactness rule: typed columns are the INDEX, the JSON is the PAYLOAD).
    Merging it under the typed columns means a caller sees every key the
    original record had — including ones this schema never modelled — while
    the typed columns stay authoritative for the fields the store owns.
    """
    typed = {k: row[k] for k in row.keys()}
    payload = {}
    raw = typed.get("record_json")
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                payload = loaded
        except (TypeError, ValueError):
            payload = {}
    merged = dict(payload)
    merged.update({k: v for k, v in typed.items() if k != "record_json"})
    return merged


def current_members(conn: StoreConnection, thread_id: str) -> list[str]:
    """Peers currently in ``thread_id``, sorted. Folds the event log."""
    rows = conn.execute(
        f"SELECT member FROM ({CURRENT_MEMBERS_SQL}) "
        "WHERE thread_id = ? AND current_action = 'join' ORDER BY member",
        (thread_id,),
    ).fetchall()
    return [r["member"] for r in rows]


def messages_in_conn(
    conn: StoreConnection, thread_id: str, *, include_deleted: bool = False
) -> list[dict]:
    """Every live message of ``thread_id``, in the total order.

    Tombstoned messages are HIDDEN, not removed — exactly as
    ``_task._is_tombstoned`` hides a deleted card. ``include_deleted`` exists
    for audit and for the export rail, which must see the whole row set.
    """
    clause = "" if include_deleted else " AND deleted_at IS NULL"
    rows = conn.execute(
        f"SELECT * FROM dm_messages WHERE thread_id = ?{clause} {MESSAGE_ORDER_SQL}",
        (thread_id,),
    ).fetchall()
    return [row_to_message(r) for r in rows]


def unread_for_conn(
    conn: StoreConnection, reader: str, *, thread_id: str | None = None
) -> list[dict]:
    """Messages ``reader`` can see, did not send, and has no receipt for.

    The four conditions are the whole definition of unread once a thread can
    have three members: membership (may see it), authorship (own messages are
    not unread), the tombstone (a deleted message is not pending), and the
    absence of a receipt. None of them is a mutable flag on the message row,
    which is what keeps ``dm_messages`` immutable and the merge a pure union.
    """
    params: list[object] = [reader, reader, reader]
    thread_clause = ""
    if thread_id is not None:
        thread_clause = " AND m.thread_id = ?"
        params.append(thread_id)
    rows = conn.execute(
        "SELECT m.* FROM dm_messages m "
        f"JOIN ({CURRENT_MEMBERS_SQL}) mem "
        "  ON mem.thread_id = m.thread_id AND mem.member = ? "
        "WHERE mem.current_action = 'join' "
        "  AND m.sender != ? "
        "  AND m.deleted_at IS NULL "
        "  AND NOT EXISTS (SELECT 1 FROM dm_receipts r "
        "                   WHERE r.message_id = m.id AND r.reader = ?)"
        f"{thread_clause} "
        "ORDER BY m.seq, m.ts, m.origin_host, m.id",
        params,
    ).fetchall()
    return [row_to_message(r) for r in rows]


def _open(db, store):
    from .._db import open_db
    from .ids import resolve_dm_db

    return open_db(resolve_dm_db(db, store=store))


def messages_in(
    thread_id: str,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
    include_deleted: bool = False,
) -> list[dict]:
    """Public read: every live message of ``thread_id``, in the total order."""
    conn = _open(db, store)
    try:
        return messages_in_conn(conn, thread_id, include_deleted=include_deleted)
    finally:
        conn.close()


def unread_for(
    reader: str,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
    thread_id: str | None = None,
) -> list[dict]:
    """Public read: everything ``reader`` has not seen yet."""
    conn = _open(db, store)
    try:
        return unread_for_conn(conn, reader, thread_id=thread_id)
    finally:
        conn.close()


def list_members(
    thread_id: str,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
) -> list[str]:
    """Public read: who is in ``thread_id`` right now."""
    conn = _open(db, store)
    try:
        return current_members(conn, thread_id)
    finally:
        conn.close()


def threads_summary_conn(conn: StoreConnection, reader: str) -> dict[str, dict]:
    """Per-thread summary FROM THE STORE, shaped like the sidecar's version.

    Returns ``{thread_id: {"peers": (a, b), "last": <message|None>,
    "count": N, "unread": {reader: n}}}`` — the exact shape
    ``_threads.list_threads`` returns, so the board's DM view is a
    substitution rather than a rewrite.

    WHY THIS EXISTS. ``_threads.list_threads`` reads ``threads.json``, A
    PER-HOST FILE, and reads nothing else. The board therefore showed only the
    threads of agents running on the SAME MACHINE as the board. Measured
    2026-08-09 on the operator's laptop: its sidecar had
    ``dm:operator::scitex-agent-container`` live at 12:33 with 386 messages
    (that agent runs on the laptop) while ``dm:operator::scitex-cards`` sat at
    2026-08-02 — the last time THAT agent ran laptop-side. Five agents on
    scitex-compute-04 were invisible to him entirely, writing a different
    ``threads.json`` on a different host. The store meanwhile held all 4150
    messages. Nothing was lost; the display was host-local.

    NO WINDOW FUNCTIONS AND NO NEW AGGREGATION SQL, deliberately. One flat
    SELECT and a fold in Python:

      * the ORDER is the package's declared total order, applied with the same
        key ``MESSAGE_ORDER_SQL`` names (``seq, ts, origin_host, id``). Using
        ``MAX(ts)`` instead would silently disagree with what the thread pane
        shows when opened, because ts alone is not how this package sorts.
      * UNREAD IS NOT REIMPLEMENTED. It comes from :func:`unread_for_conn`,
        whose docstring records why it is subtle — "the four conditions are the
        whole definition of unread once a thread can have three members".
        A second definition living in a summary query is a second thing to keep
        in step, and it would drift toward the easy wrong answer (count rows
        where ``read`` is false).
      * TOMBSTONES ARE HIDDEN, matching :func:`messages_in_conn`, so a deleted
        message can never become the "last" one.

    A fold is affordable here and a join is not worth its risk: the whole store
    is ~4150 rows, and the dialect differences this package has already been
    bitten by are exactly where a hand-written window query would break.
    """
    rows = conn.execute(
        "SELECT * FROM dm_messages WHERE deleted_at IS NULL"
    ).fetchall()

    by_thread: dict[str, list[dict]] = {}
    for row in rows:
        message = row_to_message(row)
        by_thread.setdefault(message["thread_id"], []).append(message)

    unread_counts: dict[str, int] = {}
    for message in unread_for_conn(conn, reader):
        thread_id = message["thread_id"]
        unread_counts[thread_id] = unread_counts.get(thread_id, 0) + 1

    summary: dict[str, dict] = {}
    for thread_id, messages in by_thread.items():
        messages.sort(
            key=lambda m: (
                m.get("seq") or 0,
                m.get("ts") or "",
                m.get("origin_host") or "",
                m.get("id") or "",
            )
        )
        summary[thread_id] = {
            "peers": _peers_of(thread_id),
            "last": messages[-1] if messages else None,
            "count": len(messages),
            "unread": {reader: unread_counts.get(thread_id, 0)},
        }
    return summary


def _peers_of(thread_id: str) -> tuple[str, str]:
    """The two peer names encoded in ``dm:<a>::<b>``.

    Duplicated from ``_threads.peers_of`` rather than imported: ``_threads``
    owns the SIDECAR, and this module must not grow an import edge to the file
    layer it exists to replace. Sixteen characters of parsing is a cheaper
    price than that dependency.
    """
    body = thread_id[3:] if thread_id.startswith("dm:") else thread_id
    a, _, b = body.partition("::")
    return (a, b)


def threads_summary(
    reader: str,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
) -> dict[str, dict]:
    """Public read: every thread's summary, from the store rather than a file."""
    conn = _open(db, store)
    try:
        return threads_summary_conn(conn, reader)
    finally:
        conn.close()


def message_count(
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
    include_deleted: bool = True,
) -> int:
    """Total rows in ``dm_messages``.

    Counts TOMBSTONED rows by default, because this number is what the
    no-shrink guard compares. The operator's ruling is that a written record
    never disappears and a count decrease is itself a bug — a count that
    excluded tombstones would fall when a message was deleted and would
    therefore report the very bug it exists to detect.
    """
    clause = "" if include_deleted else " WHERE deleted_at IS NULL"
    conn = _open(db, store)
    try:
        return int(
            _sole_value(
                conn.execute(f"SELECT COUNT(*) FROM dm_messages{clause}").fetchone()
            )
        )
    finally:
        conn.close()


def thread_ids(
    *, db: str | Path | None = None, store: str | Path | None = None
) -> list[str]:
    """Every thread id in the store, sorted."""
    conn = _open(db, store)
    try:
        rows = conn.execute("SELECT id FROM dm_threads ORDER BY id").fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


__all__ = [
    "CURRENT_MEMBERS_SQL",
    "MESSAGE_ORDER_SQL",
    "current_members",
    "list_members",
    "message_count",
    "messages_in",
    "messages_in_conn",
    "row_to_message",
    "thread_ids",
    "unread_for",
    "unread_for_conn",
]

# EOF
