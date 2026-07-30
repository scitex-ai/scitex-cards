#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-time migration of file-backed inbox records into the SQLite inbox.

Split out of :mod:`scitex_cards._inbox_sqlite`, which had reached its size
budget. The seam is a real one and worth naming: everything here runs ONCE per
store, ever, while the module it came from runs on every poll, enqueue and ack.
:func:`scitex_cards._inbox_sqlite._ensure_ready` guards this behind the
``migrated_from_yaml`` meta flag, so the steady state never reaches it.

This is also the part with the shortest remaining life. Once no reachable store
predates the SQLite inbox, this module is deleted outright — and keeping that
code beside the hot path is what pushed the original file to its cap.

Imports are re-exported from ``_inbox_sqlite``, so
``from ._inbox_sqlite import migrate_to_sqlite`` keeps resolving. No behaviour
changed in the move.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _read_legacy_embedded_inboxes(path: Path) -> dict[str, list[dict]]:
    """Read the LEGACY embedded ``inboxes:`` section off the pre-cutover
    monolithic task-store document (absent / malformed -> {}). Shared by
    the one-time JSON-sidecar migration and the one-time SQLite migration.
    """
    from ._inbox import _INBOXES_KEY

    if not path.exists():
        return {}
    from ._yaml import safe_load

    try:
        with path.open(encoding="utf-8") as handle:
            data = safe_load(handle) or {}
    except (UnicodeDecodeError, ValueError, OSError):
        # THE STORE IS SQLITE NOW, so "the legacy document" is a binary file
        # and reading it as UTF-8 YAML raises rather than returning nothing.
        # This function's contract has always been "absent / malformed -> {}";
        # it just never covered malformed-because-binary.
        #
        # It matters because a FRESH store hits it on its very first inbox
        # access: the one-time migration runs (no `migrated_from_yaml` flag
        # yet), reaches here, and raises — so the inbox cannot initialise at
        # all. Existing stores escape only because their flag was set back
        # when the store really was YAML. A brand-new host is precisely the
        # fresh-store case, so this is on the multi-host path.
        #
        # Returning {} is right, not a papering-over: a SQLite store HAS no
        # embedded `inboxes:` section, so "nothing to migrate" is the true
        # answer. The DB's own rows are read by the SQLite backend directly.
        return {}
    raw = data.get(_INBOXES_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for rid, records in raw.items():
        if not isinstance(rid, str) or not rid:
            continue
        out[rid] = (
            [r for r in records if isinstance(r, dict)]
            if isinstance(records, list)
            else []
        )
    return out


def _migrate_legacy_yaml_once(json_path: Path, legacy_doc_path: Path) -> None:
    """Fold a legacy EMBEDDED ``inboxes:`` section into ``inboxes.json``, once.

    No-op unless ``json_path`` is absent AND the legacy document has data.
    No permanent YAML fallback: once ``inboxes.json`` exists, never fires
    again.
    """
    from ._inbox import _save_inboxes_unlocked

    if json_path.exists():
        return
    raw = _read_legacy_embedded_inboxes(legacy_doc_path)
    if raw:
        _save_inboxes_unlocked(raw, json_path)


def gather_migratable_inboxes(store: str | Path | None) -> dict[str, list]:
    """Read + merge every pre-existing file-backed inbox source, per recipient.

    Two sources so a store carries over regardless of which one an operator
    was using: the pre-cutover LEGACY embedded ``inboxes:`` section, and the
    break-glass ``inboxes.json`` sidecar. Read-only. Shared by
    :func:`_migrate_into_conn` and the CLI's ``--dry-run`` preview.
    """
    from ._inbox import _INBOXES_FILENAME, _load_inboxes_section, _resolved_store

    path = _resolved_store(store)
    inboxes: dict[str, list] = {}
    for recipient_id, records in _read_legacy_embedded_inboxes(path).items():
        inboxes.setdefault(recipient_id, []).extend(records)
    breakglass_path = path.parent / _INBOXES_FILENAME
    for recipient_id, records in _load_inboxes_section(breakglass_path).items():
        inboxes.setdefault(recipient_id, []).extend(records)
    return inboxes


def _migrate_into_conn(conn: sqlite3.Connection, store: str | Path | None) -> dict:
    """Copy pre-existing file-backed inbox records into ``conn``'s ``inbox`` table.

    The shared body of :func:`migrate_to_sqlite` (explicit CLI verb) and the
    lazy ``_ensure_ready`` guard. Dedups on the notification ``id`` PRIMARY KEY
    (``INSERT OR IGNORE``) so it is idempotent, copies BOTH seen + unseen for
    fidelity, and NEVER touches either source document (reversible). Assumes
    the schema already exists (caller ran ``init_schema``); does NOT commit —
    the caller owns the transaction. Returns
    ``{recipients, records, inserted, skipped}``.

    ``msg_id`` is deliberately NOT written here. A legacy file-backed record
    predates the column, so it has no message id to carry, and inventing one
    from ``(card_id, ts, actor)`` is the lossy join that
    ``_dm_receipt_state`` measured collapsing two distinct messages onto one
    notification. A migrated row reports "no message id", which is true.
    """
    inboxes = gather_migratable_inboxes(store)
    stats = {"recipients": 0, "records": 0, "inserted": 0, "skipped": 0}
    for recipient_id, records in inboxes.items():
        if not recipient_id or not isinstance(records, list):
            continue
        stats["recipients"] += 1
        for rec in records:
            if not isinstance(rec, dict):
                continue
            nid = rec.get("id")
            if not nid:
                # A record with no stable id cannot be deduped on re-run;
                # skip it rather than risk a duplicate on the next pass.
                logger.warning(
                    "[scitex-cards._inbox_migrate] skipping id-less inbox "
                    "record for %r during migration",
                    recipient_id,
                )
                stats["skipped"] += 1
                continue
            stats["records"] += 1
            cur = conn.execute(
                "INSERT OR IGNORE INTO inbox(id, recipient, event_type, "
                "card_id, body, actor, ts, seen) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    nid,
                    recipient_id,
                    rec.get("event_type"),
                    rec.get("card_id"),
                    rec.get("body"),
                    rec.get("actor"),
                    rec.get("ts"),
                    1 if rec.get("seen") else 0,
                ),
            )
            if cur.rowcount:
                stats["inserted"] += 1
            else:
                stats["skipped"] += 1
    return stats


def migrate_to_sqlite(store: str | Path | None = None) -> dict:
    """Copy the legacy embedded ``inboxes:`` records into the SQLite inbox DB.

    Idempotent + reversible: dedups on notification ``id`` (``INSERT OR
    IGNORE`` on the ``id`` PK) so a re-run inserts nothing new, and NEVER
    touches the legacy document (a rollback keeps working). All records are
    copied (seen + unseen) for fidelity. Returns a stats dict
    ``{recipients, records, inserted, skipped}``; also sets the
    ``migrated_from_yaml`` flag so a later lazy access treats the DB as
    already migrated (this verb and the lazy guard share the same flag).
    """
    from ._inbox import _utc_now_iso
    from ._inbox_sqlite import (
        _MIGRATED_FLAG,
        inbox_db_path,
        init_schema,
        open_connection,
    )

    with open_connection(inbox_db_path(store)) as conn:
        init_schema(conn)
        stats = _migrate_into_conn(conn, store)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
            (_MIGRATED_FLAG, _utc_now_iso()),
        )
        conn.commit()
    return stats


def info(store: str | Path | None = None) -> dict[str, Any]:
    """Return a small status dict for the CLI (``inbox info``-style)."""
    from ._inbox_sqlite import inbox_db_path, init_schema, open_connection

    db = inbox_db_path(store)
    if not db.exists():
        return {"path": str(db), "exists": False, "rows": 0, "unseen": 0}
    with open_connection(db) as conn:
        init_schema(conn)
        rows = conn.execute("SELECT COUNT(*) AS n FROM inbox").fetchone()["n"]
        unseen = conn.execute(
            "SELECT COUNT(*) AS n FROM inbox WHERE seen = 0"
        ).fetchone()["n"]
    return {"path": str(db), "exists": True, "rows": rows, "unseen": unseen}


__all__ = [
    "_migrate_into_conn",
    "_migrate_legacy_yaml_once",
    "_read_legacy_embedded_inboxes",
    "gather_migratable_inboxes",
    "info",
    "migrate_to_sqlite",
]

# EOF
