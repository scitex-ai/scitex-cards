#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Graph + tasks handlers: thin adapters over the scitex-todo Python API.

Zero task logic here — everything delegates to ``scitex_cards``: the store is
resolved + loaded by ``services.get_board`` (which calls ``resolve_tasks_path``
and ``load_tasks``), the mermaid source comes from ``build_mermaid``, and node
colors come from ``STATUS_STYLE``.
"""

from pathlib import Path

from django.http import JsonResponse

# Fleet-liveness builder — extracted to graph_fleet.py (line-limit split).
# `_build_fleet` feeds the payload's "fleet" key below; the private helpers
# are re-exported so any dotted reference through handlers.graph resolves.
from .graph_fleet import (  # noqa: F401
    _LIVENESS_NONRUNNABLE,
    _build_fleet,
    _last_activity_key,
    _priority_key,
)

#: The board's HTML templates live here (board_v3.html + any partials). Their
#: max mtime is a cheap fingerprint for "the GUI code changed" — see
#: :func:`_board_asset_rev`.
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "scitex_cards"


def _board_asset_rev() -> float:
    """Max mtime across the board's HTML templates — a cheap GUI-version stamp.

    The ``/rev`` poll already tells the browser when the store DATA changed;
    this adds a second signal for when the board's own GUI CODE changed (a
    template/JS/CSS edit, or a merged PR that updated ``board_v3.html`` under
    the editable install). In DEBUG Django already serves the new template on
    the next full page load — the open pane just doesn't know to fetch it. The
    frontend compares this stamp across polls and hard-reloads (``location.
    reload()``) when it moves, so the operator's board pane picks up GUI
    updates WITHOUT a manual restart / F5. Falls back to ``0.0`` if the
    template dir can't be stat'd (never raises into the poll response).
    """
    try:
        mtimes = [p.stat().st_mtime for p in _TEMPLATE_DIR.glob("*.html")]
    except OSError:
        return 0.0
    return max(mtimes, default=0.0)


def _status_colors() -> dict:
    """Single-source the status -> color map from the core package.

    ``STATUS_STYLE`` maps status -> (fill, stroke, dasharray). The board only
    needs fill + stroke + dashed flag, so project that into a small JSON dict.
    """
    from scitex_cards._diagram import STATUS_STYLE

    return {
        status: {"fill": fill, "stroke": stroke, "dashed": bool(dash)}
        for status, (fill, stroke, dash) in STATUS_STYLE.items()
    }


def _compute_deadline_next(t):
    """Lazy proxy to scitex_cards._model.next_deadline_for_task.

    Kept here so the handler module avoids a top-level import cycle in
    test contexts that import handlers before the model package is
    fully loaded. (hook-bypass: line-limit.)
    """
    from scitex_cards._model import next_deadline_for_task

    try:
        return next_deadline_for_task(t)
    except Exception:
        return None


def _build_graph(board) -> dict:
    """Build the {nodes, edges, status_colors, ...} payload from a board."""
    from scitex_cards._diagram import build_mermaid

    from ._comment_digest import comment_scalars, rescore_history

    ids = {t["id"] for t in board.tasks}

    nodes = [
        {
            "id": t["id"],
            "title": t["title"],
            "status": t["status"],
            "priority": t.get("priority"),
            "note": t.get("note"),
            "repo": t.get("repo"),
            # `parent` is the nesting field that drives the frontend
            # drill-down: children of a node N are tasks whose
            # `parent == N.id`. Emit it verbatim; if it points to an unknown
            # id the frontend treats this task as top-level (same lenient
            # stance as edges to unknown ids).
            "parent": t.get("parent"),
            # Append-only comment thread (list of {ts, author, text}); always
            # a list so the frontend can render / count without null-checks.
            #
            # BEING REMOVED, in three shippable steps rather than one. This
            # field is 4.4 MB of a 19.8 MB payload (measured 2026-07-17 at
            # 1,837 cards; the store is 2,854 now) and every board poll
            # carries all of it. The previous attempt removed it and the
            # summary fields in a single branch, which broke every consumer
            # at once and sat unmergeable for twelve days until its owner
            # stopped existing — so:
            #   1. THIS STEP: emit the summary scalars ALONGSIDE comments[].
            #      Purely additive, every consumer keeps working, ships green.
            #   2. migrate the list consumers onto the scalars, one PR each.
            #   3. delete this line once nothing reads it.
            # Step 1 costs ~445 KB (2% of the payload) and buys the ability
            # to land 2 incrementally instead of in a flag day.
            "comments": t.get("comments") or [],
            # List-view stand-ins for the thread above. The full thread is
            # at /chat/<card_id>, which already preserves each comment's
            # `kind` so the route-trace timeline keeps working.
            **comment_scalars(t),
            # The Matrix view is the one list surface that needs comment
            # CONTENT rather than a summary: it reads the [old, new] axis
            # pairs off `kind: "rescore"` comments to draw quadrant
            # transitions. Serving just those keeps it working when
            # comments[] goes. Measured 0.11% of comments[] (30 events on
            # 2,864 cards) — see handlers/_comment_digest.py.
            "rescore_history": rescore_history(t),
            # `kind` discriminator + compute metadata (north-star pillar #1,
            # validated by `_model._validate_tasks`). `kind: null` over the
            # wire = "task" (the default). FE renders compute affordances
            # (⚙ glyph + KV table) on `kind === "compute"` and decision
            # affordances (⚖️ glyph + LOUD operator-decision halo + impact
            # badge) on `kind === "decision"`.
            "kind": t.get("kind"),
            "job_id": t.get("job_id"),
            "host": t.get("host"),
            "command": t.get("command"),
            "started_at": t.get("started_at"),
            "finished_at": t.get("finished_at"),
            # `blocker` — variant that's blocking a status=blocked row
            # (operator TG 9522 + 9524, ADR-0004). Closed enum
            # `compute|dependency|dep|operator-decision|agent-wait|none`;
            # `null` on non-blocked rows + on blocked rows where the
            # variant hasn't been named yet (soft-degrade — FE renders a
            # generic 🚧 in that case, no extra badge). `dep` is the
            # legacy spelling kept during the deprecation window; `dependency`
            # is canonical per ADR-0007.
            "blocker": t.get("blocker"),
            # Operator-co-designed fields (TG 9667 / ADR-0007). Forwarded
            # verbatim to the FE so the board-v3 layout (filter bar + cards
            # + BLOCKING YOU panel) can render directly from the Task
            # dataclass shape without a second wire format.
            "task": t.get("task"),
            "project": t.get("project"),
            "created_at": t.get("created_at"),
            "goal": t.get("goal"),
            # P4 (lead approved 2026-06-12) — deadline + scheduled ISO-8601
            # fields. FE date-pill prefers `deadline` over the title-parsed
            # date when present; absent → legacy title parse keeps working.
            "deadline": t.get("deadline"),
            "scheduled": t.get("scheduled"),
            # P4 PR3 (lead-approved 2026-06-12) — multi/recurring.
            # `deadlines` is the optional list form (mutually exclusive
            # with `deadline`); `deadline_next` is the SERVER-COMPUTED
            # next occurrence (recurring + multi expanded) the FE uses
            # for date-pill / sort / OVERDUE. Imported lazily to keep
            # the handler module light.
            "deadlines": t.get("deadlines"),
            "deadline_next": _compute_deadline_next(t),
            "agent": t.get("agent"),
            "last_activity": t.get("last_activity"),
            "pr_url": t.get("pr_url"),
            "issue_url": t.get("issue_url"),
            # USER-role fields (scitex-todo's entity is the USER;
            # an agent is just user.kind=agent). The detail drawer renders
            # a ROLES section from these. `created_by` is the creating user
            # (absent on legacy rows — FE falls back to the earliest comment
            # author, else "—"). `collaborators` / `subscribers` are the
            # persistent role lists (ADR-0009); always emitted as lists so
            # the FE can render "—" for empty without null-checks.
            "created_by": t.get("created_by"),
            "collaborators": t.get("collaborators") or [],
            "subscribers": t.get("subscribers") or [],
            # ADR-0011 §8 — the urgency×importance matrix layout's two axes
            # (1-5 each) and the engine's computed `rank`. Forwarded VERBATIM,
            # like every other Task field above, so the matrix renders from
            # the same wire format instead of needing a second endpoint.
            #
            # `None` until the schema-v5 work lands (scitex-cards' card
            # scitex-cards-schema-v5-axes-rank-rescore-verb-20260717), which
            # is why these are `.get()` and not required: 14-matrix.js treats
            # an absent axis as UNSCORED and renders the card in its tray. It
            # never coerces a missing axis to a coordinate — a card drawn at a
            # position nobody chose is a claim nobody made.
            #
            # READ-ONLY here. `rank` is the engine's output (ADR-0011 §1:
            # computed, never asserted); the GUI never writes it, and a drag
            # (PR 2) goes through the `rescore_task` store verb so the
            # `rank_changed` card-event still reaches agents' inboxes.
            "urgency": t.get("urgency"),
            "importance": t.get("importance"),
            "rank": t.get("rank"),
        }
        for t in board.tasks
    ]

    edges = []
    for t in board.tasks:
        tid = t["id"]
        for dep in t.get("depends_on", []) or []:
            if dep in ids:
                edges.append({"source": dep, "target": tid, "kind": "depends_on"})
        for target in t.get("blocks", []) or []:
            if target in ids:
                edges.append({"source": tid, "target": target, "kind": "blocks"})

    return {
        "nodes": nodes,
        "edges": edges,
        "status_colors": _status_colors(),
        "mermaid": build_mermaid(board.tasks),
        "store_path": str(board.store_path),
        "task_count": len(board.tasks),
        # HONEST EMPTY STATE: True when the store was READ and held no cards —
        # a legitimate 0-card board, on which the frontend renders the normal
        # empty board instead of the red load-error banner. It is NOT "the
        # store file is missing": a store that cannot be read raises out of
        # get_board and arrives here as a 500, never as this flag. See
        # BoardState.empty_store for why that distinction is load-bearing.
        "empty_store": board.empty_store,
        # Fleet liveness — per-agent at-a-glance summary the operator can
        # scan from the board header to answer "who is alive + working on
        # what + blocked on me" without leaving the board (ADR-0008 design,
        # ticket `proj-scitex-todo-fleet-liveness`, operator TG 9576 acute
        # pain: 返事が来ない＝私にとって死んだのと同じ). FIRST SLICE — derived
        # from the already-loaded board tasks; the sidecar daemon + cross-host
        # roll-up land in follow-up PRs (no schema change today).
        "fleet": _build_fleet(board.tasks),
        # P10 (lead a2a 2026-06-12) — user-defined project clusters from
        # the same store. Empty list when the YAML has no ``groups:`` key
        # (back-compat). FE renders group headers above the per-project
        # column grid + a horizontal "spans_all" strip for the lead group.
        "groups": [g.to_dict() for g in (board.groups or [])],
    }


#: In-process cache of the BUILT graph payload, keyed on
#: ``(store_path_str, board.mtime, board.sig)``. Skips the per-request
#: ``_build_graph`` rebuild when the store hasn't changed (operator TG12911).
#: The key INCLUDES ``board.sig`` (the DB's read-stable content version), NOT
#: ``board.mtime`` alone: a DB write never moves the identity file's mtime, so
#: an mtime-only key served the STALE graph after a reorder. ``board.sig``
#: moves on any DB change, so the graph self-invalidates with the board.
_GRAPH_PAYLOAD_CACHE: dict = {}
_GRAPH_PAYLOAD_CACHE_TTL_S = 3_600.0  # 1h, mirrors BoardState's TTL.


def _graph_cache_gc() -> None:
    """Drop stale entries from :data:`_GRAPH_PAYLOAD_CACHE`."""
    import time

    now = time.time()
    stale = [
        k
        for k, (_, ts) in _GRAPH_PAYLOAD_CACHE.items()
        if now - ts > _GRAPH_PAYLOAD_CACHE_TTL_S
    ]
    for k in stale:
        _GRAPH_PAYLOAD_CACHE.pop(k, None)


def _graph_cache_reset() -> None:
    """Test hook — clear the cache between assertions."""
    _GRAPH_PAYLOAD_CACHE.clear()


def handle_graph(request, board):
    """GET graph -> structured nodes + edges + status colors (+ mermaid).

    Cached by ``(store_path, board.mtime, board.sig)``; on hit, returns the
    prior payload directly. ``board.sig`` is the DB's read-stable content
    version, so any write rolls the cache forward by one rebuild — including a
    DB write that never touches the identity file's mtime, which an mtime-only
    key missed. The auto-update SSE wire (PR-C in the lead-approved Stage 2
    plan) will additionally PUSH the new payload — this cache is the same
    derivation, just stored.
    """
    import time

    _graph_cache_gc()
    key = (str(board.store_path), board.mtime, board.sig)
    hit = _GRAPH_PAYLOAD_CACHE.get(key)
    if hit is not None:
        payload, _ = hit
        # Touch the access time so the GC doesn't sweep a hot key.
        _GRAPH_PAYLOAD_CACHE[key] = (payload, time.time())
        return JsonResponse(payload)
    payload = _build_graph(board)
    _GRAPH_PAYLOAD_CACHE[key] = (payload, time.time())
    return JsonResponse(payload)


def handle_tasks(request, board):
    """GET tasks -> the raw validated task list (for grids / debugging)."""
    return JsonResponse(
        {
            "tasks": list(board.tasks),
            "store_path": str(board.store_path),
            # Same honest-empty-state flag as the /graph payload: the store
            # was read and holds no cards (see BoardState.empty_store). An
            # unreadable store never reaches here — it is a 500.
            "empty_store": board.empty_store,
        }
    )


def handle_ping(request, board):
    """GET ping -> health check (no store needed)."""
    return JsonResponse({"status": "ok"})


def handle_rev(request, board):
    """GET rev -> a cheap revision fingerprint of the store.

    Returns the store's ``mtime`` (float) and task ``count`` without building
    the full graph payload, so the frontend can poll this to detect when
    another agent has changed the shared YAML and trigger a refresh. The board
    is loaded mtime-cached, so unchanged stores hit the cache.
    """
    return JsonResponse(
        {
            "mtime": board.mtime,
            "count": len(board.tasks),
            "store_path": str(board.store_path),
            # GUI-code version stamp: lets the open pane hard-reload itself
            # when the board template changes (no manual restart/F5).
            "asset_rev": _board_asset_rev(),
        }
    )


# EOF
