#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo MCP tools — the card-RELATIONSHIP cluster.

Extracted from the budget-bound :mod:`scitex_cards._mcp_server` (the split its
``TOOL_NAMES`` block had queued), following the same pattern as
:mod:`scitex_cards._mcp_skills`: the tools register on the SAME shared ``mcp``
FastMCP instance, which ``_mcp_server`` imports at its tail for the
registration side effect — so ``from scitex_cards._mcp_server import mcp``
continues to expose every tool and ``TOOL_NAMES`` is unchanged.

Cluster: what a card is CONNECTED to —

  - ``set_edge`` — depends_on / blocks edges between two cards.
  - ``set_collaborator`` / ``set_subscriber`` — the ADR-0009 role lists.

Each tool is a thin wrapper around :mod:`scitex_cards._store` (§6 Python-API
parity) returning a JSON-string of what the Python API returns.
"""

from __future__ import annotations

import functools
import json

import anyio

from ._backend import get_backend
from ._mcp_app import mcp  # the LEAF — importing _mcp_server here would cycle


@mcp.tool()
async def set_edge(
    action: str,
    kind: str,
    source: str,
    target: str,
) -> str:
    """Add or remove a depends_on / blocks edge between two tasks.

    Args:
      action: ``"add"`` or ``"remove"``.
      kind: ``"depends_on"`` or ``"blocks"``.
      source / target: task ids on the edge.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(
            get_backend().set_edge,
            None,
            action=action,
            kind=kind,
            source=source,
            target=target,
        )
    )
    return json.dumps(result)


@mcp.tool()
async def set_collaborator(
    task_id: str,
    who: str,
    action: str = "add",
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
            get_backend().set_collaborator,
            None,
            task_id=task_id,
            who=who,
            action=action,
        )
    )
    return json.dumps(result)


@mcp.tool()
async def set_subscriber(
    task_id: str,
    who: str,
    action: str = "add",
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
            get_backend().set_subscriber,
            None,
            task_id=task_id,
            who=who,
            action=action,
        )
    )
    return json.dumps(result)


@mcp.tool()
async def rescore_task(
    task_id: str,
    urgency: int,
    importance: int,
    by: str | None = None,
) -> str:
    """Set a card's urgency+importance (1-5 each) and recompute the RANK
    total order (ADR-0011 §1/§8 — the matrix drag's write path).

    Rank is COMPUTED server-side, never asserted: importance dominates
    (quadrant II always outranks III), ties break by first-scored time
    (waiting never costs position) then id. Returns
    ``{"task": <card>, "rank": r, "of": N}`` — ``rank`` is null when the
    card is in a terminal state (finished work holds axes but no rank).
    Appends an auditable ``kind: rescore`` comment carrying the full
    old→new transition and emits ONE ``rank_changed`` card-event for THIS
    card (neighbours shift silently; read the new order via list_tasks).
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(
            get_backend().rescore_task,
            None,
            task_id,
            urgency=urgency,
            importance=importance,
            by=by,
        )
    )
    return json.dumps(result)


# EOF
