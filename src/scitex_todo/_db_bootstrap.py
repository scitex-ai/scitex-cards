#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YAML → shadow-SQLite bootstrap import — STAGE S0 (RFC #348).

Reads the CURRENT canonical YAML store (``tasks.yaml`` — tasks + users +
inboxes — plus the ``threads.yaml`` sidecar) via the existing load path and
populates the shadow SQLite DB across every table. The YAML is the source of
truth and is opened READ-ONLY here; the import NEVER writes back to it (the S0
safety boundary — verified byte-for-byte by the tests).

Idempotency
-----------
The import is a FULL REBUILD inside one transaction: it clears every data
table and re-inserts from the YAML. Re-running therefore yields byte-identical
DB state (``import --from-yaml`` twice == once). This is the simplest correct
idempotency for a SHADOW that is, by definition, a projection of the YAML —
there is no DB-only state to preserve in S0.

Field mapping (see :mod:`scitex_todo._db` for the schema rationale):
  * scalar Task fields → columns (``group`` → ``grp``; SQL reserved word),
  * ``deadlines`` / ``_log_meta`` → JSON TEXT columns,
  * ``comments`` → ``task_comments`` (``seq`` = position),
  * ``depends_on`` / ``blocks`` → ``task_edges`` (directional),
  * ``collaborators`` / ``subscribers`` → ``task_roles``,
  * ``users`` → ``users`` + ``user_names`` (alias fan-out),
  * ``inboxes`` map → ``notifications`` (``recipient_id`` = map key),
  * ``threads.yaml`` map → ``messages`` (``thread_key`` = map key).
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from pathlib import Path

from ._db import SCHEMA_VERSION, connect, init_schema, resolve_db_path
from ._paths import resolve_tasks_path

#: (column, yaml-key) pairs for the scalar ``tasks`` columns. ``group`` maps to
#: the ``grp`` column (SQL reserved word); ``deadlines`` / ``_log_meta`` /
#: ``row_order`` are handled separately (JSON / positional).
_TASK_SCALAR_COLS: tuple[tuple[str, str], ...] = (
    ("id", "id"),
    ("title", "title"),
    ("status", "status"),
    ("kind", "kind"),
    ("blocker", "blocker"),
    ("task", "task"),
    ("note", "note"),
    ("goal", "goal"),
    ("project", "project"),
    ("repo", "repo"),
    ("host", "host"),
    ("agent", "agent"),
    ("assignee", "assignee"),
    ("scope", "scope"),
    ("grp", "group"),
    ("priority", "priority"),
    ("parent", "parent"),
    ("pr_url", "pr_url"),
    ("issue_url", "issue_url"),
    ("deadline", "deadline"),
    ("scheduled", "scheduled"),
    ("created_at", "created_at"),
    ("last_activity", "last_activity"),
    ("started_at", "started_at"),
    ("finished_at", "finished_at"),
    ("created_by", "created_by"),
    ("job_id", "job_id"),
    ("command", "command"),
)

#: Data tables cleared before a rebuild, child-before-parent so the explicit
#: deletes never fight the FK order (cascade would also cover the children).
_CLEAR_ORDER: tuple[str, ...] = (
    "task_comments",
    "task_edges",
    "task_roles",
    "tasks",
    "user_names",
    "users",
    "notifications",
    "messages",
)


def _gen_id(prefix: str) -> str:
    """Fallback id (``<prefix>`` + 12 hex) for a record missing its own id."""
    return prefix + secrets.token_hex(6)


def _json_or_none(value) -> str | None:
    """Serialize a non-empty list/dict to compact JSON, else ``None``."""
    if value in (None, [], {}):
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_source(tasks_path: str | Path | None) -> tuple[dict, Path]:
    """Load the YAML doc (tasks/users/inboxes) + return the resolved store path.

    Uses the existing ``load_doc`` read primitive (no validation gate — a
    bootstrap must ingest whatever is on disk). A missing store yields an
    empty doc so ``db import`` on a fresh install is a no-op, not a crash.
    """
    from ._model import load_doc

    path = resolve_tasks_path(tasks_path)
    if not path.exists():
        return {}, path
    doc = load_doc(path, validate=False)
    return (doc if isinstance(doc, dict) else {}), path


def _load_threads(store_path: Path) -> dict[str, list[dict]]:
    """Load the ``threads.yaml`` sidecar map (absent → empty)."""
    from . import _threads

    tpath = _threads.threads_path(store_path)
    return _threads._load_threads(tpath)


def _insert_tasks(conn: sqlite3.Connection, tasks: list) -> dict[str, int]:
    counts = {"tasks": 0, "comments": 0, "edges": 0, "roles": 0}
    cols = [c for c, _ in _TASK_SCALAR_COLS] + [
        "deadlines_json",
        "log_meta_json",
        "row_order",
    ]
    placeholders = ", ".join("?" for _ in cols)
    insert_sql = (
        f"INSERT OR REPLACE INTO tasks ({', '.join(cols)}) "
        f"VALUES ({placeholders})"
    )
    for order, row in enumerate(tasks):
        if not isinstance(row, dict):
            continue
        values = [row.get(ykey) for _, ykey in _TASK_SCALAR_COLS]
        values.append(_json_or_none(row.get("deadlines")))
        values.append(_json_or_none(row.get("_log_meta")))
        values.append(order)
        conn.execute(insert_sql, values)
        counts["tasks"] += 1
        tid = row.get("id")
        counts["comments"] += _insert_comments(conn, tid, row.get("comments"))
        counts["edges"] += _insert_edges(conn, tid, row)
        counts["roles"] += _insert_roles(conn, tid, row)
    return counts


def _insert_comments(conn, task_id, comments) -> int:
    if not isinstance(comments, list):
        return 0
    n = 0
    for seq, c in enumerate(comments):
        if not isinstance(c, dict):
            continue
        conn.execute(
            "INSERT INTO task_comments(task_id, seq, author, ts, kind, text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                task_id,
                seq,
                c.get("author"),
                c.get("ts"),
                c.get("kind"),
                "" if c.get("text") is None else str(c.get("text")),
            ),
        )
        n += 1
    return n


def _insert_edges(conn, task_id, row) -> int:
    n = 0
    for edge_type in ("depends_on", "blocks"):
        targets = row.get(edge_type)
        if not isinstance(targets, list):
            continue
        for dst in targets:
            if not (isinstance(dst, str) and dst):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO task_edges"
                "(src_task_id, dst_task_id, edge_type) VALUES (?, ?, ?)",
                (task_id, dst, edge_type),
            )
            n += 1
    return n


def _insert_roles(conn, task_id, row) -> int:
    n = 0
    for role, key in (("collaborator", "collaborators"), ("subscriber", "subscribers")):
        members = row.get(key)
        if not isinstance(members, list):
            continue
        for who in members:
            if not (isinstance(who, str) and who):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO task_roles(task_id, who, role) "
                "VALUES (?, ?, ?)",
                (task_id, who, role),
            )
            n += 1
    return n


def _insert_users(conn, users: list) -> dict[str, int]:
    counts = {"users": 0, "user_names": 0}
    if not isinstance(users, list):
        return counts
    for u in users:
        if not isinstance(u, dict):
            continue
        uid = u.get("id")
        if not (isinstance(uid, str) and uid):
            continue
        conn.execute(
            "INSERT OR REPLACE INTO users"
            "(id, kind, host_at_name, notify_json, turn_url, a2a_port, "
            " created_at, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uid,
                u.get("kind"),
                u.get("host_at_name"),
                _json_or_none(u.get("notify")),
                u.get("turn_url"),
                u.get("a2a_port"),
                u.get("created_at"),
                u.get("last_seen"),
            ),
        )
        counts["users"] += 1
        names = u.get("names")
        if isinstance(names, list):
            for name in names:
                if not (isinstance(name, str) and name):
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO user_names(name, user_id) "
                    "VALUES (?, ?)",
                    (name, uid),
                )
                counts["user_names"] += 1
    return counts


def _insert_notifications(conn, inboxes) -> int:
    if not isinstance(inboxes, dict):
        return 0
    n = 0
    for recipient_id, records in inboxes.items():
        if not (isinstance(recipient_id, str) and recipient_id):
            continue
        if not isinstance(records, list):
            continue
        for r in records:
            if not isinstance(r, dict):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO notifications"
                "(id, recipient_id, event_type, card_id, body, actor, ts, seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.get("id") or _gen_id("n_"),
                    recipient_id,
                    "" if r.get("event_type") is None else str(r.get("event_type")),
                    r.get("card_id"),
                    r.get("body"),
                    r.get("actor"),
                    "" if r.get("ts") is None else str(r.get("ts")),
                    1 if r.get("seen") else 0,
                ),
            )
            n += 1
    return n


def _insert_messages(conn, threads) -> int:
    if not isinstance(threads, dict):
        return 0
    n = 0
    for thread_key, records in threads.items():
        if not (isinstance(thread_key, str) and thread_key):
            continue
        if not isinstance(records, list):
            continue
        for r in records:
            if not isinstance(r, dict):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO messages"
                "(id, thread_key, sender, recipient, body, ts, read)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    r.get("id") or _gen_id("m_"),
                    thread_key,
                    "" if r.get("from") is None else str(r.get("from")),
                    "" if r.get("to") is None else str(r.get("to")),
                    "" if r.get("body") is None else str(r.get("body")),
                    "" if r.get("ts") is None else str(r.get("ts")),
                    1 if r.get("read") else 0,
                ),
            )
            n += 1
    return n


#: Tables owned by the ``tasks.yaml`` doc. The ``messages`` table is DELIBERATELY
#: absent: it is derived from the ``threads.yaml`` SIDECAR, which the doc-write
#: path never touches. A doc mirror that cleared ``messages`` would silently
#: destroy every DM thread on each card write — the tables must be owned by
#: exactly the file that produces them.
_DOC_CLEAR_ORDER = tuple(t for t in _CLEAR_ORDER if t != "messages")


def _rebuild_from_doc(
    conn: sqlite3.Connection,
    doc: dict,
    *,
    threads: dict[str, list[dict]] | None = None,
) -> dict:
    """Rebuild the doc-derived tables from an ALREADY-PARSED doc, in ONE txn.

    The shared core of :func:`import_from_yaml` (which reads the doc off disk)
    and :func:`mirror_doc` (S1 dual-write, which already holds the doc in memory
    under the store lock and must NOT pay an 11-second re-parse to get it back).

    ``threads`` rebuilds the ``messages`` table too. Pass it ONLY when the caller
    genuinely owns the threads sidecar — the dual-write path does not, and
    clearing ``messages`` there would wipe every DM on every card write.

    Caller owns the transaction boundary and the connection.
    """
    clear = _CLEAR_ORDER if threads is not None else _DOC_CLEAR_ORDER
    for table in clear:
        conn.execute(f"DELETE FROM {table}")

    summary: dict = {}
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    summary.update(_insert_tasks(conn, tasks if isinstance(tasks, list) else []))
    summary.update(_insert_users(conn, doc.get("users")))
    summary["notifications"] = _insert_notifications(conn, doc.get("inboxes"))
    if threads is not None:
        summary["messages"] = _insert_messages(conn, threads)
    return summary


def _stamp_meta(conn: sqlite3.Connection, source: str) -> None:
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('source', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (source,),
    )
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )


def import_from_yaml(
    tasks_path: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """Bootstrap the shadow DB from the canonical YAML store. Idempotent.

    Reads the resolved ``tasks.yaml`` (+ ``threads.yaml`` sidecar) and rebuilds
    every DB table inside ONE transaction. The YAML is opened read-only and is
    never modified. Returns a summary dict::

        {"db_path", "yaml_path", "tasks", "comments", "edges", "roles",
         "users", "user_names", "notifications", "messages"}
    """
    doc, store_path = _load_source(tasks_path)
    threads = _load_threads(store_path)
    resolved_db = resolve_db_path(db_path)

    conn = connect(resolved_db)
    try:
        init_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        summary: dict = {
            "db_path": str(resolved_db),
            "yaml_path": str(store_path),
        }
        summary.update(_rebuild_from_doc(conn, doc, threads=threads))
        _stamp_meta(conn, "yaml-import")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return summary


def mirror_doc(doc: dict, db_path: str | Path | None = None) -> dict:
    """S1 DUAL-WRITE: mirror an in-memory doc into the DB. Raises on failure.

    Called from the store's ONE write chokepoint
    (:func:`scitex_todo._model._save_doc_unlocked`) AFTER the canonical YAML write
    has succeeded, while the store lock is still held — so the mirror can never
    interleave with another writer, and needs no lock of its own.

    Takes the doc IN MEMORY on purpose: the caller already parsed it under the
    lock, and re-reading ``tasks.yaml`` from disk to get it back would cost
    another ~11 s parse (MEASURED, 1,257 cards) — doubling the very stall this
    migration exists to remove.

    Rebuilds the doc-derived tables in ONE transaction and leaves ``messages``
    (owned by the threads sidecar) untouched.

    RAISES on failure, deliberately. The POLICY of what to do about a failed
    mirror — never break the user's write, but never be silent either — belongs
    to the caller (:mod:`scitex_todo._dual_write`), not here. A primitive that
    swallows its own errors cannot be given a policy later.
    """
    resolved_db = resolve_db_path(db_path)
    conn = connect(resolved_db)
    try:
        init_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        summary = _rebuild_from_doc(conn, doc)
        summary["db_path"] = str(resolved_db)
        _stamp_meta(conn, "dual-write")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return summary


__all__ = ["import_from_yaml", "mirror_doc"]

# EOF
