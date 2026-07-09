#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Storage layer for the CARDS + COMMENTS SQLite backend (Phase 2, slice A).

DB path resolution, connection helper, schema DDL, and Task-dict <-> DB-row
conversion. Split out of ``_store_sqlite.py`` to respect the 512-line
per-file cap; see that module's docstring for the full Phase-2 rationale
(why cards/comments are moving off the monolithic YAML store) and
``_store_sqlite_migrate.py`` for the lazy YAML->SQLite migration.

Two tables live in ``<store_dir>/runtime/todo.db`` — the SAME physical file
Phase 1's inbox backend (``_inbox_sqlite.py``) uses (one runtime DB per
package, per the constitution's runtime-state-db convention):

* ``tasks``    — one row per card, indexed on ``status`` + ``agent``.
* ``comments`` — one row per comment, indexed on ``task_id``.

``meta`` (shared with the inbox table) carries this module's OWN keys
(``migrated_from_yaml_tasks`` / ``schema_version_tasks``) so the two lazy
migrations never collide despite sharing the table.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: Env override for the store DB path (full path to the ``.db`` file).
#: Default is ``<store_dir>/runtime/todo.db``.
ENV_STORE_DB = "SCITEX_TODO_STORE_DB"

#: Runtime-DB filename (``todo`` = this package's short name).
_DB_FILENAME = "todo.db"

#: Schema version for the tasks/comments tables (independent of the inbox
#: table's own schema version — distinct ``meta`` key, see below).
SCHEMA_VERSION = 1

#: ``meta`` key recording this module's schema version. Distinct from the
#: inbox table's bare ``schema_version`` key.
_SCHEMA_VERSION_KEY = "schema_version_tasks"

#: Task-dataclass fields stored as columns on the ``tasks`` table, in column
#: order (mirrors ``scitex_todo._model.Task`` minus ``comments``, which lives
#: in its own table). ``group`` is a SQL-reserved-ish word — quoted wherever
#: it appears in a column list.
TASK_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "task",
    "project",
    "repo",
    "host",
    "created_at",
    "goal",
    "deadline",
    "scheduled",
    "deadlines",
    "status",
    "agent",
    "group",
    "last_activity",
    "blocker",
    "pr_url",
    "issue_url",
    "depends_on",
    "blocks",
    "parent",
    "priority",
    "note",
    "collaborators",
    "subscribers",
    "created_by",
    "kind",
    "job_id",
    "command",
    "started_at",
    "finished_at",
    "scope",
    "assignee",
    "log_meta",
)

#: Fields persisted as JSON text (lists / dicts).
JSON_FIELDS = frozenset(
    {"deadlines", "depends_on", "blocks", "collaborators", "subscribers", "log_meta"}
)

#: Column names that need double-quoting in SQL (reserved-ish words).
_QUOTED_COLUMNS = frozenset({"group"})

#: Task-dict key -> DB column name (only ``_log_meta`` differs).
_FIELD_TO_COLUMN = {"_log_meta": "log_meta"}
_COLUMN_TO_FIELD = {v: k for k, v in _FIELD_TO_COLUMN.items()}


def _col(name: str) -> str:
    """Quote a column name if it needs it (e.g. ``group``)."""
    return f'"{name}"' if name in _QUOTED_COLUMNS else name


def _dict_key_for_column(col: str) -> str:
    """Map a DB column name back to the Task-dict key it represents."""
    return _COLUMN_TO_FIELD.get(col, col)


def _column_for_dict_key(key: str) -> str:
    """Map a Task-dict key to the DB column name that stores it."""
    return _FIELD_TO_COLUMN.get(key, key)


# --------------------------------------------------------------------------- #
# DB path + connection                                                        #
# --------------------------------------------------------------------------- #
def store_db_path(store: str | Path | None = None) -> Path:
    """Resolved on-disk path for the tasks/comments SQLite DB.

    ``SCITEX_TODO_STORE_DB`` wins outright; otherwise the DB lives at
    ``runtime_dir(store)/todo.db`` — the SAME file Phase 1's inbox backend
    uses, so a per-test ``store=`` isolates both backends' DB together.
    """
    override = os.environ.get(ENV_STORE_DB)
    if override:
        return Path(override).expanduser()
    from ._paths import runtime_dir

    return runtime_dir(store, create=True) / _DB_FILENAME


@contextmanager
def open_connection(path: Path | None = None):
    """Open the store DB (WAL, ``Row`` factory). Mirrors ``_inbox_sqlite``."""
    target = Path(path) if path is not None else store_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the ``tasks`` + ``comments`` tables + indexes idempotently."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            task TEXT,
            project TEXT,
            repo TEXT,
            host TEXT,
            created_at TEXT,
            goal TEXT,
            deadline TEXT,
            scheduled TEXT,
            deadlines TEXT,
            status TEXT NOT NULL,
            agent TEXT,
            "group" TEXT,
            last_activity TEXT,
            blocker TEXT,
            pr_url TEXT,
            issue_url TEXT,
            depends_on TEXT,
            blocks TEXT,
            parent TEXT,
            priority INTEGER,
            note TEXT,
            collaborators TEXT,
            subscribers TEXT,
            created_by TEXT,
            kind TEXT,
            job_id TEXT,
            command TEXT,
            started_at TEXT,
            finished_at TEXT,
            scope TEXT,
            assignee TEXT,
            log_meta TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            text TEXT NOT NULL,
            by TEXT,
            kind TEXT,
            ts TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_comments_task_id ON comments(task_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_SCHEMA_VERSION_KEY, str(SCHEMA_VERSION)),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Row <-> dict conversion                                                     #
# --------------------------------------------------------------------------- #
def task_dict_to_row_values(task: dict) -> tuple:
    values = []
    for col in TASK_FIELDS:
        key = _dict_key_for_column(col)
        v = task.get(key)
        if col in JSON_FIELDS:
            v = json.dumps(v) if v is not None else None
        values.append(v)
    return tuple(values)


def upsert_task_row(conn: sqlite3.Connection, task: dict) -> None:
    """Write ``task`` as a full-row REPLACE (never touches ``comments``)."""
    cols = ", ".join(_col(c) for c in TASK_FIELDS)
    placeholders = ", ".join("?" for _ in TASK_FIELDS)
    conn.execute(
        f"INSERT OR REPLACE INTO tasks({cols}) VALUES({placeholders})",
        task_dict_to_row_values(task),
    )


def row_to_task(row: sqlite3.Row) -> dict:
    """Project a DB row back to a compact Task-shape dict.

    Mirrors ``Task.to_dict``'s compaction: ``id``/``title``/``status`` are
    always emitted (required), everything else is OMITTED when
    ``None``/empty so the returned dict matches the YAML path's shape.
    """
    out: dict[str, Any] = {}
    for col in TASK_FIELDS:
        key = _dict_key_for_column(col)
        v = row[col]
        if col in JSON_FIELDS:
            v = json.loads(v) if v is not None else None
        if key in ("id", "title", "status"):
            out[key] = v
            continue
        if v is None:
            continue
        if isinstance(v, (list, dict)) and not v:
            continue
        out[key] = v
    return out


def fetch_comments(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT text, by, kind, ts FROM comments WHERE task_id = ? ORDER BY id",
        (task_id,),
    ).fetchall()
    out = []
    for r in rows:
        entry = {"author": r["by"], "ts": r["ts"], "text": r["text"]}
        if r["kind"]:
            entry["kind"] = r["kind"]
        out.append(entry)
    return out


__all__ = [
    "ENV_STORE_DB",
    "SCHEMA_VERSION",
    "TASK_FIELDS",
    "JSON_FIELDS",
    "fetch_comments",
    "init_schema",
    "open_connection",
    "row_to_task",
    "store_db_path",
    "task_dict_to_row_values",
    "upsert_task_row",
]

# EOF
