#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-cards MCP server — one FastMCP instance per the SciTeX convention.

Tools follow audit §6 Convention A (``tool_name == python_api_name``); see
``TOOL_NAMES`` for the full registered set (task CRUD + edges + roles, the
``help_wait`` / ``help_clear`` cards, ``poll_notifications`` — the standalone
PULL card-message inbox — and the ``cards_skills_*`` §5 pair).

Three cohesive tool clusters live in sibling modules for this module's line
budget and register on the SAME ``mcp`` instance (imported at the tail for the
side effect): :mod:`scitex_cards._mcp_write` (``add_task`` + ``update_task`` —
the two tools whose signatures carry the whole card schema, so they grow with
every field), :mod:`scitex_cards._mcp_relations` (edges + roles) and
:mod:`scitex_cards._mcp_skills` (skills, help-wait, DMs, inbox, health). The
agent-facing instructions text lives in :mod:`scitex_cards._mcp_instructions`.

The task-store tool surface is a thin wrapper around :mod:`scitex_cards._store`
(the Python API) so MCP / CLI / GUI all share one logic path — §6 Python-API
parity. JSON-shape parity: every tool returns a JSON-string of the dict /
list the Python API returns.

Import semantics
----------------
``fastmcp`` is an OPTIONAL dependency (``pip install scitex-cards[all]``).
Importing this module without fastmcp installed raises :class:`ImportError`
with a clear install hint — it does NOT raise at ``import scitex_cards``
time (the CLI guards the import; the MCP `start` verb surfaces the same
hint as a click error).
"""

from __future__ import annotations

import functools
import json

import anyio

from . import _store  # resolve_store only — every verb routes via the seam
from ._backend import get_backend

# `mcp` and `_ENUM_FIELDS` now live in `_mcp_app`, a LEAF module — it imports
# nothing that imports it back. They are re-exported here because 8 modules and
# the CLI already do `from ._mcp_server import mcp`, and that surface must not
# move.
#
# THIS IS NOT COSMETIC. The satellites (`_mcp_write`, `_mcp_relations`,
# `_mcp_skills`, `_mcp_channel`) need `mcp` to decorate with, and THIS module
# imports THEM at its tail for the registration side effect. When `mcp` lived
# here, that was a cycle: importing a satellite FIRST raised ImportError, because
# it pulled in `_mcp_server`, which reached its tail and asked the satellite for
# names it had not defined yet. Hoisting the shared symbol to a leaf is what
# breaks it — see `_mcp_app`'s docstring for why a lazy re-export would have
# silently unregistered two tools instead.
from ._mcp_app import _ENUM_FIELDS, mcp  # noqa: F401  (re-export)

# NOTE on the CURRENCY gate (scitex_cards._currency.check_currency): it does
# NOT live here at module level. It used to (2026-07-21), and
# `tests/scitex_cards/test__import_order.py` correctly caught that as a bug:
# `import scitex_cards._mcp_server` raised a real StalenessError in a fresh
# subprocess whose env carries none of the test suite's suppression pins.
# Importing a module must be side-effect-free — same principle that exempts a
# docs build. The gate now lives at the actual SERVER-START call site instead:
# see `_attach_unified_start`'s `start()` handler in `_cli/_mcp.py`, which
# covers `scitex-cards mcp start` (both the stdio-unified and `--http`
# branches). A client that launches this module directly, bypassing that CLI
# (e.g. `fastmcp run scitex_cards._mcp_server:mcp`), is NOT gated — FastMCP
# does offer a `lifespan=` hook on `FastMCP(...)`, but wiring it here is
# unsafe: `_mcp_channel._run()` drives `mcp._mcp_server` (the low-level
# server) by hand for the stdio-unified path, entering `server.lifespan(server)`
# directly rather than through `FastMCP.run()`/`_lifespan_manager()`, and a
# non-default `lifespan=` in that shape raises `RuntimeError("... no lifespan
# result is set ...")` — i.e. it would break the DEFAULT `mcp start` command
# it was meant to protect. Left as a known, honestly-disclosed gap rather than
# a half-fixed lifespan wiring.

# --------------------------------------------------------------------------- #
# Task-store tools — Convention A (tool name == Python API name).             #
# --------------------------------------------------------------------------- #
# `add_task` + `update_task` live in `_mcp_write` (imported at the tail for
# the registration side effect). They carry the ENTIRE card schema in their
# signatures, so they grow with every new field — which is what pushed this
# module past its line budget. The split the docstring above called 'queued'.


@mcp.tool()
async def complete_task(
    task_id: str,
    by: str | None = None,
) -> str:
    """Mark a task done and stamp `_log_meta.completed_{at,by}`.

    Idempotent: re-completing a `done` task keeps the original stamp.
    `by` overrides the $SCITEX_CARDS_AGENT_ID → $USER precedence.
    """
    done = await anyio.to_thread.run_sync(
        functools.partial(get_backend().complete_task, None, task_id, by=by)
    )
    return json.dumps(done)


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
) -> str:
    """List tasks, filtered by any combination of fields. Returns a JSON array.

    ``scope=None`` (default) uses $SCITEX_CARDS_SCOPE if set; ``scope=""``
    opts out of that env default. ``statuses`` (multi) OR-combines with
    ``status`` (single). ``blocker="__none"`` matches rows with no blocker.
    ``blocking_me=True`` matches the board's BLOCKING-YOU predicate
    (``status=blocked AND blocker=operator-decision``). ``overdue=True``
    matches tasks past their next deadline AND not in a terminal lifecycle
    state (mirrors the ``scitex-cards list-tasks --overdue`` CLI flag and
    the fleet payload's ``overdue_count``; see scitex_cards._model.is_overdue
    — cards-p6-overdue-ui, PR #125 / #126). ``overdue`` is a PULL filter,
    not an alarm: this query is the ONLY way an overdue card reaches you
    — nothing pushes it. A deadline passing notifies nobody, so poll
    ``overdue=True`` yourself if you care. Note it only ever matches
    NON-recurring deadlines: a repeater (``+1w``) rolls the next
    occurrence into the future, so a recurring card is never overdue.
    (hook-bypass: line-limit.)
    """
    _call = functools.partial(
        get_backend().list_tasks,
        None,
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
) -> str:
    """Numeric progress: counts by status / scope / assignee."""
    result = await anyio.to_thread.run_sync(
        functools.partial(
            get_backend().summarize_tasks, None, scope=scope, assignee=assignee
        )
    )
    return json.dumps(result)


@mcp.tool()
async def resolve_store() -> str:
    """Show the resolved store path and the precedence chain.

    Useful for an agent to confirm "yes, I am writing to the shared
    user-scope store, not to a project shadow."
    """
    return json.dumps(_store.resolve_store(None))


@mcp.tool()
async def get_task(
    task_id: str,
) -> str:
    """Return one task by id as JSON. Raises if the id is unknown.

    Companion read-one verb for the CRUD surface (lead a2a `fe723080`).
    Mirrors the equivalent ``handle_get`` shape on the Django board, so
    MCP agents can use it without going through HTTP.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(get_backend().get_task, None, task_id)
    )
    return json.dumps(result)


@mcp.tool()
async def delete_task(
    task_id: str,
) -> str:
    """Delete a task + scrub references; returns the lossless payload
    a follow-up ``restore_task`` can consume to undo.

    Returns ``{"removed": <task>, "refs": [<scrubbed-ref-ids>]}``.
    Wraps the board v3 Delete-with-Undo flow for MCP agents.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(get_backend().delete_task, None, task_id)
    )
    return json.dumps(result)


@mcp.tool()
async def restore_task(
    task: dict,
    refs: list[str] | None = None,
) -> str:
    """Undo a ``delete_task`` — re-insert at the original id. ``task``
    must be the exact dict ``delete_task`` returned in ``"removed"``.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(get_backend().restore_task, None, task=task, refs=refs)
    )
    return json.dumps(result)


@mcp.tool()
async def comment_task(
    task_id: str,
    text: str,
    by: str | None = None,
) -> str:
    """Append an entry to a task's ``comments[]`` thread (the
    Gitea-compatible Issue-activity log). ``by`` overrides the default
    author resolution ($SCITEX_CARDS_AGENT_ID → $USER).
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(get_backend().comment_task, None, task_id, text, by=by)
    )
    return json.dumps(result)


@mcp.tool()
async def resolve_task(
    task_id: str,
    actor: str | None = None,
) -> str:
    """Flip a blocked task to done + clear the blocker. Appends an audit
    comment naming the actor. Idempotent on already-resolved tasks.

    This is the MCP equivalent of the board v3 "Resolve → notify agent"
    button (ADR-0006/0007).
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(get_backend().resolve_task, None, task_id, actor=actor)
    )
    return json.dumps(result)


@mcp.tool()
async def reopen_task(
    task_id: str,
    by: str | None = None,
) -> str:
    """Un-resolve: flip ``status=done`` back to ``blocked`` /
    ``blocker=operator-decision``. The Resolve→Undo partner.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(get_backend().reopen_task, None, task_id, by=by)
    )
    return json.dumps(result)


#: Canonical list of registered tool names — a constant so the `mcp doctor`
#: / `mcp list-tools` CLI verbs need not introspect FastMCP's drifting
#: internal registry. Update when a `@mcp.tool()` is added/removed.
TOOL_NAMES: tuple[str, ...] = (
    "add_task",
    "update_task",
    "complete_task",
    "list_tasks",
    "summarize_tasks",
    "resolve_store",
    # MCP completeness wave (lead a2a `fe723080`, 2026-06-08).
    "get_task",
    "delete_task",
    "restore_task",
    "comment_task",
    # The card-RELATIONSHIP cluster (edges + ADR-0009 roles) — registered in
    # `_mcp_relations` (the split this block used to have queued).
    "set_edge",
    "set_collaborator",
    "set_subscriber",
    "resolve_task",
    "reopen_task",
    # The rank engine's write verb (ADR-0011 §1/§8; registered in
    # _mcp_relations — the matrix drag's target).
    "rescore_task",
    # Registered in `_mcp_skills` (budget): reassign (1:1 `_store.reassign_task`)
    "reassign_task",
    # Help-wait SoC lift — semantics lifted out of the dotfiles hook.
    "help_wait",
    "help_clear",
    # Standalone pull-inbox read path (1:1 `_inbox.poll_inbox`; in _mcp_skills).
    "poll_notifications",
    # ...and its CONFIRM half (1:1 `_inbox_confirm.confirm_notifications`).
    # Reading hands over; only this advances the cursor, so an undelivered
    # notification is redelivered instead of destroyed (incident 2026-07-29).
    "ack_notifications",
    # Package-level health doctor (1:1 `_health.health`; in _mcp_skills). Broad
    # store/notifyd/channel diagnosis — distinct from the narrow `mcp doctor`.
    # THE MCP TOOL KEEPS THE SINGLE-TOKEN NAME `health` DELIBERATELY, and audit
    # §2 flags it. It is a CROSS-PACKAGE STANDARD — sac and
    # claude-code-telegrammer expose the same verb under the same name, so an
    # operator asks any package for its health identically. Renaming ours to
    # satisfy §2 would break the uniformity the standard exists to provide, for
    # one package only. `tests/scitex_cards/test__mcp_server.py::_STANDARD_NAMES`
    # already pins this decision; the §2 finding is raised with scitex-dev,
    # whose rule (`if "_" not in raw`) has no notion of a standard name.
    #
    # The CLI is a different surface and DID move: `validate-health`, with
    # `health` kept as a Phase-W deprecated_alias.
    "health",
    "cards_skills_list",
    "cards_skills_get",
    # Operator↔agent DMs (threads.json sidecar; registered in _mcp_skills).
    "dm_send",
    "dm_list",
    # ...and the FILE half. Text-only DMs meant a deliverable reached the
    # operator as prose describing a deliverable; this is the entry point that
    # was simply missing. Composes _attachments with dm_send in-process, and
    # is deliberately NOT a BACKEND_VERBS member — `_server.py` dispatches that
    # tuple over HTTP, and a path-taking verb there would be remotely callable.
    "dm_send_document",
)

# Imports for the registration side effect: these modules (kept separate for
# this module's line budget) decorate their tools onto the shared ``mcp``
# instance, so ``from scitex_cards._mcp_server import mcp`` exposes every tool.
from . import (
    _mcp_relations,  # noqa: E402,F401
    _mcp_skills,  # noqa: E402,F401
    _mcp_write,  # noqa: E402,F401 — add_task + update_task
)

# RE-EXPORT the two write tools. Registering them on `mcp` from `_mcp_write` was
# enough for the MCP surface, but NOT for the PYTHON one: callers and tests do
# `from scitex_cards._mcp_server import add_task`, and moving the definition out
# broke that import while every MCP tool still resolved. A split must leave the
# original module a thin orchestrator that RE-EXPORTS its public API — otherwise
# it is not a refactor, it is a rename with extra steps.
from ._mcp_write import add_task, update_task  # noqa: E402,F401

__all__ = ["TOOL_NAMES", "mcp", "add_task", "update_task"]

# EOF
