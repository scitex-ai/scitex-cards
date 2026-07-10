#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo MCP server — one FastMCP instance per the SciTeX convention.

Tools follow audit §6 Convention A (``tool_name == python_api_name``); see
``TOOL_NAMES`` for the full registered set (task CRUD + edges + roles, the
``help_wait`` / ``help_clear`` cards, ``poll_notifications`` — the standalone
PULL card-message inbox — and the ``todo_skills_*`` §5 pair).

The task-store tool surface is a thin wrapper around :mod:`scitex_todo._store`
(the Python API) so MCP / CLI / GUI all share one logic path — §6 Python-API
parity. JSON-shape parity: every tool returns a JSON-string of the dict /
list the Python API returns.

Import semantics
----------------
``fastmcp`` is an OPTIONAL dependency (``pip install scitex-todo[mcp]``).
Importing this module without fastmcp installed raises :class:`ImportError`
with a clear install hint — it does NOT raise at ``import scitex_todo``
time (the CLI guards the import; the MCP `start` verb surfaces the same
hint as a click error).

This module orchestrator (2026-07-10 split)
--------------------------------------------
This file used to hold every ``@mcp.tool()`` registration plus the
``instructions=`` string inline and sat at the pre-tool-use hook's
512-line edit limit — which made the ``instructions=`` string, seen by
EVERY connecting agent, effectively frozen. The actual tool bodies (and
the instructions string) now live in focused sibling modules, and this
file is a THIN ORCHESTRATOR: it builds the shared ``mcp`` FastMCP
instance, then imports each cluster module for its registration side
effect (each does ``from ._mcp_server import mcp`` + ``@mcp.tool()``) so
``from scitex_todo._mcp_server import mcp`` still exposes every tool —
exactly the pattern ``_mcp_skills`` already established. No tool logic
was deleted; it was moved verbatim:

  - ``_mcp_instructions``  — the ``instructions=`` string (``INSTRUCTIONS``).
  - ``_mcp_tasks_write``   — add_task / update_task / complete_task /
    delete_task / restore_task (mutate paths).
  - ``_mcp_tasks_read``    — list_tasks / summarize_tasks / resolve_store /
    get_task (query paths).
  - ``_mcp_tasks_social``  — comment_task / set_edge / set_collaborator /
    set_subscriber / resolve_task / reopen_task (collaboration / workflow).
  - ``_mcp_skills``        — skills introspection, reassign_task, help_wait /
    help_clear, poll_notifications, health, dm_send / dm_list (pre-existing
    extraction, unchanged).
"""

from __future__ import annotations

try:
    from fastmcp import FastMCP
except ImportError as _exc:  # pragma: no cover — exercised in the doctor test
    raise ImportError(
        "scitex-todo MCP tools require the [mcp] extra. Install with:\n"
        "  pip install 'scitex-todo[mcp]'"
    ) from _exc

from ._mcp_instructions import INSTRUCTIONS

mcp = FastMCP(
    name="scitex-todo",
    instructions=INSTRUCTIONS,
)


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
    "set_edge",
    "set_collaborator",
    "set_subscriber",
    "resolve_task",
    "reopen_task",
    # Registered in `_mcp_skills` (budget): reassign (1:1 `_store.reassign_task`)
    "reassign_task",
    # Help-wait SoC lift — semantics lifted out of the dotfiles hook.
    "help_wait",
    "help_clear",
    # Standalone pull-inbox read path (1:1 `_inbox.poll_inbox`; in _mcp_skills).
    "poll_notifications",
    # Package-level health doctor (1:1 `_health.health`; in _mcp_skills). Broad
    # store/notifyd/channel diagnosis — distinct from the narrow `mcp doctor`.
    "health",
    "todo_skills_list",
    "todo_skills_get",
    # Operator↔agent DMs (threads.yaml sidecar; registered in _mcp_skills).
    "dm_send",
    "dm_list",
)

# Imports below are for the registration side effect: each sibling module
# decorates its tools onto the shared ``mcp`` instance built above. The task
# CRUD / read / social clusters additionally re-export their public names
# here (explicit imports, not ``import *``) because callers across the repo
# do ``from scitex_todo._mcp_server import add_task`` etc. — those historical
# import paths must keep resolving unchanged. The ``_mcp_skills`` cluster's
# tools are (and always were) imported directly from ``scitex_todo._mcp_skills``
# by every caller, never re-exported here, so only the side effect is needed.
from ._mcp_tasks_write import (  # noqa: E402,F401
    add_task,
    complete_task,
    delete_task,
    restore_task,
    update_task,
)
from ._mcp_tasks_read import (  # noqa: E402,F401
    get_task,
    list_tasks,
    resolve_store,
    summarize_tasks,
)
from . import _mcp_tasks_social  # noqa: E402,F401
from . import _mcp_skills  # noqa: E402,F401

__all__ = ["TOOL_NAMES", "mcp"]

# EOF
