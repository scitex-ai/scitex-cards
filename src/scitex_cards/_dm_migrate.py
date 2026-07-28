#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill from the sidecar, cross-host merge, and the A/B verification gate.

DESIGN: ``docs/design/dm-into-cards-db-migration.md`` M1 (backfill), M2
(verify), §6.3 (federated merge).

THE TWO PROPERTIES THAT MAKE A STORE MIGRATION SURVIVABLE, and neither is
visible from the code that performs it:

**It can be run twice.** Every insert is ``INSERT OR IGNORE`` against a stable
primary key, so a second pass inserts nothing. That is what makes an
interrupted backfill recoverable by simply running it again, instead of by
reasoning about what it got halfway through.

**It can be undone, because it never touched the source.** ``threads.json`` is
read under its own flock and is not written, moved or truncated. Rollback is
redeploying the previous version — no restore, no repair. This is why
:func:`backfill_from_sidecar` opens the sidecar READ-ONLY and why the test
suite asserts the file is byte-identical afterwards.

DRY RUN IS A REAL RUN THAT ROLLS BACK. It performs every insert and reports
the true ``rowcount``, then aborts the transaction. An estimate computed by a
different code path would be measuring a different thing than the migration
does — and the counts a dry run prints are exactly the numbers an operator
uses to decide whether to proceed.

THE NO-SHRINK RULE IS THE OPERATOR'S, MADE EXECUTABLE. A written record never
disappears; a count decrease is itself a bug. A peer's export is always a
SNAPSHOT and may be older than what is here, so a merge that receives a subset
must keep the local extras. Every board wipe in the 2026-07-19/20 sequence was
a stale snapshot treated as truth, deleting the rows it happened to lack.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ._dm_ids import (
    derived_message_id,
    origin_host,
    peers_of_pair,
    resolve_dm_db,
    utc_now_iso,
)
from ._dm_write import (
    ensure_thread,
    insert_message,
    insert_receipt,
    record_member_event,
)

#: The tables a merge payload carries, PARENT FIRST. Order is not cosmetic:
#: ``dm_messages`` has a foreign key onto ``dm_threads`` and ``dm_receipts``
#: onto ``dm_messages``, and connections run with ``foreign_keys=ON``.
MERGE_TABLES: tuple[str, ...] = (
    "dm_threads",
    "dm_thread_member_events",
    "dm_messages",
    "dm_receipts",
)

#: Receipt provenance for rows the backfill synthesises. The sidecar records
#: ``read: true`` without saying WHEN, so the receipt gets the backfill time
#: plus this source — a SENTINEL, not an absence. A NULL ``read_at`` would be
#: indistinguishable from "never read" and would pop every already-read
#: message unread for everyone at cutover.
BACKFILL_SOURCE = "backfill"


def _open(db, store) -> sqlite3.Connection:
    from ._db import open_db

    return open_db(resolve_dm_db(db, store=store))


def _read_sidecar(path: Path) -> dict[str, list[dict]]:
    """Parse ``threads.json`` UNDER ITS OWN FLOCK, without writing it.

    The lock matters: ``append_message`` rewrites the WHOLE document, so an
    unlocked read can land mid-rewrite and see a truncated file. Taking the
    sidecar's own lock (never the task store's) is the same discipline the
    sidecar's writers use.
    """
    from ._threads import _load_threads, _threads_lock

    if not path.exists():
        return {}
    with _threads_lock(path):
        return _load_threads(path)


def _sidecar_message_count(threads: dict[str, list[dict]]) -> int:
    return sum(len(v) for v in threads.values())


def backfill_from_sidecar(
    sidecar: str | Path,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
    dry_run: bool = False,
) -> dict:
    """COPY every sidecar record into the DM tables. Never moves, never deletes.

    Returns a report carrying both sides of the count the operator must check:
    ``sidecar_messages`` (what the file holds) and ``db_messages_after`` (what
    the store holds once this is applied). They are printed together because
    the migration's whole claim is that they agree.

    ``seq`` comes from each record's POSITION in the sidecar list, which is
    exactly the append order the sidecar preserved — so the migrated
    conversation renders in the order it was actually written.
    """
    sidecar_path = Path(sidecar).expanduser()
    threads = _read_sidecar(sidecar_path)
    stamp = utc_now_iso()
    host = origin_host()
    report = {
        "sidecar": str(sidecar_path),
        "dry_run": bool(dry_run),
        "sidecar_threads": len(threads),
        "sidecar_messages": _sidecar_message_count(threads),
        "inserted_threads": 0,
        "inserted_members": 0,
        "inserted_messages": 0,
        "inserted_receipts": 0,
    }
    conn = _open(db, store)
    report["db"] = str(resolve_dm_db(db, store=store))
    try:
        conn.execute("BEGIN IMMEDIATE")
        report["db_messages_before"] = _count(conn)
        for thread_id, records in threads.items():
            report["inserted_threads"] += int(
                ensure_thread(
                    conn,
                    thread_id,
                    kind="pair",
                    created_at=_earliest_ts(records) or stamp,
                    host=host,
                )
            )
            for peer in peers_of_pair(thread_id):
                report["inserted_members"] += int(
                    record_member_event(
                        conn, thread_id, peer, "join", ts=stamp, host=host
                    )
                )
            _backfill_records(conn, thread_id, records, stamp, host, report)
        report["db_messages_after"] = _count(conn)
        _assert_no_shrink(report["db_messages_before"], report["db_messages_after"])
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return report


def _earliest_ts(records: list[dict]) -> str | None:
    stamps = [r.get("ts") for r in records if isinstance(r.get("ts"), str)]
    return min(stamps) if stamps else None


def _backfill_records(conn, thread_id, records, stamp, host, report) -> None:
    """Insert one thread's records, minting an id for any that lacks one."""
    for index, record in enumerate(records):
        sender = record.get("from") or ""
        to = record.get("to") or ""
        ts = record.get("ts") or stamp
        body = record.get("body") or ""
        message_id = record.get("id") or derived_message_id(
            thread_id, sender, to, ts, body
        )
        report["inserted_messages"] += int(
            insert_message(
                conn,
                message_id=message_id,
                thread_id=thread_id,
                sender=sender,
                body=body,
                ts=ts,
                seq=index + 1,
                host=host,
                record=record,
            )
        )
        if record.get("read") and to:
            report["inserted_receipts"] += int(
                insert_receipt(
                    conn,
                    message_id,
                    to,
                    read_at=stamp,
                    host=host,
                    source=BACKFILL_SOURCE,
                )
            )


def _count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM dm_messages").fetchone()[0])


def _assert_no_shrink(before: int, after: int) -> None:
    """RAISE if the operation removed rows. It does not warn.

    A backfill is a copy and a merge is a union; neither can shrink. A shrink
    therefore means a bug, and the ruling is that a count decrease IS the bug —
    so this refuses rather than logging and carrying on.
    """
    if after < before:
        raise RuntimeError(
            f"refusing to commit: dm_messages would shrink from {before} to "
            f"{after}. The store is append-only - a count decrease is itself a "
            f"bug, never routine cleanup."
        )


def merge_dm(
    payload: dict,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
) -> dict:
    """Union a peer host's DM export into this store. Never overwrites.

    Every table is append-only and every row carries a globally-unique primary
    key, so the merge is an ``INSERT OR IGNORE`` with no arbitration: no
    last-write-wins, no clock comparison, no vector clocks. It is commutative,
    associative and idempotent, which is what removes the need for a
    coordinator and makes merge ORDER irrelevant.
    """
    conn = _open(db, store)
    report = {"merged": {}, "db": str(resolve_dm_db(db, store=store))}
    try:
        conn.execute("BEGIN IMMEDIATE")
        before = _count(conn)
        for table in MERGE_TABLES:
            rows = payload.get(table) or []
            report["merged"][table] = sum(_insert_row(conn, table, r) for r in rows)
        after = _count(conn)
        _assert_no_shrink(before, after)
        report["db_messages_before"] = before
        report["db_messages_after"] = after
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return report


def _insert_row(conn: sqlite3.Connection, table: str, row: dict) -> int:
    """``INSERT OR IGNORE`` one payload row. Returns 1 if it was new.

    Columns come from the ROW, intersected with what the table actually has,
    so a peer running a newer schema cannot break the merge with a column this
    host does not know about — it is dropped, and its data survives inside
    ``record_json`` where the exactness rule keeps it.
    """
    from ._db import table_columns

    known = table_columns(conn, table)
    cols = [c for c in row if c in known]
    if not cols:
        return 0
    placeholders = ", ".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT OR IGNORE INTO {table}({', '.join(cols)}) VALUES({placeholders})",
        [_coerce(row[c]) for c in cols],
    )
    return 1 if cur.rowcount > 0 else 0


def _coerce(value):
    """Payload values are JSON; a nested structure rides as a JSON string."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def export_dm(*, db: str | Path | None = None, store: str | Path | None = None) -> dict:
    """Dump every DM table in the shape :func:`merge_dm` consumes.

    Read from ONE connection inside ONE transaction. The WAL snapshot lesson is
    already recorded in ``_db_export.export_doc``: reading the halves of a
    comparison from separate connections lets a concurrent writer manufacture a
    false mismatch, and that exact false negative blanked ``list_tasks``
    fleet-wide once.
    """
    conn = _open(db, store)
    try:
        conn.execute("BEGIN")
        out = {}
        for table in MERGE_TABLES:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            out[table] = [{k: r[k] for k in r.keys()} for r in rows]
        conn.commit()
        return out
    finally:
        conn.close()


def verify_against_sidecar(
    sidecar: str | Path,
    *,
    db: str | Path | None = None,
    store: str | Path | None = None,
) -> dict:
    """The M2 A/B gate: diff the sidecar's records against the DB's, by id.

    ``ok`` means every sidecar message is present in the store. EXTRA rows in
    the store are reported but do NOT fail the gate — after the write path
    flips, new DMs land in the DB first, so "the DB has more" is the expected
    steady state and treating it as a mismatch would make the gate cry wolf
    exactly when it is working.
    """
    threads = _read_sidecar(Path(sidecar).expanduser())
    conn = _open(db, store)
    try:
        conn.execute("BEGIN")
        db_ids = {r[0] for r in conn.execute("SELECT id FROM dm_messages").fetchall()}
        conn.commit()
    finally:
        conn.close()
    sidecar_ids: set[str] = set()
    for thread_id, records in threads.items():
        for record in records:
            sidecar_ids.add(
                record.get("id")
                or derived_message_id(
                    thread_id,
                    record.get("from") or "",
                    record.get("to") or "",
                    record.get("ts") or "",
                    record.get("body") or "",
                )
            )
    missing = sorted(sidecar_ids - db_ids)
    return {
        "ok": not missing,
        "sidecar_threads": len(threads),
        "sidecar_messages": _sidecar_message_count(threads),
        "db_messages": len(db_ids),
        "missing_in_db": missing,
        "extra_in_db": sorted(db_ids - sidecar_ids),
    }


__all__ = [
    "BACKFILL_SOURCE",
    "MERGE_TABLES",
    "backfill_from_sidecar",
    "export_dm",
    "merge_dm",
    "verify_against_sidecar",
]

# EOF
