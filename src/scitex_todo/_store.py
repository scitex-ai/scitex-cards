#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mutation-side Python API for the scitex-todo task store.

Sits on top of ``_model.load_tasks`` / ``_model.save_tasks`` to give agents,
the CLI verbs, the MCP tools, and (later) the web GUI write handlers a
single, well-tested surface for:

    add_task        Append a new task to the store.
    update_task     Mutate fields of an existing task by id.
    complete_task   Convenience: set status='done' + stamp completion meta.
    list_tasks      Filter by scope / assignee / status.
    summary         Count tasks per status (and by scope/assignee).

Every mutation runs through :func:`_model.save_tasks`, which holds an
``fcntl.flock``-based mutex on a sibling lock file so two concurrent writers
cannot interleave (PHASE 1 prereq for the cross-host sync substrate — see
``GITIGNORED/ARCHITECTURE.md`` Req 2).

The filter functions are read-only and do NOT lock — they snapshot the
store via :func:`_model.load_tasks` and apply the filter in memory.

Design constraints
------------------
- **Generic** (Req 8): scope/assignee/status are free-form strings. The
  helpers don't know what an "agent" is.
- **Centralized** (Req 3): the default store is whatever
  :func:`_paths.resolve_tasks_path` returns; callers can override with an
  explicit ``store=`` path. The user-scope default
  (``~/.scitex/todo/tasks.yaml``) covers Req 7.
- **Shared with scopes** (Req 1): ``$SCITEX_TODO_SCOPE`` provides the
  default value for ``list_tasks(scope=...)`` when the caller doesn't pass
  one explicitly. Pass ``scope=""`` (empty string) to ignore the env
  default and see everything.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import os
from pathlib import Path

from ._model import (
    VALID_STATUSES,
    TaskValidationError,
    _save_tasks_unlocked,
    _store_lock,
    load_tasks,
    save_tasks,
)
from ._paths import resolve_tasks_path

#: Env var name an agent sets to scope its default `list_tasks` / `summary`
#: view. The CLI's `--scope` flag overrides this; pass `scope=""` in the
#: Python API to see the unfiltered store.
ENV_SCOPE = "SCITEX_TODO_SCOPE"

#: Env var name carrying the agent's identity. Used as the default
#: `completed_by` when :func:`complete_task` doesn't get an explicit `by=`.
ENV_AGENT = "SCITEX_TODO_AGENT"


class TaskNotFoundError(KeyError):
    """Raised when an update/complete target id is not in the store."""


# --------------------------------------------------------------------------- #
# Internal helpers                                                            #
# --------------------------------------------------------------------------- #
def _resolved_store(store: str | Path | None) -> Path:
    """Resolve a store path argument through the precedence chain.

    ``None`` ⇒ apply the full resolution chain (`_paths.resolve_tasks_path`).
    Explicit path ⇒ used as-is (must exist for reads; will be created for
    fresh writes by :func:`_model.save_tasks`).
    """
    return resolve_tasks_path(store) if store is None else Path(store).expanduser()


def _default_scope(arg: str | None) -> str | None:
    """Resolve a scope argument, honoring ``$SCITEX_TODO_SCOPE`` as the
    default.

    ``None`` (caller didn't pass anything) → env var if set, else ``None``
    (no filter).
    Empty string ``""`` → caller explicitly opted out of filtering.
    Non-empty string → used as-is.
    """
    if arg is None:
        env = os.environ.get(ENV_SCOPE)
        return env if env else None
    if arg == "":
        return None
    return arg


def _default_agent(arg: str | None) -> str:
    """Resolve a `completed_by` argument with the env→login→unknown chain.

    Per ``GITIGNORED/QUESTIONS.md`` #2 the precedence is
    ``$SCITEX_TODO_AGENT`` → ``getpass.getuser()`` → ``"unknown"`` (final
    fallback handles environments where login info isn't available).
    """
    if arg:
        return arg
    env = os.environ.get(ENV_AGENT)
    if env:
        return env
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover — extremely rare environments
        return "unknown"


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with second resolution and the ``Z`` suffix.

    Trims the microseconds (the operator reads these on the board; second
    resolution is plenty) and uses the canonical ``Z`` suffix rather than
    ``+00:00`` so the string round-trips losslessly through YAML.
    """
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _match(task: dict, *, scope: str | None, assignee: str | None,
           status: str | None) -> bool:
    """Three-way string-equality filter. Any argument that is None is
    treated as "no constraint".

    **`assignee` semantics (PR #68, ADR-0008 D2 Q2-locked 2026-06-07):**
    matches rows where ``(agent or assignee) == <value>``. ``agent`` is
    the new PRIMARY linking field per the operator's Q2 confirmation;
    ``assignee`` is the deprecated alias. Both readings now succeed
    during the deprecation window — legacy rows with only ``assignee``
    and new rows with only ``agent`` both surface when callers filter
    by the legacy ``--assignee`` flag.
    """
    if scope is not None and task.get("scope") != scope:
        return False
    if assignee is not None:
        eff = task.get("agent") or task.get("assignee")
        if eff != assignee:
            return False
    if status is not None and task.get("status") != status:
        return False
    return True


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #
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
) -> dict:
    """Append a new task to ``store`` and persist via :func:`save_tasks`.

    Returns the inserted task mapping (a fresh dict, not the underlying
    YAML node) for convenient round-trip use by callers — the CLI prints
    it, the MCP tools serialize it as the JSON result.

    Raises
    ------
    TaskValidationError
        On duplicate id or any other structural fault — `save_tasks`
        re-runs the full validation gate before touching disk.
    """
    resolved = _resolved_store(store)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    new: dict = {"id": id, "title": title, "status": status}
    if scope is not None:
        new["scope"] = scope
    if assignee is not None:
        new["assignee"] = assignee
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

    # Lock for the FULL read-modify-write — without this, two concurrent
    # writers each load a stale snapshot and the second `save_tasks` call
    # silently clobbers the first writer's insert. See
    # tests/scitex_todo/test__store.py::test_two_concurrent_writers...
    with _store_lock(resolved):
        tasks = load_tasks(resolved) if resolved.exists() else []
        tasks.append(new)
        _save_tasks_unlocked(tasks, resolved)
    return dict(new)


def update_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    **fields,
) -> dict:
    """Update fields of the task with id ``task_id``; return the merged dict.

    Any keyword argument becomes a field on the task. Passing ``None`` for
    a field DELETES it (matches the operator's mental model: "clear the
    scope" = `update_task(..., scope=None)`). To leave a field untouched,
    just omit it.

    Raises
    ------
    TaskNotFoundError
        If no task matches ``task_id``.
    TaskValidationError
        If the resulting mutation is structurally invalid.
    """
    if not task_id:
        raise TypeError("update_task() requires a non-empty task_id")
    resolved = _resolved_store(store)
    with _store_lock(resolved):
        tasks = load_tasks(resolved)
        for task in tasks:
            if task.get("id") == task_id:
                for key, value in fields.items():
                    if value is None:
                        task.pop(key, None)
                    else:
                        task[key] = value
                _save_tasks_unlocked(tasks, resolved)
                return dict(task)
    raise TaskNotFoundError(f"task id {task_id!r} not found in {resolved}")


def complete_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    *,
    by: str | None = None,
) -> dict:
    """Mark ``task_id`` as ``done`` and stamp ``_log_meta.completed_{at,by}``.

    Idempotent per ``GITIGNORED/QUESTIONS.md`` #3: re-completing a
    ``done`` task is a no-op (timestamps stay frozen from the first
    completion). Pass ``by=`` to override the
    ``$SCITEX_TODO_AGENT`` → ``$USER`` → ``"unknown"`` precedence chain.

    Returns the (post-mutation) task mapping.

    Raises
    ------
    TaskNotFoundError
        If no task matches ``task_id``.
    """
    if not task_id:
        raise TypeError("complete_task() requires a non-empty task_id")
    resolved = _resolved_store(store)
    with _store_lock(resolved):
        tasks = load_tasks(resolved)
        for task in tasks:
            if task.get("id") == task_id:
                if task.get("status") == "done":
                    # Idempotent: don't refresh the stamp, just return.
                    return dict(task)
                task["status"] = "done"
                log_meta = task.get("_log_meta")
                if not isinstance(log_meta, dict):
                    log_meta = {}
                    task["_log_meta"] = log_meta
                log_meta["completed_at"] = _utc_now_iso()
                log_meta["completed_by"] = _default_agent(by)
                _save_tasks_unlocked(tasks, resolved)
                return dict(task)
    raise TaskNotFoundError(f"task id {task_id!r} not found in {resolved}")


def list_tasks(
    store: str | Path | None = None,
    *,
    scope: str | None = None,
    assignee: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Snapshot the store, then filter by scope / assignee / status.

    Filter semantics:
    - ``scope=None`` (default): use ``$SCITEX_TODO_SCOPE`` if set, else
      no filter. ``scope=""`` opts out of the env default explicitly.
    - ``assignee`` / ``status``: ``None`` = no filter; any string = exact
      match. (Generic Req 8 — no fuzzy / glob; callers compose.)

    The returned list contains fresh dicts, safe to mutate without
    affecting the on-disk store (no save here).
    """
    resolved = _resolved_store(store)
    tasks = load_tasks(resolved)
    scope_eff = _default_scope(scope)
    return [
        dict(t)
        for t in tasks
        if _match(t, scope=scope_eff, assignee=assignee, status=status)
    ]


def summarize_tasks(
    store: str | Path | None = None,
    *,
    scope: str | None = None,
    assignee: str | None = None,
) -> dict:
    """Return numeric progress counts grouped by status, scope, assignee.

    Output shape (always present keys):

    ::

        {
          "store": "/abs/path/to/tasks.yaml",
          "total": int,
          "by_status": {<status>: int, ...},  # one key per VALID_STATUSES
          "by_scope": {<scope|"">: int, ...},
          "by_assignee": {<assignee|"">: int, ...},
        }

    Tasks with no scope / assignee bucket under the empty string ``""``.
    The ``by_status`` map is densified to all :data:`VALID_STATUSES` so
    consumers (web UI, progress widgets) don't have to special-case
    zero-count keys.
    """
    resolved = _resolved_store(store)
    tasks = load_tasks(resolved)
    scope_eff = _default_scope(scope)
    by_status: dict[str, int] = {s: 0 for s in VALID_STATUSES}
    by_scope: dict[str, int] = {}
    by_assignee: dict[str, int] = {}
    total = 0
    for task in tasks:
        if not _match(task, scope=scope_eff, assignee=assignee, status=None):
            continue
        total += 1
        st = task.get("status")
        if st in by_status:
            by_status[st] += 1
        sc = task.get("scope") or ""
        by_scope[sc] = by_scope.get(sc, 0) + 1
        asg = task.get("assignee") or ""
        by_assignee[asg] = by_assignee.get(asg, 0) + 1
    return {
        "store": str(resolved),
        "total": total,
        "by_status": by_status,
        "by_scope": by_scope,
        "by_assignee": by_assignee,
    }


def resolve_store(store: str | Path | None = None) -> dict:
    """Return the resolved task store path and the precedence chain.

    Mirrors the data the `scitex-todo resolve-store` CLI verb and the
    `resolve_store` MCP tool emit. Keeping a Python API by the same name
    as the MCP tool satisfies audit §6 (Convention A: tool_name == api_name).

    Output shape::

        {
          "resolved":         "/abs/path/to/tasks.yaml",
          "explicit":         <the `store` arg you passed, or None>,
          "env_tasks":        <value of $SCITEX_TODO_TASKS, or None>,
          "user_store":       "/abs/path/to/~/.scitex/todo/tasks.yaml",
          "bundled_example":  "/abs/path/to/bundled/example.yaml",
          "pkg_short":        "scitex_todo",
          "exists":           bool,
        }
    """
    import os

    from ._paths import (
        ENV_TASKS,
        PKG_SHORT,
        _user_root,
        bundled_example,
        resolve_tasks_path,
    )

    resolved = resolve_tasks_path(store if isinstance(store, (str, type(None))) else str(store))
    return {
        "resolved": str(resolved),
        "explicit": str(store) if store is not None else None,
        "env_tasks": os.environ.get(ENV_TASKS),
        "user_store": str(_user_root() / "tasks.yaml"),
        "bundled_example": str(bundled_example()),
        "pkg_short": PKG_SHORT,
        "exists": Path(resolved).exists(),
    }


__all__ = [
    "ENV_AGENT",
    "ENV_SCOPE",
    "TaskNotFoundError",
    "TaskValidationError",
    "add_task",
    "complete_task",
    "list_tasks",
    "resolve_store",
    "summarize_tasks",
    "update_task",
]

# EOF
