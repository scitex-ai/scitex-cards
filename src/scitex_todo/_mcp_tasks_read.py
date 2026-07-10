#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo MCP tools — task read/query cluster (Convention A).

Extracted from :mod:`scitex_todo._mcp_server` (2026-07-10 split, to keep
that module under the pre-tool-use line-limit hook) — registers on the SAME
shared ``mcp`` FastMCP instance via ``from ._mcp_server import mcp``;
``_mcp_server`` imports this module at its tail for the registration side
effect, so ``from scitex_todo._mcp_server import mcp`` continues to expose
every tool. This mirrors the existing ``_mcp_skills`` extraction pattern.

Cluster: ``list_tasks``, ``summarize_tasks``, ``resolve_store``,
``get_task`` — the read / query paths of the task-store tool surface. Each
is a thin wrapper around :mod:`scitex_todo._store` (the Python API) so
MCP / CLI / GUI all share one logic path — §6 Python-API parity. JSON-shape
parity: every tool returns a JSON-string of the dict / list the Python API
returns.
"""

from __future__ import annotations

import functools
import json

import anyio

from . import _store
from ._mcp_server import mcp


@mcp.tool()
async def list_tasks(
    scope: str | None = None,
    assignee: str | None = None,
    status: str | None = None,
    statuses: list[str] | None = None,
    agent: str | None = None,
    project: str | None = None,
    host: str | None = None,
    blocker: str | None = None,
    kind: str | None = None,
    id_prefix: str | None = None,
    blocking_me: bool = False,
    overdue: bool = False,
    tasks_path: str | None = None,
) -> str:
    """List tasks, filtered by any combination of fields. Returns a JSON array.

    ``scope=None`` (default) uses $SCITEX_TODO_SCOPE if set; ``scope=""``
    opts out of that env default. ``statuses`` (multi) OR-combines with
    ``status`` (single). ``blocker="__none"`` matches rows with no blocker.
    ``blocking_me=True`` matches the board's BLOCKING-YOU predicate
    (``status=blocked AND blocker=operator-decision``). ``overdue=True``
    matches tasks past their next deadline AND not in a terminal lifecycle
    state (mirrors the ``scitex-todo list-tasks --overdue`` CLI flag and
    the fleet payload's ``overdue_count``; see scitex_todo._model.is_overdue
    — todo-p6-overdue-ui, PR #125 / #126).
    """
    _call = functools.partial(
        _store.list_tasks,
        tasks_path,
        scope=scope,
        assignee=assignee,
        status=status,
        statuses=statuses,
        agent=agent,
        project=project,
        host=host,
        blocker=blocker,
        kind=kind,
        id_prefix=id_prefix,
        blocking_me=blocking_me,
        overdue=overdue,
    )
    rows = await anyio.to_thread.run_sync(_call)
    return json.dumps(rows)


@mcp.tool()
async def summarize_tasks(
    scope: str | None = None,
    assignee: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Numeric progress: counts by status / scope / assignee."""
    result = await anyio.to_thread.run_sync(
        functools.partial(
            _store.summarize_tasks, tasks_path, scope=scope, assignee=assignee
        )
    )
    return json.dumps(result)


@mcp.tool()
async def resolve_store(tasks_path: str | None = None) -> str:
    """Show the resolved store path and the precedence chain.

    Useful for an agent to confirm "yes, I am writing to the shared
    user-scope store, not to a project shadow."
    """
    return json.dumps(_store.resolve_store(tasks_path))


@mcp.tool()
async def get_task(
    task_id: str,
    tasks_path: str | None = None,
) -> str:
    """Return one task by id as JSON. Raises if the id is unknown.

    Companion read-one verb for the CRUD surface (lead a2a `fe723080`).
    Mirrors the equivalent ``handle_get`` shape on the Django board, so
    MCP agents can use it without going through HTTP.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_store.get_task, tasks_path, task_id)
    )
    return json.dumps(result)


__all__ = [
    "get_task",
    "list_tasks",
    "resolve_store",
    "summarize_tasks",
]

# EOF
