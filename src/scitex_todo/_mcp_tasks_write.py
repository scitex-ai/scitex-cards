#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo MCP tools — task mutation cluster (Convention A).

Extracted from :mod:`scitex_todo._mcp_server` (2026-07-10 split, to keep
that module under the pre-tool-use line-limit hook) — registers on the SAME
shared ``mcp`` FastMCP instance via ``from ._mcp_server import mcp``;
``_mcp_server`` imports this module at its tail for the registration side
effect, so ``from scitex_todo._mcp_server import mcp`` continues to expose
every tool. This mirrors the existing ``_mcp_skills`` extraction pattern.

Cluster: ``add_task``, ``update_task``, ``complete_task``, ``delete_task``,
``restore_task`` — the write / mutate paths of the task-store tool surface.
Each is a thin wrapper around :mod:`scitex_todo._store` (the Python API) so
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
async def add_task(
    id: str,
    title: str,
    status: str = "pending",
    scope: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    parent: str | None = None,
    note: str | None = None,
    repo: str | None = None,
    depends_on: list[str] | None = None,
    blocks: list[str] | None = None,
    # Operator-co-designed surface (TG 9667).
    task: str | None = None,
    project: str | None = None,
    host: str | None = None,
    agent: str | None = None,
    goal: str | None = None,
    last_activity: str | None = None,
    blocker: str | None = None,
    pr_url: str | None = None,
    issue_url: str | None = None,
    kind: str | None = None,
    # Compute-kind metadata (ADR-0002).
    job_id: str | None = None,
    command: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    # Deadline schema (P4 + recurring extension; closes the gap
    # noted in PR #127: callers couldn't SET deadlines via MCP).
    deadline: str | None = None,
    deadlines: list[str] | None = None,
    scheduled: str | None = None,
    created_by: str | None = None,  # creating USER
    tasks_path: str | None = None,
) -> str:
    """Append a new task to the store. Returns the inserted task as JSON.

    ``tasks_path`` overrides the default resolution chain; pass ``None`` to
    use the resolved default (project → user → bundled).

    Closed-enum fields (``status`` / ``kind`` / ``blocker``) are gated by
    the writer's validator — typos raise ``TaskValidationError`` with the
    bad value and the valid set.

    ``deadline`` accepts the P4 schema: a bare ISO date / ISO datetime,
    optionally followed by a recurring repeater suffix
    (``+1d``/``+1w``/``+1m``/``+1y``). ``deadlines`` is the multi form (a
    list of the same shape) — mutually exclusive with ``deadline``.
    ``scheduled`` is the corresponding "start work on" stamp (validator
    rejects ``deadline < scheduled``). See ``scitex_todo._model`` +
    ``next_deadline_for_task`` for parse rules.
    """
    _call = functools.partial(
        _store.add_task,
        tasks_path,
        id=id,
        title=title,
        status=status,
        scope=scope,
        assignee=assignee,
        priority=priority,
        parent=parent,
        note=note,
        repo=repo,
        depends_on=depends_on,
        blocks=blocks,
        task=task,
        project=project,
        host=host,
        agent=agent,
        goal=goal,
        last_activity=last_activity,
        blocker=blocker,
        pr_url=pr_url,
        issue_url=issue_url,
        kind=kind,
        job_id=job_id,
        command=command,
        started_at=started_at,
        finished_at=finished_at,
        deadline=deadline,
        deadlines=deadlines,
        scheduled=scheduled,
        created_by=created_by,
    )
    inserted = await anyio.to_thread.run_sync(_call)
    return json.dumps(inserted)


@mcp.tool()
async def update_task(
    task_id: str,
    title: str | None = None,
    status: str | None = None,
    scope: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    parent: str | None = None,
    note: str | None = None,
    repo: str | None = None,
    depends_on: list[str] | None = None,
    blocks: list[str] | None = None,
    # Operator-co-designed surface (TG 9667).
    task: str | None = None,
    project: str | None = None,
    host: str | None = None,
    agent: str | None = None,
    goal: str | None = None,
    last_activity: str | None = None,
    blocker: str | None = None,
    pr_url: str | None = None,
    issue_url: str | None = None,
    kind: str | None = None,
    # Compute-kind metadata (ADR-0002).
    job_id: str | None = None,
    command: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    # Deadline schema (P4 + recurring extension) — mirror of the
    # add_task surface so callers can SET deadlines via MCP, not just
    # READ them via list_tasks (PR #127 gap).
    deadline: str | None = None,
    deadlines: list[str] | None = None,
    scheduled: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Mutate fields of an existing task. Returns the merged task as JSON.

    Pass an empty string (e.g. ``scope=""``) to CLEAR a string field.
    Pass an empty list to CLEAR a list field. Omit a field to leave it
    untouched. Closed-enum values (``status`` / ``kind`` / ``blocker``)
    are gated by the writer's validator.

    ``deadline`` / ``deadlines`` / ``scheduled`` follow the same P4
    schema as ``add_task``. Pass an empty string to CLEAR ``deadline`` /
    ``scheduled``; pass an empty list to CLEAR ``deadlines``. The pair
    ``deadline`` + ``deadlines`` is mutually exclusive; the validator
    will raise if both are set on the resulting task.
    """
    fields: dict = {}
    for key, value in (
        ("title", title),
        ("status", status),
        ("scope", scope),
        ("assignee", assignee),
        ("priority", priority),
        ("parent", parent),
        ("note", note),
        ("repo", repo),
        ("task", task),
        ("project", project),
        ("host", host),
        ("agent", agent),
        ("goal", goal),
        ("last_activity", last_activity),
        ("blocker", blocker),
        ("pr_url", pr_url),
        ("issue_url", issue_url),
        ("kind", kind),
        ("job_id", job_id),
        ("command", command),
        ("started_at", started_at),
        ("finished_at", finished_at),
        ("deadline", deadline),
        ("scheduled", scheduled),
    ):
        if value is None:
            continue
        fields[key] = None if value == "" else value
    # List fields: ``None`` = leave untouched (filtered above);
    # empty list = clear; non-empty list = replace.
    if depends_on is not None:
        fields["depends_on"] = list(depends_on) if depends_on else None
    if blocks is not None:
        fields["blocks"] = list(blocks) if blocks else None
    if deadlines is not None:
        fields["deadlines"] = list(deadlines) if deadlines else None
    merged = await anyio.to_thread.run_sync(
        functools.partial(_store.update_task, tasks_path, task_id, **fields)
    )
    return json.dumps(merged)


@mcp.tool()
async def complete_task(
    task_id: str,
    by: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Mark a task done and stamp `_log_meta.completed_{at,by}`.

    Idempotent: re-completing a `done` task keeps the original stamp.
    `by` overrides the $SCITEX_TODO_AGENT_ID → $USER precedence.
    """
    done = await anyio.to_thread.run_sync(
        functools.partial(_store.complete_task, tasks_path, task_id, by=by)
    )
    return json.dumps(done)


@mcp.tool()
async def delete_task(
    task_id: str,
    tasks_path: str | None = None,
) -> str:
    """Delete a task + scrub references; returns the lossless payload
    a follow-up ``restore_task`` can consume to undo.

    Returns ``{"removed": <task>, "refs": [<scrubbed-ref-ids>]}``.
    Wraps the board v3 Delete-with-Undo flow for MCP agents.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_store.delete_task, tasks_path, task_id)
    )
    return json.dumps(result)


@mcp.tool()
async def restore_task(
    task: dict,
    refs: list[str] | None = None,
    tasks_path: str | None = None,
) -> str:
    """Undo a ``delete_task`` — re-insert at the original id. ``task``
    must be the exact dict ``delete_task`` returned in ``"removed"``.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_store.restore_task, tasks_path, task=task, refs=refs)
    )
    return json.dumps(result)


__all__ = [
    "add_task",
    "complete_task",
    "delete_task",
    "restore_task",
    "update_task",
]

# EOF
