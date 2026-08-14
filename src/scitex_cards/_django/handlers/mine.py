#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``GET /mine`` — the cards belonging to whoever is asking, and nobody else.

The data half of the phone view (card
``cards-gui-phone-view-own-cards-20260814``; operator, 2026-08-14: he wants
his cards from his phone through scitex.ai). The page half consumes this.

WHY A DEDICATED ENDPOINT RATHER THAN A FILTER OVER ``/graph``. Two reasons,
both measured rather than assumed:

* ``/graph`` DOES NOT EMIT ``assignee`` at all (see ``handlers/graph.py``: it
  forwards ``agent``, ``created_by``, ``collaborators``, ``subscribers`` and
  stops). ``assignee`` is the canonical ownership field -- the store's own
  docs call ``list_tasks(assignee=…)`` "the direct question" -- so the board's
  existing payload cannot answer "is this card mine?" without a change either
  way.
* ``/graph`` ships the WHOLE board plus its edge list and mermaid source so the
  client can draw a graph. A phone on mobile data asking "what am I meant to
  be doing?" should not download every card in the fleet to display eight, and
  filtering client-side would mean the private answer travelled anyway.

OWNERSHIP IS THE STORE'S DEFINITION, NOT A NEW ONE. The query is
``list_tasks(scope="agent:<name>")``, whose documented semantic is precisely
this: "names an OWNER, not a lens ... returns every card assigned to <id> as
well as those filed under that scope, so work a peer filed under `fleet` or
under no scope still reaches the agent responsible for it." Re-deriving
ownership here would create a second definition of "mine" that drifts from the
CLI and MCP surfaces the same person also uses.

AN UNIDENTIFIED CALLER GETS 403, NOT THE BOARD. The failure that matters for
this feature is not an error page; it is a plausible one. Falling back to "show
everything" when identity does not resolve hands a visitor somebody else's
cards on a public scitex.ai and looks exactly like the feature working. So the
refusal is explicit, typed, and carries the reason -- which is also what lets
the page say "your account is not linked yet" instead of "unknown error".
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse

__all__ = ["mine_view"]

#: Order the phone renders statuses in: what is being worked, then what is
#: stuck, then what is waiting, then what is closed. Derived once here and
#: reused for BOTH sorting and the counts payload so the list order and the
#: summary can never disagree.
_STATUS_ORDER: tuple[str, ...] = (
    "in_progress",
    "blocked",
    "deferred",
    "goal",
    "done",
    "failed",
    "cancelled",
)

#: The per-card fields the phone view needs. An explicit allowlist rather than
#: forwarding the whole row: a card body can carry a long ``note``, a full
#: ``comments[]`` thread and arbitrary operator text, none of which a summary
#: list renders, all of which would be paid for on mobile data.
_CARD_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "status",
    "kind",
    "blocker",
    "priority",
    "project",
    "assignee",
    "agent",
    "deadline",
    "scheduled",
    "last_activity",
    "created_at",
    "pr_url",
    "issue_url",
    "urgency",
    "importance",
    "rank",
)


def _card(task: dict) -> dict:
    """Project one store row onto the phone payload.

    Keys absent from the row are OMITTED rather than emitted as ``null``, so
    "this card has no deadline" and "this build forgot to send deadlines" stay
    distinguishable on the client.
    """
    return {k: task[k] for k in _CARD_FIELDS if task.get(k) is not None}


def _sort_key(task: dict):
    """Most actionable first: status band, then priority, then most recent.

    ``priority`` is ascending because the store treats 1 as the most important
    (``priority: 1`` on the operator's own P1 cards). ``last_activity``
    descends via reversal at the call site rather than by negating a string.
    """
    try:
        band = _STATUS_ORDER.index(task.get("status") or "")
    except ValueError:
        # An unknown status sorts after every known one instead of raising:
        # a card the board cannot categorise must still be visible to its
        # owner, and a status enum that grew is not this endpoint's fault.
        band = len(_STATUS_ORDER)
    priority = task.get("priority")
    if not isinstance(priority, int):
        # Unprioritised sorts after prioritised. A missing priority is not
        # "priority 0" -- treating it as most-urgent would put every
        # unscored card above the operator's real P1s.
        priority = 10**6
    return (band, priority)


def mine_view(request: HttpRequest) -> HttpResponse:
    """Serve the requesting viewer's own cards.

    Query parameters
    ----------------
    ``?closed=1``
        Include terminal cards (done / failed / cancelled). Omitted by
        default: the phone view answers "what is on my plate", and a year of
        finished work buried under it is the opposite of that.
    """
    if request.method != "GET":
        return JsonResponse(
            {"error": "method-not-allowed", "method": request.method},
            status=405,
        )

    from ..._store_list import list_tasks
    from ..._throughput import TERMINAL_STATUSES
    from .._board_identity import resolve_viewer
    from .._request_store import read_store

    store = read_store(request)
    viewer = resolve_viewer(request, store=store)

    if not viewer.is_known:
        # 403 + a TYPED reason, mirroring how the board already answers an
        # absent store (a configuration state answers 4xx with a machine
        # -readable ``reason``, per test__board_reads_the_database). The page
        # branches on ``reason``; the human reads ``detail``.
        if viewer.source == "unlinked-email":
            detail = (
                f"Signed in as {viewer.email}, but that address is not linked "
                f"to a board identity yet, so there is no way to tell which "
                f"cards are yours."
            )
        else:
            detail = (
                "This board could not tell who you are. It has no per-user "
                "login configured, so it cannot show one person's cards "
                "rather than everyone's."
            )
        return JsonResponse(
            {
                "error": "identity-unresolved",
                "reason": viewer.source,
                "email": viewer.email,
                "detail": detail,
            },
            status=403,
        )

    # The store's own OWNER query -- see the module docstring. Read failures
    # are left to bubble into Django's 500 handler exactly like every other
    # board read: an unreadable store must never present as "you have no
    # cards", which is the believable-empty-board failure the 2026-07-29
    # outage taught this repo to refuse.
    tasks = list_tasks(store, scope=f"agent:{viewer.name}")

    include_closed = request.GET.get("closed") in ("1", "true", "yes")
    if not include_closed:
        tasks = [t for t in tasks if t.get("status") not in TERMINAL_STATUSES]

    # Newest activity first WITHIN each status band: sort by recency, then by
    # the band/priority key, relying on Python's stable sort to keep the
    # recency order inside each band rather than composing a mixed-direction
    # key out of a string.
    tasks.sort(key=lambda t: t.get("last_activity") or "", reverse=True)
    tasks.sort(key=_sort_key)

    counts: dict[str, int] = {}
    for task in tasks:
        status = task.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1

    return JsonResponse(
        {
            "viewer": {
                "name": viewer.name,
                "source": viewer.source,
                "email": viewer.email,
            },
            "cards": [_card(t) for t in tasks],
            "counts": counts,
            "total": len(tasks),
            "include_closed": include_closed,
        }
    )


# EOF
