#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo MCP tools — comments/edges/roles/workflow cluster (Convention A).

Extracted from :mod:`scitex_todo._mcp_server` (2026-07-10 split, to keep
that module under the pre-tool-use line-limit hook) — registers on the SAME
shared ``mcp`` FastMCP instance via ``from ._mcp_server import mcp``;
``_mcp_server`` imports this module at its tail for the registration side
effect, so ``from scitex_todo._mcp_server import mcp`` continues to expose
every tool. This mirrors the existing ``_mcp_skills`` extraction pattern.

Cluster: ``comment_task``, ``set_edge``, ``set_collaborator``,
``set_subscriber``, ``resolve_task``, ``reopen_task`` — the
collaboration / dependency-graph / role / resolve-workflow tools that
operate on a card's social metadata rather than its core CRUD fields.
Each is a thin wrapper around :mod:`scitex_todo._store` (the Python API)
so MCP / CLI / GUI all share one logic path — §6 Python-API parity.
JSON-shape parity: every tool returns a JSON-string of the dict the
Python API returns.
"""

from __future__ import annotations

import functools
import json

import anyio

from . import _store
from ._mcp_server import mcp


@mcp.tool()
async def comment_task(
    task_id: str,
    text: str,
    by: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Append an entry to a task's ``comments[]`` thread (the
    Gitea-compatible Issue-activity log). ``by`` overrides the default
    author resolution ($SCITEX_TODO_AGENT_ID → $USER).
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_store.comment_task, tasks_path, task_id, text, by=by)
    )
    return json.dumps(result)


@mcp.tool()
async def set_edge(
    action: str,
    kind: str,
    source: str,
    target: str,
    tasks_path: str | None = None,
) -> str:
    """Add or remove a depends_on / blocks edge between two tasks.

    Args:
      action: ``"add"`` or ``"remove"``.
      kind: ``"depends_on"`` or ``"blocks"``.
      source / target: task ids on the edge.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(
            _store.set_edge,
            tasks_path,
            action=action,
            kind=kind,
            source=source,
            target=target,
        )
    )
    return json.dumps(result)


@mcp.tool()
async def resolve_task(
    task_id: str,
    actor: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Flip a blocked task to done + clear the blocker. Appends an audit
    comment naming the actor. Idempotent on already-resolved tasks.

    This is the MCP equivalent of the board v3 "Resolve → notify agent"
    button (ADR-0006/0007).
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_store.resolve_task, tasks_path, task_id, actor=actor)
    )
    return json.dumps(result)


@mcp.tool()
async def reopen_task(
    task_id: str,
    by: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Un-resolve: flip ``status=done`` back to ``blocked`` /
    ``blocker=operator-decision``. The Resolve→Undo partner.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_store.reopen_task, tasks_path, task_id, by=by)
    )
    return json.dumps(result)


@mcp.tool()
async def set_collaborator(
    task_id: str,
    who: str,
    action: str = "add",
    tasks_path: str | None = None,
) -> str:
    """Add or remove a collaborator on a card (ADR-0009 roles).

    Args:
      task_id: the card id.
      who: the agent/human to add or remove.
      action: ``"add"`` (default) or ``"remove"``.

    Adding a collaborator also subscribes them to the card's feedback
    (the default — subscribers include collaborators). Removing a
    collaborator leaves their subscription intact; use ``set_subscriber``
    with ``action="remove"`` to also stop their notices.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(
            _store.set_collaborator, tasks_path, task_id=task_id, who=who, action=action
        )
    )
    return json.dumps(result)


@mcp.tool()
async def set_subscriber(
    task_id: str,
    who: str,
    action: str = "add",
    tasks_path: str | None = None,
) -> str:
    """Subscribe or unsubscribe an agent/human on a card's notify list
    (ADR-0009 roles).

    Args:
      task_id: the card id.
      who: the agent/human to subscribe or unsubscribe.
      action: ``"add"`` (subscribe, default) or ``"remove"`` (unsubscribe).

    Anyone may unsubscribe — even a collaborator (the "always
    unsubscribable" rule).
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(
            _store.set_subscriber, tasks_path, task_id=task_id, who=who, action=action
        )
    )
    return json.dumps(result)


__all__ = [
    "comment_task",
    "reopen_task",
    "resolve_task",
    "set_collaborator",
    "set_edge",
    "set_subscriber",
]

# EOF
