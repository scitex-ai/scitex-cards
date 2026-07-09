#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lazy one-time YAML -> SQLite migration for cards/comments (Phase 2, slice A).

Split out of ``_store_sqlite.py`` to respect the 512-line per-file cap.
Mirrors the lazy-migration shape of Phase 1's ``_inbox_sqlite._ensure_ready``
/ ``_migrate_into_conn`` exactly: a ``meta`` flag guards a one-time copy of
the YAML ``tasks:`` list (+ each task's ``comments``) into the SQLite
``tasks`` / ``comments`` tables. NEVER deletes or mutates the YAML file
(reversible); idempotent per task id (a re-run skips ids already present,
so re-running never duplicates comments either — they were copied the first
time that id was inserted).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ._store import _utc_now_iso
from ._store_sqlite_schema import init_schema, store_db_path, upsert_task_row

#: ``meta`` key set ONCE after the YAML ``tasks:`` list has been copied into
#: this DB. Distinct from Phase 1's ``migrated_from_yaml`` key (inbox) so the
#: two lazy-migration guards never collide despite sharing one ``meta`` table.
_MIGRATED_FLAG = "migrated_from_yaml_tasks"


def _is_migrated(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM meta WHERE key = ? LIMIT 1", (_MIGRATED_FLAG,)
    ).fetchone()
    return row is not None


def migrate_into_conn(conn: sqlite3.Connection, store: str | Path | None) -> dict:
    """Copy the YAML ``tasks:`` list (+ each task's ``comments``) into ``conn``.

    Idempotent PER TASK: a task id already present in the ``tasks`` table is
    skipped entirely (including its comments), so a re-run inserts nothing
    new. NEVER touches the YAML file. Does NOT commit — caller owns the
    transaction. Returns ``{tasks, comments, inserted, skipped}``.
    """
    from ._store import _resolved_store as _resolve

    path = _resolve(store)
    stats = {"tasks": 0, "comments": 0, "inserted": 0, "skipped": 0}
    if not path.exists():
        return stats
    from . import _model

    try:
        tasks = _model.load_tasks(path)
    except FileNotFoundError:
        return stats
    for task in tasks:
        if not isinstance(task, dict) or not task.get("id"):
            continue
        stats["tasks"] += 1
        tid = task["id"]
        existing = conn.execute(
            "SELECT 1 FROM tasks WHERE id = ? LIMIT 1", (tid,)
        ).fetchone()
        if existing is not None:
            stats["skipped"] += 1
            continue
        upsert_task_row(conn, task)
        stats["inserted"] += 1
        for c in task.get("comments") or []:
            if not isinstance(c, dict) or not c.get("text"):
                continue
            conn.execute(
                "INSERT INTO comments(task_id, text, by, kind, ts) "
                "VALUES(?, ?, ?, ?, ?)",
                (tid, str(c.get("text")), c.get("author"), c.get("kind"), c.get("ts")),
            )
            stats["comments"] += 1
    return stats


def ensure_ready(conn: sqlite3.Connection, store: str | Path | None) -> None:
    """Per-connection readiness: schema + lazy one-time migration.

    Guarded by the ``migrated_from_yaml_tasks`` meta flag: the first access
    performs the one-time copy + sets the flag; every later access is a
    cheap flag probe with no YAML read.
    """
    init_schema(conn)
    if _is_migrated(conn):
        return
    migrate_into_conn(conn, store)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
        (_MIGRATED_FLAG, _utc_now_iso()),
    )
    conn.commit()


def migrate_to_sqlite(store: str | Path | None = None) -> dict:
    """Explicit migration verb (mirrors ``_inbox_sqlite.migrate_to_sqlite``).

    Idempotent + reversible — see :func:`migrate_into_conn`. Also sets the
    ``migrated_from_yaml_tasks`` flag so a later lazy access treats the DB as
    already migrated (this verb and the lazy guard share the same flag).
    """
    from ._store_sqlite_schema import open_connection

    with open_connection(store_db_path(store)) as conn:
        init_schema(conn)
        stats = migrate_into_conn(conn, store)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
            (_MIGRATED_FLAG, _utc_now_iso()),
        )
        conn.commit()
    return stats


__all__ = ["ensure_ready", "migrate_into_conn", "migrate_to_sqlite"]

# EOF
