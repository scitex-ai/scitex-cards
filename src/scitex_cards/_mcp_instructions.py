#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The scitex-cards MCP server's agent-facing instructions text.

Every agent reads this string at session start — it is the single most-read
sentence the package ships, so it gets its own module (extracted from the
budget-bound :mod:`scitex_cards._mcp_server`) and its own tests.

Why the identity is INTERPOLATED
--------------------------------
The instructions used to hard-code ONE example scope — the ``proj-scitex-cards``
identity. That identity does not exist: the ``proj-`` prefix is a dead legacy
naming (see :data:`scitex_cards._users.IDENTITY_PREFIXES`, which exists to STRIP
it). An agent that followed the instruction filtered on a scope holding almost
nothing and reasonably concluded the board had no work for it. Measured against
the live store on 2026-07-11: **2** cards scoped to the dead ``proj-scitex-cards``
vs **63** scoped to the real ``scitex-cards``. A mechanical explanation for the
standing "the fleet ignores the board" complaint.

So the scope is now rendered from the agent's OWN id — resolved by the package's
existing :func:`scitex_cards._channel_identity.resolve_agent_id_optional`
(``$SCITEX_CARDS_AGENT_ID``) — and when that identity is UNRESOLVABLE we name NO
scope at all. A silently-wrong example is worse than an honest absence: that IS
the bug. The unresolved branch instead tells the agent how to DISCOVER its slice.

Why "your slice" stopped being a false claim
--------------------------------------------
This string used to end ``to see only your slice``, which does not suggest a
query — it asserts an EQUIVALENCE between a scope filter and an agent's work,
with tool authority, on first contact. It was false: ``list_tasks`` compared
scope by exact string, so a card filed against you under ``fleet``,
``ecosystem`` or no scope was excluded from "your slice".

Measured on the CANONICAL store 2026-08-06: **441 open cards** owned by an agent
were invisible to that agent's own scoped query, across **39 owners**; 398 of
them only because nobody set a scope when filing. The ``lead`` agent had 12
hidden and 0 visible — an empty board while holding work. Reported independently by
scitex-agent-container (69), scitex-ui (3, all P1/P2 blocked on an operator
decision) and scitex-app (4), none of whom were looking for it. As scitex-app
put it, disbelieving this sentence would have required suspecting the tool's own
documentation.

BOTH HALVES WERE FIXED, and the order mattered: :func:`scitex_cards._store_list._in_scope`
now treats ``agent:<id>`` as an OWNER rather than a lens, so the sentence is
TRUE before it is repeated. Changing only the wording — telling agents to query
by ``assignee`` instead — would have moved the failure into the tool-result size
cap, which both reporters had already hit that same session, and an agent that
hits the cap narrows its query, which is this bug again in different clothes.

One thing this module got right and put where it could not act: the
UNRESOLVED branch below has always warned that "a wrong ``scope`` silently hides
your own cards". The branch every agent actually receives asserted the opposite.
A correct insight in the path that almost never runs is not a safeguard.
"""

from __future__ import annotations

#: Store-identity sentence — identical in both branches of the instructions.
#:
#: Agents read this string at session start and act on it, so it must describe
#: the store as it actually is. IT MUST NOT NAME A BACKEND OR A DEFAULT PATH.
#:
#: It used to say "the canonical store is the database file at
#: $SCITEX_CARDS_DB (default ~/.scitex/cards/cards.db)". After the PostgreSQL
#: cutover that sentence was FALSE in both halves at once: the backend is
#: postgres on this fleet, and ``~/.scitex/cards/cards.db`` is the abandoned
#: pre-migration file, still on disk, still holding thousands of real cards.
#:
#: On 2026-08-06 it misled the maintainer of this very package. Measuring a
#: fleet-wide defect, I read that path directly — because my own instructions
#: named it — and produced a full set of numbers from a four-day-old snapshot,
#: which reached three docstrings, a pull-request body and a card comment to the
#: agent who reported the bug before a positive control caught it. The file is
#: not obviously stale: it answered plausibly and reproduced the reporter's own
#: count exactly, which is what stopped me checking.
#:
#: So the sentence now names only the QUESTION and the verb that answers it.
#: ``resolve_store`` reports the resolved target and its backend; anything this
#: string asserted about either would be a second thing to keep in step, and
#: this is the second time it has fallen out of step (YAML -> a local
#: database, then that -> PostgreSQL).
_STORE_LINE = (
    "The store is whatever $SCITEX_CARDS_DB resolves to, and that resolved "
    "target is the SOLE store identity. Do NOT assume a backend or a default "
    "path — the deployment decides both. Call resolve_store, which reports the "
    "target you actually resolved to, and read the store ONLY through this "
    "package's verbs: opening a store file directly is how an abandoned one "
    "gets mistaken for the live board. An unresolvable/absent store raises "
    "rather than silently handing you an empty board."
)


def build_instructions(agent_id: str | None) -> str:
    """Render the MCP server instructions for THIS agent's REAL scope.

    Parameters
    ----------
    agent_id : str | None
        The resolved agent identity (``$SCITEX_CARDS_AGENT_ID``), or ``None`` /
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
            f"You are `{agent_id}` (from $SCITEX_CARDS_AGENT_ID): call list_tasks "
            f"with scope='agent:{agent_id}' for your slice — that scope names YOU, "
            "so it returns cards assigned to you even when a peer filed them under "
            "`fleet`, `ecosystem` or no scope at all. If you need certainty, "
            f"list_tasks(assignee='{agent_id}') is the direct question and the two "
            "should agree. Stamp your writes with that same id."
        )
    else:
        slice_line = (
            "Your identity is UNRESOLVED ($SCITEX_CARDS_AGENT_ID is unset or "
            "blank), so this server cannot name your scope — do NOT guess one, "
            "because a wrong `scope` silently hides your own cards. Discover it "
            "instead: call list_tasks with NO scope to see every card (yours are "
            "the ones whose agent/assignee names you), and resolve_store to "
            "confirm which store you are reading. Then set "
            "SCITEX_CARDS_AGENT_ID=<your-agent-id> so scoped queries work."
        )
    return (
        "scitex-cards: shared task store across agents and hosts. "
        f"{slice_line} {_STORE_LINE}"
    )


__all__ = ["build_instructions"]

# EOF
