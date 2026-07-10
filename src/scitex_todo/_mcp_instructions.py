#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The ``FastMCP(instructions=...)`` string, split out on its own.

Extracted from :mod:`scitex_todo._mcp_server` (2026-07-10) so this string —
what every connecting agent sees in its system prompt — is editable without
first fighting the pre-tool-use line-limit hook on the (much larger) server
module.

Fix landed here: the dead ``proj-scitex-todo`` example agent name is gone
(it is not a live agent; a card ``scope=agent:proj-scitex-todo`` is enqueued
to an inbox nobody drains) and "shared YAML task store" became "shared task
store" (the per-recipient inbox moved to SQLite in v0.7.50 — "YAML" is now
wrong for that part of the system, even though ``tasks.yaml`` itself is
still the canonical task store).
"""

from __future__ import annotations

INSTRUCTIONS = (
    "scitex-todo: shared task store across agents and hosts. "
    "Your identity is $SCITEX_TODO_AGENT_ID — let it resolve, and never "
    "hardcode an agent name (inboxes are keyed by owner identity, so a "
    "card addressed to a name no live agent answers to is enqueued to an "
    "inbox nobody drains and its notifications are silently lost). "
    "Use list_tasks with a `scope` arg (e.g. "
    "'agent:<your-agent-id>') to see only your slice. The canonical "
    "store lives at ~/.scitex/todo/tasks.yaml; precedence is "
    "explicit > $SCITEX_TODO_TASKS_YAML_SHARED > project (<git-root>/.scitex/todo) > "
    "user (~/.scitex/todo) > bundled example."
)

__all__ = ["INSTRUCTIONS"]

# EOF
