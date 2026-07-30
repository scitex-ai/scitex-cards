#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The scitex-todo MCP server's agent-facing instructions text.

Every agent reads this string at session start — it is the single most-read
sentence the package ships, so it gets its own module (extracted from the
budget-bound :mod:`scitex_cards._mcp_server`) and its own tests.

Why the identity is INTERPOLATED
--------------------------------
The instructions used to hard-code ONE example scope — the ``proj-scitex-todo``
identity. That identity does not exist: the ``proj-`` prefix is a dead legacy
naming (see :data:`scitex_cards._users.IDENTITY_PREFIXES`, which exists to STRIP
it). An agent that followed the instruction filtered on a scope holding almost
nothing and reasonably concluded the board had no work for it. Measured against
the live store on 2026-07-11: **2** cards scoped to the dead ``proj-scitex-todo``
vs **63** scoped to the real ``scitex-todo``. A mechanical explanation for the
standing "the fleet ignores the board" complaint.

So the scope is now rendered from the agent's OWN id — resolved by the package's
existing :func:`scitex_cards._channel_identity.resolve_agent_id_optional`
(``$SCITEX_TODO_AGENT_ID``) — and when that identity is UNRESOLVABLE we name NO
scope at all. A silently-wrong example is worse than an honest absence: that IS
the bug. The unresolved branch instead tells the agent how to DISCOVER its slice.
"""

from __future__ import annotations

#: Store-identity sentence — identical in both branches of the instructions.
#:
#: Agents read this string at session start and act on it, so it must describe
#: the store as it actually is: the SQLite database at $SCITEX_CARDS_DB is the
#: sole store identity (the pre-cutover YAML path and its "bundled example" tier
#: are gone). A stale sentence here is the fleet being told to write to a store
#: that does not exist.
_STORE_LINE = (
    "The canonical store is the SQLite database at $SCITEX_CARDS_DB "
    "(default ~/.scitex/cards/cards.db) — that path is the SOLE store identity; "
    "call resolve_store to see which store you actually resolved to. An "
    "unresolvable/absent store raises rather than silently handing you an "
    "empty board."
)


def build_instructions(agent_id: str | None) -> str:
    """Render the MCP server instructions for THIS agent's REAL scope.

    Parameters
    ----------
    agent_id : str | None
        The resolved agent identity (``$SCITEX_TODO_AGENT_ID``), or ``None`` /
        ``""`` when it cannot be resolved. NEVER substitute a placeholder here:
        the caller passes exactly what resolution returned.

    Returns
    -------
    str
        The instructions. With an ``agent_id`` the string names that agent and
        its ``agent:<id>`` scope. Without one it names NO scope — it says the
        identity is unresolved and points at ``list_tasks`` (no scope) +
        ``resolve_store`` to discover the slice, plus the env var to set.
    """
    if agent_id:
        slice_line = (
            f"You are `{agent_id}` (from $SCITEX_TODO_AGENT_ID): call list_tasks "
            f"with scope='agent:{agent_id}' to see only your slice, and stamp "
            "your writes with that same id."
        )
    else:
        slice_line = (
            "Your identity is UNRESOLVED ($SCITEX_TODO_AGENT_ID is unset or "
            "blank), so this server cannot name your scope — do NOT guess one, "
            "because a wrong `scope` silently hides your own cards. Discover it "
            "instead: call list_tasks with NO scope to see every card (yours are "
            "the ones whose agent/assignee names you), and resolve_store to "
            "confirm which store you are reading. Then set "
            "SCITEX_TODO_AGENT_ID=<your-agent-id> so scoped queries work."
        )
    return (
        "scitex-cards: shared task store across agents and hosts. "
        f"{slice_line} {_STORE_LINE}"
    )


__all__ = ["build_instructions"]

# EOF
