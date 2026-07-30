#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite derived-index for the scitex-todo board (PR-B of the lead-
approved Stage 2 plan, lead a2a `aa02fb0e` / `e5243003`).

The task store stays authoritative. This SQLite file is a SEPARATE
read-cache (a derived FTS/search index, not the store itself), never
written to by the writer path. Indexing is idempotent + rebuildable:
``scitex-todo index rebuild`` drops + repopulates from the store(s)
in a single transaction. Schema migrations move the
``meta.index_version`` forward; older versions are dropped and
rebuilt.

Multi-source: the indexer scans the global store (resolved by
:func:`scitex_cards._paths.resolve_tasks_path`) PLUS every per-project
lane discovered by :func:`scitex_cards._django.services._discover_lanes`
— same union policy as PR #137 (project-lane wins on id collision,
logged at WARNING).

Out of scope here: the file-watcher daemon + SSE wire (PR-C) and the
/graph SQL flip (PR-D). This PR delivers the durable backing store +
the rebuild CLI so other PRs build on a working index.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

#: Env override for the index file path. Default is sibling to the
#: global task store: ``~/.scitex/todo/.tasks.index.sqlite``.
ENV_INDEX_PATH = "SCITEX_TODO_INDEX_PATH"

#: Schema version. Bump when the column set or indexes change; the
#: rebuilder drops + recreates when a stored ``meta.index_version`` is
#: lower than this. (v2: the source-path/mtime columns were renamed to
#: drop their format-specific names — the source is the store, not a
#: particular file format.)
SCHEMA_VERSION = 2


def index_path() -> Path:
    """Resolved on-disk path for the SQLite index file."""
    override = os.environ.get(ENV_INDEX_PATH)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".scitex" / "todo" / ".tasks.index.sqlite"


@contextmanager
def open_connection(path: Optional[Path] = None):
    """Open the index DB (WAL, isolation level autocommit).

    Caller-managed: closes on context exit. Creates the parent dir
    if missing — friendly for first-run.
    """
    target = path or index_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the four index tables idempotently.

    Schema:
      - ``tasks`` — the per-task row, sourced from the store.
      - ``tasks_fts`` — FTS5 virtual table over title + note for fuzzy
        search. The /graph SQL flip (PR-D) will use this in MATCH.
      - ``tags`` — placeholder for the labels/tags surface operator-12911
        asked about (no labels: field in the store today; this table lets
        the DB carry them when the schema adds them, no migration).
      - ``meta`` — key/value: last_index_at, store_mtime, index_version.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            status TEXT,
            priority INTEGER,
            project TEXT,
            agent TEXT,
            host TEXT,
            kind TEXT,
            blocker TEXT,
            scope TEXT,
            parent TEXT,
            repo TEXT,
            pr_url TEXT,
            deadline TEXT,
            last_activity TEXT,
            created_at TEXT,
            note TEXT,
            source_path TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
    # FTS5 — virtual table mirrors the (id, title, note) tuple.
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
                id UNINDEXED, title, note,
                content='tasks', content_rowid='rowid'
            )
            """
        )
    except sqlite3.OperationalError as e:
        # FTS5 may be absent on older SQLite builds — degrade
        # gracefully; the rest of the index still works.
        logger.warning(
            "[scitex-todo._index] FTS5 unavailable, skipping (%s)",
            e,
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tags (
            task_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (task_id, tag)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?",
        (key,),
    ).fetchone()
    return None if row is None else row["value"]


def _coerce_priority(value) -> Optional[int]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _flatten_task(task: dict, source: Path) -> dict:
    """Project a YAML task dict into the SQLite column shape."""
    return {
        "id": task.get("id"),
        "title": task.get("title"),
        "status": task.get("status"),
        "priority": _coerce_priority(task.get("priority")),
        "project": task.get("project"),
        "agent": task.get("agent") or task.get("assignee"),
        "host": task.get("host"),
        "kind": task.get("kind"),
        "blocker": task.get("blocker"),
        "scope": task.get("scope"),
        "parent": task.get("parent"),
        "repo": task.get("repo"),
        "pr_url": task.get("pr_url"),
        "deadline": task.get("deadline"),
        "last_activity": task.get("last_activity"),
        "created_at": task.get("created_at"),
        "note": task.get("note"),
        "source_path": str(source),
    }


def _insert_task(conn: sqlite3.Connection, row: dict) -> None:
    columns = list(row.keys())
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        f"INSERT OR REPLACE INTO tasks({','.join(columns)}) VALUES({placeholders})",
        [row[c] for c in columns],
    )
    # Best-effort FTS upsert (skip when FTS5 isn't available).
    try:
        conn.execute(
            "INSERT OR REPLACE INTO tasks_fts(id, title, note) VALUES(?, ?, ?)",
            (row["id"], row["title"] or "", row["note"] or ""),
        )
    except sqlite3.OperationalError:
        pass


def rebuild_index(
    global_path: Optional[Path] = None,
    lane_paths: Optional[Iterable[Path]] = None,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, int]:
    """Drop + repopulate the index from the YAML source(s).

    Same union policy as PR #137: global tasks load first; per-project
    lanes overlay on id collision (logged at WARNING). The rebuild is
    a single transaction so a partial failure leaves the DB on the
    PRIOR (already-known-good) snapshot.

    Returns a small stats dict so the CLI can print "rebuilt N tasks
    from M lanes" with a real number.
    """
    from scitex_cards._model import load_tasks
    from scitex_cards._paths import resolve_tasks_path

    if global_path is None:
        global_path = resolve_tasks_path(None)
    if lane_paths is None:
        from scitex_cards._django.services import _discover_lanes

        lane_paths = _discover_lanes()
    lane_list = list(lane_paths)

    own_conn = conn is None
    if own_conn:
        ctx = open_connection()
    else:
        from contextlib import nullcontext

        ctx = nullcontext(conn)

    with ctx as c:
        init_schema(c)
        # Drop the prior snapshot.
        c.execute("DELETE FROM tasks")
        try:
            c.execute("DELETE FROM tasks_fts")
        except sqlite3.OperationalError:
            pass
        c.execute("DELETE FROM tags")

        stats = {"global": 0, "lanes": 0, "total": 0, "skipped": 0}
        # GLOBAL first so per-project lanes overlay (skill 30 union).
        try:
            global_tasks = load_tasks(global_path) if global_path.exists() else []
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[scitex-todo._index] global store unreadable %s: %s",
                global_path,
                exc,
            )
            global_tasks = []
        for t in global_tasks:
            if not isinstance(t, dict) or not t.get("id"):
                stats["skipped"] += 1
                continue
            _insert_task(c, _flatten_task(t, global_path))
            stats["global"] += 1
        # Lanes — each overlay row is logged on collision.
        for lp in lane_list:
            try:
                lane_tasks = load_tasks(lp)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[scitex-todo._index] lane %s unreadable: %s",
                    lp,
                    exc,
                )
                continue
            for t in lane_tasks:
                if not isinstance(t, dict) or not t.get("id"):
                    stats["skipped"] += 1
                    continue
                # Detect collision (purely for the log line — the
                # INSERT OR REPLACE handles the overlay).
                prior = c.execute(
                    "SELECT source_path FROM tasks WHERE id = ?",
                    (t["id"],),
                ).fetchone()
                if prior is not None and prior["source_path"] != str(lp):
                    logger.warning(
                        "[scitex-todo._index] id %r collision — %s overrides %s",
                        t["id"],
                        lp,
                        prior["source_path"],
                    )
                _insert_task(c, _flatten_task(t, lp))
                stats["lanes"] += 1
        stats["total"] = stats["global"] + stats["lanes"]

        # Update meta.
        _set_meta(c, "index_version", str(SCHEMA_VERSION))
        _set_meta(c, "last_index_at", str(time.time()))
        # store_mtime = MAX across all sources, mirroring PR #137 +
        # PR #136's cache-key contract.
        mtimes: List[float] = []
        if global_path.exists():
            mtimes.append(global_path.stat().st_mtime)
        for lp in lane_list:
            try:
                mtimes.append(lp.stat().st_mtime)
            except OSError:
                continue
        _set_meta(c, "store_mtime", str(max(mtimes)) if mtimes else "0")
        _set_meta(c, "lane_count", str(len(lane_list)))
        c.commit()

    return stats


def info() -> Dict[str, object]:
    """Return a dict suitable for ``index info --json``."""
    p = index_path()
    if not p.exists():
        return {
            "path": str(p),
            "exists": False,
            "rows": 0,
            "index_version": None,
            "last_index_at": None,
            "store_mtime": None,
            "lane_count": 0,
        }
    with open_connection(p) as c:
        init_schema(c)  # idempotent — for first-run `index info`.
        rows = c.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        return {
            "path": str(p),
            "exists": True,
            "rows": rows,
            "index_version": _get_meta(c, "index_version"),
            "last_index_at": _get_meta(c, "last_index_at"),
            "store_mtime": _get_meta(c, "store_mtime"),
            "lane_count": int(_get_meta(c, "lane_count") or 0),
        }


def query_tasks(
    *,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    status: Optional[str] = None,
    overdue: bool = False,
) -> List[dict]:
    """Tiny read-path the /graph SQL flip (PR-D) will build on.

    Filters compose AND. Returns plain dicts. Today's caller is the
    test suite (proves the index is consultable); PR-D adds the
    routing logic that prefers this path over the YAML walk.
    """
    where = []
    params: List = []
    if project is not None:
        where.append("project = ?")
        params.append(project)
    if agent is not None:
        where.append("agent = ?")
        params.append(agent)
    if status is not None:
        where.append("status = ?")
        params.append(status)
    sql = "SELECT * FROM tasks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY priority IS NULL, priority, id"
    out: List[dict] = []
    p = index_path()
    if not p.exists():
        return out
    with open_connection(p) as c:
        init_schema(c)
        for row in c.execute(sql, params):
            out.append({k: row[k] for k in row.keys()})
    if overdue:
        # Overdue filter handled in Python to reuse `is_overdue` (the
        # same rule the YAML side uses) — keeps a single source of
        # truth for the predicate. PR-D will move this into SQL.
        from scitex_cards._model import is_overdue

        out = [r for r in out if is_overdue(r)]
    return out
