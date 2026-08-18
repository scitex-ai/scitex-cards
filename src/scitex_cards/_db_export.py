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

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only -- no driver is imported at runtime
    from ._backend_connect import StoreConnection

import logging
import os
from pathlib import Path
from typing import Any

from ._db import open_db
from ._db_payload import card_from_payload

logger = logging.getLogger(__name__)

#: (table, mutable-column overlays applied on top of the verbatim payload)
_OVERLAYS: dict[str, tuple[str, ...]] = {
    "users": ("last_seen",),
    "notifications": ("seen",),
    "messages": ("read",),
}

#: columns whose SQL integer form maps back to a YAML bool.
_BOOL_COLS = {"seen", "read"}


class ExportRefused(RuntimeError):
    """A row has no verbatim payload AND none can be rebuilt from its columns."""


#: Per-table rebuild rules for a payload-less row. A table appears here ONLY
#: when its record shape is CLOSED and every key is a column of its own, so the
#: rebuild is exact rather than stripped. ``notifications`` qualifies;
#: ``tasks`` emphatically does not (22 live card keys are not columns at all —
#: see :mod:`scitex_cards._db_payload`), and ``users`` / ``messages`` have no
#: live column-only writer, so a payload-less row there is a genuine unknown
#: rather than a writer this package can name.
_REBUILDERS: dict[str, Any] = {}


def _rebuilders() -> dict[str, Any]:
    """The rebuild rules, imported lazily to keep module import cheap."""
    if not _REBUILDERS:
        from ._inbox_record import rebuild_notification_record

        _REBUILDERS["notifications"] = rebuild_notification_record
    return _REBUILDERS


#: The column whose value dates a row, per table. Named because the ONE fact
#: that most reliably diagnoses a payload-less row is WHEN it was written, and
#: the message that omitted it cost a night: it asserted "this DB predates
#: schema v3" about rows created minutes earlier by current code, and sent
#: three separate agents hunting a migration problem that did not exist.
_WRITTEN_AT: dict[str, str] = {
    "users": "created_at",
    "notifications": "ts",
    "messages": "ts",
}


def _written_at(row, table: str) -> str | None:
    """When this row was written, if the table records it."""
    column = _WRITTEN_AT.get(table)
    if not column:
        return None
    try:
        return row[column]
    except (IndexError, KeyError):
        return None


def _refusal(row, table: str, *, detail: str) -> ExportRefused:
    """The refusal, saying what is true and what to do about it.

    SAY WHAT IS TRUE, NOT WHAT IS LIKELY. This message used to assert "this DB
    predates schema v3's payload columns ... use a database written by a
    current version". Both clauses were a GUESS PRESENTED AS A DIAGNOSIS, and
    on 2026-08-11 both were false: the rows were SECONDS old, written by
    current code, and they rebuilt trivially from their own columns. Three
    agents chased a migration that did not exist. An error that names the wrong
    cause is worse than one that names none, because it is ACTIONABLE in the
    wrong direction.

    The wording naming the WRITER rather than the row's age is #805's (itself
    dotfiles' formulation) and is kept verbatim, because it points at the bug
    instead of away from it. What is added here is the row's own TIMESTAMP —
    the single fact that lets a reader settle the age question for themselves
    instead of taking anyone's word for it — and the reason the automatic
    repair could not rescue this particular row.
    """
    stamp = _written_at(row, table)
    when = f" (written {stamp})" if stamp else ""
    return ExportRefused(
        f"{table} row {row['id']!r}{when} was written without a record_json "
        "payload. This is a WRITER defect: some emit path inserted the row "
        "without serialising its payload. It is not evidence that the "
        "database is old — a row written seconds ago by current code "
        "produces this same refusal, and the row's own timestamp above is "
        "what settles that question.\n"
        f"  This row could not be repaired automatically because {detail}\n"
        "  NEXT STEP: find the write path that produced this row and make "
        "it pass record_json. Rebuildable rows ARE repaired automatically on "
        "read; do NOT delete or quarantine this one, because notification "
        "rows are often undelivered messages and discarding one to unblock a "
        "write destroys it. Nothing was deleted or modified here — inspect "
        "the row with:\n"
        f"    SELECT * FROM {table} WHERE id = '{row['id']}';\n"
        "  Exporting stripped records is worse than exporting none, which "
        "is why this refuses rather than returning a partial record."
    )


def _record(row, table: str, *, repair: bool = True) -> dict[str, Any]:
    """Rebuild one record from its verbatim payload + mutable-column overlay.

    ``repair`` names WHICH CONTRACT the caller is under. The board read is
    tolerant (``True``, the default); the backup rail is strict (``False``).
    See the ``else`` branch below for why those must not be the same setting.

    A payload-less row is REPAIRED where the data allows it, rather than
    failing the whole read. This matters far beyond the export: the live read
    path assembles the ENTIRE document through here, so refusing one row failed
    every card write fleet-wide — ``add_task``, ``update_task``,
    ``comment_task`` — over a single notification the card path never even
    looks at. Refusing was also the most destructive option available: an
    operator responding to the refusal by clearing the offending row would have
    destroyed an undelivered operator DM, which is what one of the three rows
    measured on 2026-08-11 actually was.
    """
    blob = row["record_json"]
    if blob is not None:
        rec = card_from_payload(blob)
    elif repair:
        rec = _repair(row, table)
    else:
        # THE BACKUP RAIL DOES NOT REPAIR, and that is not a lesser tolerance —
        # it is a different contract. This function serves two callers whose
        # requirements are opposites: a BOARD READ must survive one damaged row
        # (the alternative is a fleet-wide write outage over a notification the
        # card path never looks at), while a BACKUP must be EXACT OR ABSENT
        # (ADR-0010), because a snapshot that silently contains a rebuilt record
        # is a snapshot you cannot restore from and cannot audit against.
        #
        # A rebuild is exact for what the columns hold, but it is still not the
        # bytes the writer stored — and a backup's whole job is to be those
        # bytes. So the strictness lives on the caller, not on the row.
        raise _refusal(
            row,
            table,
            detail=(
                "this caller requires the VERBATIM payload: it is the backup "
                "rail, whose contract is exact-or-absent. The same row reads "
                "fine on the board, which repairs it in memory — so the store "
                "is usable; it is the SNAPSHOT that must not silently contain "
                "a reconstruction."
            ),
        )
    for col in _OVERLAYS[table]:
        if row[col] is not None:
            rec[col] = bool(row[col]) if col in _BOOL_COLS else row[col]
    return rec


def _repair(row, table: str) -> dict[str, Any]:
    """Reconstruct a payload-less row from its columns, or raise saying why not."""
    rebuild = _rebuilders().get(table)
    if rebuild is None:
        raise _refusal(
            row,
            table,
            detail=(
                f"no rebuild rule exists for {table}: its record may carry "
                "keys that are not columns, so a column-based rebuild could "
                "silently drop them."
            ),
        )
    rebuilt = rebuild(row)
    if rebuilt is None:
        raise _refusal(
            row,
            table,
            detail=(
                "it cannot be rebuilt from its own columns either — a NOT NULL "
                "column is empty, so the row carries no recoverable record."
            ),
        )
    logger.warning(
        "!! %s row %r%s HAS NO record_json PAYLOAD and was rebuilt from its "
        "columns for this read. The row is UNCHANGED on disk and will be "
        "rebuilt again on every read until it is repaired in place. A row this "
        "package wrote should never lack a payload: if the timestamp is "
        "recent, a WRITER omitted it and that is a bug worth reporting.",
        table,
        row["id"],
        f" written {_written_at(row, table)}" if _written_at(row, table) else "",
    )
    return rebuilt


#: Policy for a row that carries no payload AND cannot be rebuilt.
#:
#: ``"raise"`` (the default) refuses the WHOLE export. That is correct for two
#: of this function's three callers and MUST stay the default:
#:
#:   backup rail   ADR-0010 says a snapshot is exact or absent.
#:   READ-MODIFY-WRITE  whatever this returns is written back as the whole
#:                      store, so omitting a row DELETES it. Measured
#:                      2026-08-17: making the users loop tolerant and running
#:                      one `comment_task` reported SUCCESS and left the row
#:                      gone. Pinned by
#:                      tests/…/test__rmw_refusal_must_not_become_tolerance.py.
#:
#: ``"omit"`` is for a PURE read — one that never writes the document back.
#: There, refusing the whole result set over a row the caller does not even
#: return is the larger harm: an unreadable `users` row blanks `list_tasks`,
#: and `help_wait` (the card an agent files to say it is stuck) is refused at
#: exactly the moment it is needed.
_ON_UNREBUILDABLE = ("raise", "omit")


def _omit_or_raise(exc: "ExportRefused", policy: str, table: str, row_id) -> None:
    """Re-raise, or NAME the row being skipped. Never drop one in silence."""
    if policy != "omit":
        raise exc
    logger.warning(
        "[scitex-cards] SKIPPED an unreadable %s row %r on a read-only query: "
        "%s — the rest of the result is complete. This row is NOT deleted and "
        "a WRITE will still refuse until its payload is repaired.",
        table,
        row_id,
        exc,
    )


def export_doc(
    db_path: str | Path | None = None,
    *,
    conn: StoreConnection | None = None,
    repair: bool = True,
    on_unrebuildable: str = "raise",
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
        # `last_activity` is selected for the REFUSAL, not for the record: a
        # payload-less row's own timestamp is what says whether it is an old
        # row or one a current writer just broke, and those need opposite
        # responses. The record itself still comes from the verbatim payload.
        for r in conn.execute(
            "SELECT id, card_json, last_activity FROM tasks ORDER BY row_order"
        ).fetchall():
            if r["card_json"] is None:
                stamp = r["last_activity"]
                when = f" (last activity {stamp})" if stamp else ""
                _omit_or_raise(ExportRefused(
                    f"task {r['id']!r}{when} has no card_json payload, and a "
                    "card CANNOT be rebuilt from its columns: 22 distinct card "
                    "keys measured on the live store are not columns at all, "
                    "so a rebuild would drop them silently.\n"
                    "\n"
                    "Two different faults look like this:\n"
                    "  * an OLD row predating the payload columns — re-import "
                    "the database from an export written by a current "
                    "version;\n"
                    "  * a row a CURRENT writer stored without a payload — "
                    "that is a WRITER defect; report it with the id and "
                    "timestamp above.\n"
                    "\n"
                    "Nothing was deleted or modified. Inspect the row with:\n"
                    f"  SELECT * FROM tasks WHERE id = '{r['id']}';"
                ), on_unrebuildable, "tasks", r["id"])
                continue
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
        users = []
        for r in conn.execute(
            "SELECT * FROM users ORDER BY created_at, id"
        ).fetchall():
            try:
                users.append(_record(r, "users", repair=repair))
            except ExportRefused as exc:
                _omit_or_raise(exc, on_unrebuildable, "users", r["id"])

        # Seed from the recipients table first so a DRAINED inbox (a
        # key with zero rows) still appears as an empty list (v4).
        inboxes: dict[str, list[dict]] = {
            r["recipient_id"]: []
            for r in conn.execute(
                "SELECT recipient_id FROM inbox_recipients ORDER BY recipient_id"
            ).fetchall()
        }
        for r in conn.execute("SELECT * FROM notifications ORDER BY ts, id").fetchall():
            try:
                rec = _record(r, "notifications", repair=repair)
            except ExportRefused as exc:
                _omit_or_raise(exc, on_unrebuildable, "notifications", r["id"])
                continue
            inboxes.setdefault(r["recipient_id"], []).append(rec)

        threads: dict[str, list[dict]] = {}
        for r in conn.execute("SELECT * FROM messages ORDER BY ts, id").fetchall():
            try:
                rec = _record(r, "messages", repair=repair)
            except ExportRefused as exc:
                _omit_or_raise(exc, on_unrebuildable, "messages", r["id"])
                continue
            threads.setdefault(r["thread_key"], []).append(rec)
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

    # STRICT: a snapshot is exact or it is absent (ADR-0010). The board read
    # takes the same function with repair=True, because a board that dies over
    # one damaged inbox row is a fleet outage; a backup that silently contains
    # a rebuilt record is a restore that quietly loses what the writer stored.
    doc, threads = export_doc(db_path, repair=False)

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
