#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DELIVERY RECEIPTS — write down what we actually know, and no more.

WHAT THE TRANSPORT CAN AND CANNOT TELL US (measured, not assumed)
-----------------------------------------------------------------
``notifications/claude/channel`` is a JSON-RPC **notification**. It carries no
``id``, so by JSON-RPC 2.0 the peer MUST NOT answer it — there is no response,
no error frame, no receipt. In the MCP Python SDK the push bottoms out at
``ServerSession.send_message`` -> ``await self._write_stream.send(message)``
over an anyio memory stream created with ``max_buffer_size=0``
(``mcp/server/stdio.py``), so a returning ``await send(params)`` proves exactly
one thing: **the stdout writer task took the bytes.** It does not prove the
client read them, and it certainly does not prove the client SURFACED them —
Claude Code discards a channel push from a server that is not on its launch-line
allowlist, silently, after reading it.

``ServerSession.send_ping()`` exists and does await a reply, but a ping proves
the client process is answering JSON-RPC — it stays green through exactly the
outage this module exists for (allowlist mismatch: tools fine, channel deaf).

So there is NO transport-level arrival signal to wait on. The honest move is
therefore not to invent one; it is to stop conflating two different facts that
the inbox used to store in a single ``seen`` bit:

* ``pushed_at``    — WE handed this record to the transport (all we know).
* ``confirmed_at`` — the RECIPIENT told us it arrived, by id, through
  :func:`scitex_cards._inbox_confirm.confirm_notifications` (PR #617).

``seen`` keeps its old job — the cursor, "do not push this again". What changes
is that advancing it no longer CLAIMS delivery, and a record that was pushed and
never confirmed stays visible forever instead of vanishing.

WHY THIS MATTERS (the incident, 2026-07-29)
-------------------------------------------
An agent's spec allowlisted ``server:scitex-cards`` while ``.mcp.json`` registers
this server as ``scitex-cards``. Every push was discarded on arrival; the drain
ack'd on ``send()`` returning; 228 rows were enqueued and consumed, ZERO unseen.
Weeks of operator DMs were destroyed with every check green. With a receipt on
each row, those 228 rows read "pushed, never confirmed" and
:mod:`scitex_cards._health_delivery` turns them into a red check with a hint.

THE BOUND
---------
:func:`record_push` flips ``seen`` in the SAME single UPDATE that writes
``pushed_at``. So the normal path pushes each record EXACTLY ONCE — no
redelivery, no storm. The only retry is when that write FAILS, in which case
neither the stamp nor the cursor moved and the record is re-pushed on the next
drain tick, capped by ``MAX_PUSH_PER_DRAIN`` (50) pushes per tick. That is the
same retry the old ``_inbox.ack`` failure path already had; this adds no new
redelivery class.

ZERO external-runtime imports (this sits under the standalone delivery rail).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Column/key: when WE handed the record to the transport.
PUSHED_AT = "pushed_at"

#: Column/key: when the RECIPIENT confirmed arrival, by id.
CONFIRMED_AT = "confirmed_at"

#: The receipt columns, added to the inbox table on demand (see
#: :func:`_ensure_columns`). Kept nullable so every pre-existing row stays
#: readable and simply reports "no receipt".
RECEIPT_COLUMNS = (PUSHED_AT, CONFIRMED_AT)


def _now_iso() -> str:
    """UTC timestamp in the inbox's own shape (``...Z``, second resolution)."""
    from ._inbox import _utc_now_iso

    return _utc_now_iso()


def _wanted(ids: "list[str] | str | None") -> list[str]:
    """Normalize an id argument to a de-duplicated, order-stable list."""
    if ids is None:
        return []
    if isinstance(ids, str):
        ids = [ids]
    out: list[str] = []
    for nid in ids:
        if nid and nid not in out:
            out.append(nid)
    return out


# --------------------------------------------------------------------------- #
# SQLite backend (the default)                                                #
# --------------------------------------------------------------------------- #
def _existing_columns(conn: sqlite3.Connection) -> set[str]:
    """Column names currently on the ``inbox`` table (empty when absent)."""
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(inbox)")}
    except sqlite3.Error:
        return set()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add the receipt columns if this DB predates them. Idempotent + racy-safe.

    ~21 agents share one ``cards.db``; two of them can reach the ``ALTER`` at the
    same instant and the loser sees ``duplicate column name``. That is the
    winner having done our job, not a failure, so it is swallowed — anything
    else and a health check could take down a drain.
    """
    have = _existing_columns(conn)
    for column in RECEIPT_COLUMNS:
        if column in have:
            continue
        try:
            conn.execute(f"ALTER TABLE inbox ADD COLUMN {column} TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.commit()


def _sqlite_stamp(
    recipient_id: str,
    ids: list[str],
    *,
    column: str,
    stamp: str,
    advance_cursor: bool,
    store: str | Path | None,
) -> list[str]:
    """Stamp ``column`` on ``ids`` (first stamp wins) in ONE atomic UPDATE.

    ``advance_cursor`` additionally flips ``seen`` in the SAME statement — that
    is what makes "the push is recorded" and "the cursor moved" a single fact
    with no window in between. ``COALESCE`` keeps the FIRST stamp: the age of an
    unconfirmed push must measure how long it has gone unanswered, not how
    recently it was retried.

    Returns the ids that exist in this recipient's inbox (the ones stamped).
    """
    from ._inbox_sqlite import _ensure_ready, inbox_target, open_connection

    placeholders = ",".join("?" for _ in ids)
    with open_connection(inbox_target(store)) as conn:
        _ensure_ready(conn, store)
        _ensure_columns(conn)
        # Same reason as the read below: table / recipient column / arrival
        # order are properties of the OPEN CONNECTION, not of this file.
        from ._inbox_shape import shape_for  # noqa: PLC0415 -- import cycle

        shape = shape_for(conn)
        rows = conn.execute(
            f"SELECT id FROM {shape.table} WHERE {shape.recipient} = ? "
            f"AND id IN ({placeholders}) {shape.order()}",
            (recipient_id, *ids),
        ).fetchall()
        present = [row["id"] for row in rows]
        if not present:
            return []
        present_placeholders = ",".join("?" for _ in present)
        seen_clause = "seen = 1, " if advance_cursor else ""
        conn.execute(
            f"UPDATE inbox SET {seen_clause}{column} = COALESCE({column}, ?) "
            f"WHERE recipient = ? AND id IN ({present_placeholders})",
            (stamp, recipient_id, *present),
        )
        conn.commit()
    return present


def _sqlite_receipts(recipient_id: str, store: str | Path | None) -> list[dict]:
    """Read every record + its receipts for ``recipient_id`` (read-only).

    Never creates the database and never ALTERs it: a doctor that has to write
    before it can measure is a doctor that can break the patient. A DB that
    predates the receipt columns simply reports ``None`` for both, which the
    health check reads — correctly — as "no push has ever been recorded".
    """
    from ._inbox_sqlite import inbox_target, open_connection

    db = inbox_target(store)
    # THE ABSENCE PROBE CANNOT BE `.exists()` ANY MORE, and this is the same
    # class the rest of today was: a store TARGET may be a URL, and `Path.exists`
    # on one is either an AttributeError (it is a str) or a lie (it is a
    # relative path that happens not to be there). The never-create guarantee in
    # the docstring above is a FILE property, so it is asked only of files.
    from ._store_url import is_postgres_url  # noqa: PLC0415 -- import cycle

    if not is_postgres_url(str(db)) and not Path(db).exists():
        return []
    with open_connection(db) as conn:
        columns = _existing_columns(conn)
        if not columns:
            return []
        # Table, recipient column and arrival order come from the LIVE
        # connection, not from this file's belief. Hardcoding `FROM inbox …
        # ORDER BY rowid` was correct while the rail was its own SQLite file
        # and is a syntax error against the canonical store, where the rail is
        # `notifications` ordered by `seq`.
        from ._inbox_shape import shape_for  # noqa: PLC0415 -- import cycle

        shape = shape_for(conn)
        selected = ["id", "event_type", "card_id", "ts", "seen"]
        selected += [c for c in RECEIPT_COLUMNS if c in columns]
        rows = conn.execute(
            f"SELECT {', '.join(selected)} FROM {shape.table} "
            f"WHERE {shape.recipient} = ? {shape.order()}",
            (recipient_id,),
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        record = {name: row[name] for name in selected}
        record["seen"] = bool(record.get("seen"))
        for column in RECEIPT_COLUMNS:
            record.setdefault(column, None)
        out.append(record)
    return out


# --------------------------------------------------------------------------- #
# File backend (break-glass, SCITEX_CARDS_INBOX_BACKEND=yaml)                   #
# --------------------------------------------------------------------------- #
def _file_stamp(
    recipient_id: str,
    ids: list[str],
    *,
    column: str,
    stamp: str,
    advance_cursor: bool,
    store: str | Path | None,
) -> list[str]:
    """File-backend twin of :func:`_sqlite_stamp` — same contract, one lock."""
    from ._inbox import _inboxes_path, _load_inboxes_section, _save_inboxes_unlocked
    from ._model import _store_lock

    path = _inboxes_path(store)
    wanted = set(ids)
    present: list[str] = []
    with _store_lock(path):
        inboxes = _load_inboxes_section(path)
        records = inboxes.get(recipient_id, [])
        for record in records:
            if record.get("id") not in wanted:
                continue
            present.append(record["id"])
            if not record.get(column):
                record[column] = stamp
            if advance_cursor:
                record["seen"] = True
        if present:
            inboxes[recipient_id] = records
            _save_inboxes_unlocked(inboxes, path)
    return present


def _file_receipts(recipient_id: str, store: str | Path | None) -> list[dict]:
    """File-backend twin of :func:`_sqlite_receipts` — read-only."""
    from ._inbox import _inboxes_path, _load_inboxes_section

    path = _inboxes_path(store)
    if not path.exists():
        return []
    out: list[dict] = []
    for record in _load_inboxes_section(path).get(recipient_id, []):
        out.append(
            {
                "id": record.get("id"),
                "event_type": record.get("event_type"),
                "card_id": record.get("card_id"),
                "ts": record.get("ts"),
                "seen": bool(record.get("seen")),
                PUSHED_AT: record.get(PUSHED_AT),
                CONFIRMED_AT: record.get(CONFIRMED_AT),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Public API — backend-agnostic, identical semantics on both                   #
# --------------------------------------------------------------------------- #
def _stamp(
    recipient_id: str,
    ids: "list[str] | str | None",
    *,
    column: str,
    advance_cursor: bool,
    at: str | None,
    store: str | Path | None,
) -> list[str]:
    """Dispatch a stamp onto whichever inbox backend is active.

    THREE BACKENDS, NOT TWO, and the missing third was a live defect. This read
    ``_sqlite_stamp if _use_sqlite() else _file_stamp`` — a two-valued question
    asked of a three-valued world. After #780 the shared-inbox deployment (the
    fleet's) answers "not sqlite", so every push receipt and every recipient
    confirmation was written to a FILE while the notifications themselves lived
    in PostgreSQL. Measured 2026-08-11: 8 rows on the rail, 0 with ``pushed_at``,
    0 with ``confirmed_at``, and an ``inboxes.json`` LOCK file next to no
    ``inboxes.json`` at all — the file rail being taken, finding nothing, and
    reporting success. See :mod:`scitex_cards._inbox_receipt_postgres`.

    Asking :func:`scitex_cards._inbox_backend.backend` — the same function the
    enqueue/poll/ack path asks — is what keeps the receipt and the row it
    describes in the same database by construction.
    """
    from ._inbox_backend import POSTGRES, SQLITE, backend

    normalized = _wanted(ids)
    if not recipient_id or not normalized:
        return []
    stamp = at or _now_iso()
    active = backend()
    if active == POSTGRES:
        from ._inbox_receipt_postgres import stamp as _postgres_stamp

        return _postgres_stamp(
            recipient_id,
            normalized,
            column=column,
            stamp=stamp,
            advance_cursor=advance_cursor,
            store=store if isinstance(store, str) else None,
        )
    writer = _sqlite_stamp if active == SQLITE else _file_stamp
    return writer(
        recipient_id,
        normalized,
        column=column,
        stamp=stamp,
        advance_cursor=advance_cursor,
        store=store,
    )


def record_push(
    recipient_id: str,
    ids: "list[str] | str | None",
    *,
    at: str | None = None,
    store: str | Path | None = None,
) -> list[str]:
    """Record "we handed these to the transport" AND advance the cursor, atomically.

    This is what the channel drain calls INSTEAD of :func:`scitex_cards._inbox.ack`.
    Same cursor effect as before — each record is pushed exactly once, so no
    redelivery — but the row now says what actually happened: pushed at ``at``,
    confirmed by nobody. If this write fails, the cursor did NOT move either, so
    the record is retried on the next drain rather than disappearing.

    Returns the ids present in ``recipient_id``'s inbox (the ones stamped).
    """
    return _stamp(
        recipient_id,
        ids,
        column=PUSHED_AT,
        advance_cursor=True,
        at=at,
        store=store,
    )


def record_confirmation(
    recipient_id: str,
    ids: "list[str] | str | None",
    *,
    at: str | None = None,
    store: str | Path | None = None,
) -> list[str]:
    """Record "the recipient told us these arrived" — the only arrival evidence.

    Called by :func:`scitex_cards._inbox_confirm.confirm_notifications`, the one
    sanctioned confirm verb. Deliberately does NOT touch ``seen``: by the time a
    real recipient confirms, the drain has usually already advanced the cursor,
    and confirmation is a DIFFERENT fact from the cursor — conflating the two is
    the whole defect. Idempotent: the first confirmation time is kept.

    Returns the ids present in ``recipient_id``'s inbox (the ones stamped).
    """
    return _stamp(
        recipient_id,
        ids,
        column=CONFIRMED_AT,
        advance_cursor=False,
        at=at,
        store=store,
    )


def receipts(recipient_id: str, *, store: str | Path | None = None) -> list[dict]:
    """Every record for ``recipient_id`` with its receipts, oldest first.

    Read-only on every backend (it will not create or migrate a store), so the
    health doctor can measure without changing what it measures. Each entry is
    ``{id, event_type, card_id, ts, seen, pushed_at, confirmed_at}``; a record
    that predates receipts reports ``None`` for both stamps.

    Dispatches three ways for the same reason :func:`_stamp` does — a doctor
    reading a different database from the one the rail writes is a doctor that
    reports health it never measured.
    """
    from ._inbox_backend import POSTGRES, SQLITE, backend

    if not recipient_id:
        return []
    active = backend()
    if active == POSTGRES:
        from ._inbox_receipt_postgres import receipts as _postgres_receipts

        return _postgres_receipts(
            recipient_id, store if isinstance(store, str) else None
        )
    reader = _sqlite_receipts if active == SQLITE else _file_receipts
    return reader(recipient_id, store)


def is_confirmed(record: dict) -> bool:
    """Has THIS record been confirmed by its recipient? The single definition.

    ``confirmed_at`` is the ONLY evidence of delivery. ``seen`` is not: the
    channel drain advances ``seen`` when it pushes a record (see
    ``_inbox_confirm.confirm_notifications``), so by the time a consumer acts on
    a notification it is already seen and was never confirmed. Keying anything
    on ``seen`` therefore answers "did the drain run?" while appearing to answer
    "did the recipient get it?".

    THIS FUNCTION EXISTS BECAUSE A COMMENT COULD NOT TRAVEL. That rule was
    already written down, correctly and in full, at
    ``_inbox_confirm.py:218-224`` — and it protected exactly the one line it was
    attached to. Two other surfaces needed it and did not get it: ack's response
    vocabulary and poll's ``unconfirmed`` both keyed on ``seen``, so both told
    consumers "nothing outstanding" while the health doctor counted a growing
    pile of pushed-but-unconfirmed rows and blamed the consumer for not acking.
    Measured 2026-08-11 by scitex-db and reproduced first-person on 0.35.1: 20
    acks, 20 ``already_confirmed``, zero ``confirmed``.

    So the rule now lives in a function the callers must call, rather than in
    prose the next caller must read. A mechanical barrier beats a written
    warning precisely when the warning is correct and still gets missed.
    """
    return bool(record.get(CONFIRMED_AT))


def unconfirmed_ids(
    recipient_id: str,
    ids: "list[str] | str | None" = None,
    *,
    store: str | Path | None = None,
) -> list[str]:
    """Ids in ``recipient_id``'s inbox with no confirmation stamp, oldest first.

    Scoped to ``ids`` when given; otherwise the WHOLE inbox. The whole-inbox
    default is the point: "what is still awaiting confirmation" is a property of
    the INBOX, not of whichever page a caller happened to fetch. Computing it
    over a page means a caller who fetched an empty page is told nothing is
    outstanding — which is exactly what poll did, because the drain had already
    marked every row seen and the default page is unseen-only.

    Read-only: it never creates or migrates a store.
    """
    if not recipient_id:
        return []
    wanted = set(_wanted(ids)) if ids is not None else None
    out: list[str] = []
    for record in receipts(recipient_id, store=store):
        rid = record.get("id")
        if not rid or is_confirmed(record):
            continue
        if wanted is not None and rid not in wanted:
            continue
        out.append(str(rid))
    return out


__all__ = [
    "CONFIRMED_AT",
    "PUSHED_AT",
    "RECEIPT_COLUMNS",
    "is_confirmed",
    "receipts",
    "record_confirmation",
    "record_push",
    "unconfirmed_ids",
]

# EOF
