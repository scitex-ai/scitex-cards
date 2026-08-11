#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB → JSON export: the backup/audit rail of ADR-0010.

The operator's ruling (2026-07-16): the DATABASE is the single source of
truth; backup = periodically EXPORT a JSON snapshot *from* the DB and git-
snapshot that export. Git tracks an export, never live data — which is what
retires the "dotfiles working tree IS the live store" merge hazard.

Exactness contract
------------------
Every entity is reconstructed from its VERBATIM ``*_json`` payload
(``tasks.card_json`` — v2; ``users/notifications/messages.record_json`` — v3),
never from typed columns: a column-based rebuild would drop unknown keys and
reorder the rest. A NULL payload means the row predates its payload column and
the DB was never re-imported — the export REFUSES loudly rather than emit a
stripped record.

The columns that legitimately MUTATE in the DB after import (``seen`` on a
notification, ``read`` on a message, ``last_seen`` on a user) are overlaid
onto the payload so a post-cutover export reflects live state, not the
import-time snapshot of those flags.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from ._db import open_db
from ._db_payload import card_from_payload

#: (table, mutable-column overlays applied on top of the verbatim payload)
_OVERLAYS: dict[str, tuple[str, ...]] = {
    "users": ("last_seen",),
    "notifications": ("seen",),
    "messages": ("read",),
}

#: columns whose SQL integer form maps back to a YAML bool.
_BOOL_COLS = {"seen", "read"}


class ExportRefused(RuntimeError):
    """A row has no verbatim payload — the DB must be re-imported first."""


def _record(row, table: str) -> dict[str, Any]:
    """Rebuild one record from its verbatim payload + mutable-column overlay."""
    blob = row["record_json"]
    if blob is None:
        # SAY WHAT IS TRUE, NOT WHAT IS LIKELY. This message used to assert
        # "this DB predates schema v3's payload columns and cannot be
        # back-filled ... use a database written by a current version". Both
        # clauses were a GUESS PRESENTED AS A DIAGNOSIS, and on 2026-08-11 both
        # were false: the offending rows were SECONDS old, written by the
        # current version (`_inbox_postgres.enqueue` omitted the column), and
        # they back-filled trivially from their own columns.
        #
        # It cost real time. Three agents chased a migration that did not exist
        # and could not have been performed, on a database that was current —
        # dotfiles reported losing time to it before a channel event revealed
        # the row's true age. An error that names the wrong cause is worse than
        # one that names none, because it is ACTIONABLE in the wrong direction.
        #
        # So: report the row and the fact. The age of the database is not
        # observable from here and must not be claimed. Where a writer can be
        # named it should be, because that points at the bug rather than away
        # from it (dotfiles' formulation, adopted).
        raise ExportRefused(
            f"{table} row {row['id']!r} was written without a record_json "
            "payload. This is a WRITER defect: some emit path inserted the row "
            "without serialising its payload. It is not evidence that the "
            "database is old — a row written seconds ago by current code "
            "produces this same refusal.\n"
            "  NEXT STEP: find the write path that produced this row and make "
            "it pass record_json. The row itself can usually be repaired by "
            "rebuilding the payload from its own columns; do NOT delete or "
            "quarantine it, because notification rows are often undelivered "
            "messages and discarding one to unblock a write destroys it.\n"
            "  Exporting stripped records is worse than exporting none, which "
            "is why this refuses rather than returning a partial record."
        )
    rec = card_from_payload(blob)
    for col in _OVERLAYS[table]:
        if row[col] is not None:
            rec[col] = bool(row[col]) if col in _BOOL_COLS else row[col]
    return rec


def export_doc(
    db_path: str | Path | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> tuple[dict, dict]:
    """Assemble ``({tasks, users, inboxes}, threads)`` from the DB, exactly.

    Tasks come back in document order (``row_order``); inbox and thread
    records in creation order (timestamp, then primary key as tie-breaker) —
    matching how the exported lists grew, and expressible on either backend.

    ``conn`` — READ THE EXPORT AND ITS VERIFICATION FROM ONE SNAPSHOT.
    A caller that must cross-check this export against the database (see
    :func:`scitex_cards._store._read_canonical_db_or_raise`) cannot re-count on
    a SECOND connection: the store is WAL, so two connections take two
    INDEPENDENT snapshots taken however long the export took apart, and any
    concurrent writer in that window makes the two disagree with no card
    missing at all. That false "INCOMPLETE" refusal blanked ``list_tasks``
    fleet-wide (observed 2,374 exported vs 2,375 in-table while
    ``scitex-cards db verify`` reported the DB perfectly healthy).

    So the caller opens ONE connection, begins ONE read transaction, and hands
    it here. When ``conn`` is supplied it is used as-is and NOT closed —
    ownership stays with the caller, whose transaction defines the snapshot
    both the export and the verifying ``COUNT(*)`` observe. ``db_path`` is
    ignored in that case (the connection already names the database).

    The connection MUST have been opened through :func:`scitex_cards._db.connect`
    (directly or via :func:`open_db`), because that is where the
    min-client-version gate lives. Hand-rolling a bare ``sqlite3.connect`` here
    would silently delete that gate.
    """
    owned = conn is None
    if owned:
        conn = open_db(db_path)
    try:
        tasks: list[dict] = []
        for r in conn.execute(
            "SELECT id, card_json FROM tasks ORDER BY row_order"
        ).fetchall():
            if r["card_json"] is None:
                raise ExportRefused(
                    f"task {r['id']!r} has no card_json payload — this DB "
                    "predates the payload columns; use one written by a "
                    "current version."
                )
            tasks.append(card_from_payload(r["card_json"]))

        # ORDERED BY REAL COLUMNS, NOT ``rowid``. ``rowid`` is a SQLite
        # implementation detail with no PostgreSQL equivalent, so these four
        # queries were the export path's hard stop against a server backend --
        # and they would have failed at CUTOVER, not at porting time.
        #
        # The replacement keeps the property that actually mattered. ``rowid``
        # was never the goal; a STABLE, REPRODUCIBLE order was, so that an
        # export of an unchanged store is byte-identical each time. A creation
        # timestamp with the primary key as tie-breaker gives that on both
        # engines, and on append-only tables it is the same order ``rowid``
        # produced. The tie-break is not decorative: timestamps here have
        # one-second resolution, so same-second rows would otherwise order
        # arbitrarily and the export would differ run to run.
        users = [
            _record(r, "users")
            for r in conn.execute(
                "SELECT * FROM users ORDER BY created_at, id"
            ).fetchall()
        ]

        # Seed from the recipients table first so a DRAINED inbox (a
        # key with zero rows) still appears as an empty list (v4).
        inboxes: dict[str, list[dict]] = {
            r["recipient_id"]: []
            for r in conn.execute(
                "SELECT recipient_id FROM inbox_recipients ORDER BY recipient_id"
            ).fetchall()
        }
        for r in conn.execute("SELECT * FROM notifications ORDER BY ts, id").fetchall():
            inboxes.setdefault(r["recipient_id"], []).append(
                _record(r, "notifications")
            )

        threads: dict[str, list[dict]] = {}
        for r in conn.execute("SELECT * FROM messages ORDER BY ts, id").fetchall():
            threads.setdefault(r["thread_key"], []).append(_record(r, "messages"))
    finally:
        if owned:
            conn.close()

    doc: dict[str, Any] = {"tasks": tasks}
    if users:
        doc["users"] = users
    if inboxes:
        doc["inboxes"] = inboxes
    return doc, threads


def _newest_last_activity(tasks: list) -> str | None:
    """The lexically-latest ``last_activity`` among ``tasks``, or ``None``.

    ``last_activity`` is always an ISO-8601 UTC timestamp with the ``Z``
    suffix (see ``_db._utc_now_iso``), so a plain lexical max sorts correctly
    without parsing. Exposed on the export report so a caller (``db
    snapshot``'s freshness guard) can compare it against a LIVE query of the
    DB's typed ``last_activity`` column — the export is built exclusively
    from ``card_json`` (never the typed columns), so the two values agree in
    a healthy DB and diverge exactly when the export has gone stale.
    """
    values = [t.get("last_activity") for t in tasks if t.get("last_activity")]
    return max(values) if values else None


def _atomic_write(path: Path, text: str) -> None:
    """tmp → flush+fsync → rename; a crash never leaves a torn export."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def export_json(
    db_path: str | Path | None = None,
    out: str | Path | None = None,
    threads_out: str | Path | None = None,
) -> dict[str, Any]:
    """Export the DB to JSON text files; return a count report.

    ``out`` defaults to ``<db_dir>/export/tasks.json``; ``threads_out``
    defaults to ``threads.json`` beside it. The report carries the counts so
    a caller (or the snapshot rail) prints what was exported — a silent
    export is a bulk operation with no dry-run trace.
    """
    import json

    from ._paths import resolve_tasks_path
    from ._store_target import resolve_store_target

    doc, threads = export_doc(db_path)

    # TWO AXES, RESOLVED SEPARATELY — this was one call and it broke on a DSN.
    #
    # `db` is the store's IDENTITY, which may legitimately be a PostgreSQL URL,
    # and is only used as a label in the returned report. `out_path` is a LOCAL
    # STATE path, which is always a real directory. Deriving both from
    # `resolve_db_path` meant a DSN raised before either was needed, which is
    # what killed the hourly off-site snapshot for ~31 hours (2026-08-02).
    db = resolve_store_target(db_path)
    out_path = (
        Path(out).expanduser()
        if out
        else resolve_tasks_path(db_path).parent / "export" / "tasks.json"
    )
    threads_path = (
        Path(threads_out).expanduser()
        if threads_out
        else out_path.parent / "threads.json"
    )

    _atomic_write(out_path, json.dumps(doc, indent=2, ensure_ascii=False))
    # The sidecar contract is a top-level ``threads:`` mapping
    # (scitex_cards._threads._load_threads reads exactly that key) — an
    # export must be loadable by the same reader as the live sidecar.
    _atomic_write(
        threads_path, json.dumps({"threads": threads}, indent=2, ensure_ascii=False)
    )

    return {
        "db": str(db),
        "tasks_json": str(out_path),
        "threads_json": str(threads_path),
        "tasks": len(doc.get("tasks", [])),
        "users": len(doc.get("users", [])),
        "inbox_recipients": len(doc.get("inboxes", {})),
        "notifications": sum(len(v) for v in doc.get("inboxes", {}).values()),
        "threads": len(threads),
        "messages": sum(len(v) for v in threads.values()),
        "newest_last_activity": _newest_last_activity(doc.get("tasks", [])),
    }


__all__ = ["ExportRefused", "export_doc", "export_json"]

# EOF
