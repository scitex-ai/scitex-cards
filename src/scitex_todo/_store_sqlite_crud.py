#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CRUD verbs for the CARDS + COMMENTS SQLite backend (Phase 2, slice A).

Split out of ``_store_sqlite.py`` to respect the 512-line per-file cap; see
that module's docstring for the full Phase-2 rationale + the documented
scope gaps (no card-event emission / active-unblock / liveness / WIP gate —
deliberately out of scope for this slice; the backend defaults OFF).

Signatures are IDENTICAL to ``scitex_todo._store``'s originals. Identity
resolution (``_resolve_creator_or_raise`` / ``_default_agent``), the
read-side filter predicate (``_match`` / ``_default_scope``), the timestamp
helper (``_utc_now_iso``), and ``TaskNotFoundError`` are all REUSED from
``_store`` (SSOT) rather than reimplemented. Closed-enum validation
(``status`` / ``kind`` / ``blocker``) reuses ``_model._validate_tasks``
verbatim — the SAME validator the YAML path runs.
"""

from __future__ import annotations

from pathlib import Path

from ._model import TaskValidationError, _validate_tasks
from ._store import (
    TaskNotFoundError,
    _default_agent,
    _default_scope,
    _match,
    _resolve_creator_or_raise,
    _utc_now_iso,
)
from ._store_sqlite_migrate import ensure_ready
from ._store_sqlite_schema import (
    fetch_comments,
    open_connection,
    row_to_task,
    store_db_path,
    upsert_task_row,
)


def add_task(
    store: str | Path | None = None,
    *,
    id: str,
    title: str,
    status: str = "pending",
    scope: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    parent: str | None = None,
    note: str | None = None,
    depends_on: list[str] | None = None,
    blocks: list[str] | None = None,
    repo: str | None = None,
    created_by: str | None = None,
    entry_points=None,
    **extras,
) -> dict:
    """SQLite twin of ``scitex_todo._store.add_task``."""
    agent_in = extras.get("agent")
    owner = assignee or agent_in or ""
    owner = owner.strip() if isinstance(owner, str) else owner
    if not owner:
        raise TaskValidationError(
            "assignee is required — pass assignee=<user> (or agent=<user>); "
            "creator+assignee are mandatory and an owner-less card is "
            "rejected (no silent fallback; see constitution)."
        )
    creator = _resolve_creator_or_raise(created_by)
    stamp = _utc_now_iso()
    new: dict = {"id": id, "title": title, "status": status}
    new["created_at"] = stamp
    new["last_activity"] = stamp
    new["created_by"] = creator
    if scope is not None:
        new["scope"] = scope
    new["assignee"] = owner
    extras["agent"] = owner
    if priority is not None:
        new["priority"] = priority
    if parent is not None:
        new["parent"] = parent
    if note is not None:
        new["note"] = note
    if depends_on is not None:
        new["depends_on"] = list(depends_on)
    if blocks is not None:
        new["blocks"] = list(blocks)
    if repo is not None:
        new["repo"] = repo
    for key, value in extras.items():
        if value is None:
            continue
        new[key] = value

    db = store_db_path(store)
    with open_connection(db) as conn:
        ensure_ready(conn, store)
        existing = conn.execute(
            "SELECT 1 FROM tasks WHERE id = ? LIMIT 1", (id,)
        ).fetchone()
        if existing is not None:
            raise TaskValidationError(f"<sqlite store>: duplicate task id {id!r}")
        _validate_tasks([new], source="<sqlite store>")
        upsert_task_row(conn, new)
        conn.commit()
    return dict(new)


def update_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    *,
    entry_points=None,
    **fields,
) -> dict:
    """SQLite twin of ``scitex_todo._store.update_task``."""
    if not task_id:
        raise TypeError("update_task() requires a non-empty task_id")
    db = store_db_path(store)
    with open_connection(db) as conn:
        ensure_ready(conn, store)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"task id {task_id!r} not found in {db}")
        task = row_to_task(row)
        for key, value in fields.items():
            if value is None:
                task.pop(key, None)
            else:
                task[key] = value
        if "last_activity" not in fields:
            task["last_activity"] = _utc_now_iso()
        _validate_tasks([task], source="<sqlite store>")
        upsert_task_row(conn, task)
        conn.commit()
    return dict(task)


def get_task(store: str | Path | None = None, task_id: str | None = None) -> dict:
    """SQLite twin of ``scitex_todo._store.get_task``."""
    if not task_id:
        raise ValueError("get_task: 'task_id' is required")
    db = store_db_path(store)
    with open_connection(db) as conn:
        ensure_ready(conn, store)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"task id {task_id!r} not found in {db}")
        task = row_to_task(row)
        comments = fetch_comments(conn, task_id)
        if comments:
            task["comments"] = comments
        return task


def list_tasks(
    store: str | Path | None = None,
    *,
    scope: str | None = None,
    assignee: str | None = None,
    status: str | None = None,
    statuses: list[str] | None = None,
    agent: str | None = None,
    project: str | None = None,
    host: str | None = None,
    repo: str | None = None,
    blocker: str | None = None,
    kind: str | None = None,
    id_prefix: str | None = None,
    blocking_me: bool = False,
    overdue: bool = False,
) -> list[dict]:
    """SQLite twin of ``scitex_todo._store.list_tasks``."""
    db = store_db_path(store)
    with open_connection(db) as conn:
        ensure_ready(conn, store)
        rows = conn.execute("SELECT * FROM tasks").fetchall()
        comment_rows = conn.execute(
            "SELECT task_id, text, by, kind, ts FROM comments ORDER BY task_id, id"
        ).fetchall()
    by_task: dict[str, list[dict]] = {}
    for r in comment_rows:
        entry = {"author": r["by"], "ts": r["ts"], "text": r["text"]}
        if r["kind"]:
            entry["kind"] = r["kind"]
        by_task.setdefault(r["task_id"], []).append(entry)
    tasks = []
    for row in rows:
        t = row_to_task(row)
        cs = by_task.get(t["id"])
        if cs:
            t["comments"] = cs
        tasks.append(t)
    scope_eff = _default_scope(scope)
    return [
        t
        for t in tasks
        if _match(
            t,
            scope=scope_eff,
            assignee=assignee,
            status=status,
            statuses=statuses,
            agent=agent,
            project=project,
            host=host,
            repo=repo,
            blocker=blocker,
            kind=kind,
            id_prefix=id_prefix,
            blocking_me=blocking_me,
            overdue=overdue,
        )
    ]


def comment_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    text: str | None = None,
    by: str | None = None,
    kind: str | None = None,
    entry_points=None,
) -> dict:
    """SQLite twin of ``scitex_todo._store.comment_task``.

    The mutation Phase 2 exists for: an indexed INSERT into ``comments``,
    NOT a whole-store re-serialize.
    """
    if not task_id:
        raise ValueError("comment_task: 'task_id' is required")
    if not text or not str(text).strip():
        raise ValueError("comment_task: 'text' is required")
    author = _default_agent(by)
    ts = _utc_now_iso()
    db = store_db_path(store)
    with open_connection(db) as conn:
        ensure_ready(conn, store)
        exists = conn.execute(
            "SELECT 1 FROM tasks WHERE id = ? LIMIT 1", (task_id,)
        ).fetchone()
        if exists is None:
            raise TaskNotFoundError(f"task id {task_id!r} not found in {db}")
        conn.execute(
            "INSERT INTO comments(task_id, text, by, kind, ts) VALUES(?, ?, ?, ?, ?)",
            (task_id, str(text), author, str(kind) if kind else None, ts),
        )
        conn.commit()
    entry = {"author": author, "ts": ts, "text": str(text)}
    if kind:
        entry["kind"] = str(kind)
    return {"task_id": task_id, "comment": entry}


def reassign_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    new_owner: str | None = None,
    *,
    by: str | None = None,
    entry_points=None,
) -> dict:
    """SQLite twin of ``scitex_todo._store.reassign_task``."""
    if not task_id:
        raise ValueError("reassign_task: 'task_id' is required")
    if not new_owner or not str(new_owner).strip():
        raise ValueError("reassign_task: 'new_owner' is required")
    new_owner = str(new_owner)
    actor = _default_agent(by)
    db = store_db_path(store)
    with open_connection(db) as conn:
        ensure_ready(conn, store)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"reassign_task: unknown id {task_id!r}")
        task = row_to_task(row)
        old_owner = task.get("agent") or task.get("assignee")
        if old_owner == new_owner:
            return {
                "task_id": task_id,
                "from_owner": old_owner,
                "to_owner": new_owner,
                "actor": actor,
                "changed": False,
                "task": dict(task),
            }
        task["agent"] = new_owner
        task["assignee"] = new_owner
        task["scope"] = f"agent:{new_owner}"
        task["last_activity"] = _utc_now_iso()
        upsert_task_row(conn, task)
        conn.execute(
            "INSERT INTO comments(task_id, text, by, kind, ts) VALUES(?, ?, ?, ?, ?)",
            (
                task_id,
                f"reassigned {old_owner or '(unassigned)'} -> {new_owner} by {actor}",
                actor,
                None,
                _utc_now_iso(),
            ),
        )
        conn.commit()
    return {
        "task_id": task_id,
        "from_owner": old_owner,
        "to_owner": new_owner,
        "actor": actor,
        "changed": True,
        "task": dict(task),
    }


def complete_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    *,
    by: str | None = None,
    entry_points=None,
) -> dict:
    """SQLite twin of ``scitex_todo._store.complete_task``. Idempotent."""
    if not task_id:
        raise TypeError("complete_task() requires a non-empty task_id")
    db = store_db_path(store)
    with open_connection(db) as conn:
        ensure_ready(conn, store)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"task id {task_id!r} not found in {db}")
        task = row_to_task(row)
        if task.get("status") == "done":
            return task
        task["status"] = "done"
        log_meta = task.get("_log_meta")
        if not isinstance(log_meta, dict):
            log_meta = {}
        log_meta["completed_at"] = _utc_now_iso()
        log_meta["completed_by"] = _default_agent(by)
        task["_log_meta"] = log_meta
        upsert_task_row(conn, task)
        conn.commit()
    return dict(task)


def delete_task(store: str | Path | None = None, task_id: str | None = None) -> dict:
    """SQLite twin of ``scitex_todo._store.delete_task``."""
    if not task_id:
        raise ValueError("delete_task: 'task_id' is required")
    db = store_db_path(store)
    with open_connection(db) as conn:
        ensure_ready(conn, store)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"task id {task_id!r} not found in {db}")
        target = row_to_task(row)
        comments = fetch_comments(conn, task_id)
        if comments:
            target["comments"] = comments
        other_rows = conn.execute(
            "SELECT * FROM tasks WHERE id != ?", (task_id,)
        ).fetchall()
        refs: list[str] = []
        for r in other_rows:
            t = row_to_task(r)
            mutated = False
            if isinstance(t.get("depends_on"), list) and task_id in t["depends_on"]:
                t["depends_on"] = [d for d in t["depends_on"] if d != task_id]
                mutated = True
            if isinstance(t.get("blocks"), list) and task_id in t["blocks"]:
                t["blocks"] = [b for b in t["blocks"] if b != task_id]
                mutated = True
            if t.get("parent") == task_id:
                t.pop("parent", None)
                mutated = True
            if mutated:
                upsert_task_row(conn, t)
                refs.append(t.get("id"))
        conn.execute("DELETE FROM comments WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
    return {"removed": target, "refs": refs}


__all__ = [
    "add_task",
    "comment_task",
    "complete_task",
    "delete_task",
    "get_task",
    "list_tasks",
    "reassign_task",
    "update_task",
]

# EOF
