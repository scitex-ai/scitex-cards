#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/timeline`` Django endpoint — fleet TIME-RASTER surface.

Operator-direct ask (TG, relayed by lead a2a ``d0f7a0e3``, 2026-06-14):
build a "Time View" / タイム View so the operator watches ONE screen and
sees the whole fleet in motion. Horizontal axis = TIME (scrolling/live,
~24 h default), each task/event = a mark that appears when it starts
(``created_at`` / ``started_at``) and fades when it completes
(``_log_meta.completed_at``). Dependencies (``depends_on`` / ``blocks``)
draw as lines between marks. Lanes = agent or group.

Slots as the 5th LAYOUT toggle alongside Graph / Column / Table /
Calendar (the floor visual is a raster plot per agent).

Endpoint shape::

    GET /timeline?window_hours=24&lane_by=agent

Response::

    {
      "events": [
        {"id","title","agent","group","lane",
         "started_at","ended_at|null","status","priority","kind"}, ...
      ],
      "edges": [{"source": <id>, "target": <id>,
                 "kind": "depends_on"|"blocks"}, ...],
      "window_start": "<ISO>", "window_end": "<ISO>",
      "lane_by": "agent"|"group",
      "lanes": [<lane names sorted>, ...],
      "store_path": "<path>"
    }

Design principles (HARD, from the operator brief):

- **fail-loud**: if the underlying task store is unreadable,
  :func:`load_tasks` raises; we DO NOT degrade silently to ``events: []``.
- **registry-sourced**: the store already carries ``created_at`` /
  ``started_at`` / ``_log_meta.completed_at`` / ``agent`` / ``group`` /
  ``depends_on`` / ``blocks``. We re-project — never duplicate state.
- **NO hardcoded proper nouns**: lane labels come from the task data;
  the ungrouped sentinel is the empty string surfaced as ``"(ungrouped)"``
  for readability, but it is not a content literal — it is a label.
- **read-only**: ``POST`` returns ``405``.

Method violations return ``405``. The Phase-0 fail-loud principle
applies (mirrors ``handlers/runnable.py``): underlying-store errors
bubble into Django's 500 handler.

Out of scope (deferred per the operator brief — flagged with CARDs in
the FE):
- Pan / zoom / drag-to-reschedule (``update`` is the side channel today).
- WebSocket push (polling is fine for the floor — 30s, same cadence
  as the CI-status pills).
- Sub-second resolution (minute-level is enough for fleet visibility).
"""

from __future__ import annotations

import datetime as _dt

from django.http import HttpRequest, HttpResponse, JsonResponse

# ---------------------------------------------------------------------------
# Defaults — operator-stated floor.
# ---------------------------------------------------------------------------

#: Default sliding-window size when ``window_hours`` is absent or unparseable.
#: The operator's brief calls out "last ~24 h default".
_DEFAULT_WINDOW_HOURS: float = 24.0

#: Sentinel label for tasks that don't have a value in the selected ``lane_by``
#: dimension. Not a content literal — purely a display string for the lane axis
#: (per the brief: tasks with no lane value go into an "(ungrouped)" lane).
_UNGROUPED_LANE: str = "(ungrouped)"

#: Closed set of accepted ``lane_by`` values. ``agent`` is the operator default
#: (raster plot per agent — the brief's anchor visual). ``group`` rasters by
#: the T1.1 group field; ``project`` by the task's project (operator CARD
#: 2026-06-17 by-project view); ``task`` gives ONE lane per task (the basis
#: of the "simple" per-task view).
_VALID_LANE_BY: frozenset[str] = frozenset({"agent", "group", "project", "task"})


# ---------------------------------------------------------------------------
# Helpers — pure / stateless so they're cheap to test.
# ---------------------------------------------------------------------------


def _parse_iso(ts: str | None) -> _dt.datetime | None:
    """Parse an ISO-8601 string into a tz-aware ``datetime`` or ``None``.

    Mirrors the lenient parser the fleet builder uses
    (``handlers/graph._seconds_since``): ``"Z"`` is treated as ``+00:00``,
    naive strings get ``UTC`` attached so all comparisons stay tz-aware.
    """
    if not ts:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _completed_at(task: dict) -> str | None:
    """Return the task's ``_log_meta.completed_at`` ISO string, or ``None``.

    The ``_log_meta`` field is opaque (see ``_model.Task._log_meta``); the
    writer side stamps ``completed_at`` via :func:`scitex_cards._store.complete_task`.
    Returns the raw string so the FE can render it verbatim.
    """
    meta = task.get("_log_meta")
    if not isinstance(meta, dict):
        return None
    val = meta.get("completed_at")
    if not val:
        return None
    return str(val)


def _started_at(task: dict) -> str | None:
    """Return the task's start timestamp.

    Precedence (per the brief): explicit ``started_at`` (compute-job stamp)
    first, then ``created_at`` (when the task was added). Returns ``None`` if
    neither is present so the caller can skip the row from the window-filter.
    """
    s = task.get("started_at")
    if s:
        return str(s)
    c = task.get("created_at")
    if c:
        return str(c)
    return None


def _lane_for(task: dict, lane_by: str) -> str:
    """Project the task onto a lane label.

    For ``lane_by=agent`` the value is the OWNER SSOT
    :func:`scitex_cards._owner.card_owner` (``agent`` falling back to
    ``assignee``) — the same rule the board grouping + comment relay + notify
    use, so every owner read agrees. For ``lane_by=group`` it's the T1.1
    ``group`` field; ``lane_by=project`` the task's ``project``;
    ``lane_by=task`` the task title (ONE lane per task — the "simple" view).
    An empty / missing value maps to :data:`_UNGROUPED_LANE` — never
    duplicated as a hard-coded list anywhere.
    """
    if lane_by == "group":
        val = task.get("group")
    elif lane_by == "project":
        val = task.get("project")
    elif lane_by == "task":
        # ONE lane per task — the "simple" per-task view. The title is the
        # lane label (falls back to task/id). Distinct titles → distinct
        # rows; a rare title collision just shares a row, which is fine.
        val = task.get("title") or task.get("task") or task.get("id")
    else:
        from ..._owner import card_owner

        val = card_owner(task)
    if val is None or str(val).strip() == "":
        return _UNGROUPED_LANE
    return str(val)


def _event_within_window(
    task: dict,
    *,
    window_start: _dt.datetime,
    window_end: _dt.datetime,
) -> bool:
    """True iff ANY of the task's three timestamps falls in the window.

    Brief: "Selects tasks whose ``created_at`` or ``started_at`` OR
    ``_log_meta.completed_at`` falls within the window." We check all three.
    """
    for ts in (
        task.get("started_at"),
        task.get("created_at"),
        _completed_at(task),
    ):
        parsed = _parse_iso(ts) if isinstance(ts, str) else None
        if parsed is None:
            continue
        if window_start <= parsed <= window_end:
            return True
    return False


def _parse_window_hours(raw: str | None) -> float:
    """Return ``window_hours`` as a positive float, defaulting on bad input.

    Floor: never raise on a bad value — the FE may pass "" or a stale token;
    fall back to :data:`_DEFAULT_WINDOW_HOURS` so the operator always sees
    something. Cap at ~3 months to bound the response size while still
    covering the FE's day / week / month window selector.
    """
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_WINDOW_HOURS
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_WINDOW_HOURS
    if v <= 0:
        return _DEFAULT_WINDOW_HOURS
    # Cap at ~3 months (92 d) to keep the response bounded; the FE window
    # selector tops out at "month" (720 h), so this leaves headroom without
    # ever returning an unbounded sweep.
    return min(v, 2208.0)


def _build_payload(
    tasks: list[dict],
    *,
    window_hours: float,
    lane_by: str,
    now: _dt.datetime | None = None,
) -> dict:
    """Assemble the ``/timeline`` JSON payload from a raw task list.

    Pure function — no Django, no I/O. Easy to unit-test, and the view
    just wires it to :func:`load_tasks` + :class:`JsonResponse`.
    """
    cur = now or _dt.datetime.now(tz=_dt.timezone.utc)
    window_start = cur - _dt.timedelta(hours=window_hours)
    window_end = cur

    events: list[dict] = []
    event_ids: set[str] = set()
    for t in tasks:
        if not _event_within_window(
            t, window_start=window_start, window_end=window_end
        ):
            continue
        lane = _lane_for(t, lane_by)
        from ..._owner import card_owner

        events.append(
            {
                "id": t.get("id"),
                "title": t.get("title") or t.get("task") or t.get("id"),
                "agent": card_owner(t),
                "group": t.get("group"),
                "lane": lane,
                "started_at": _started_at(t),
                "ended_at": _completed_at(t),
                "status": t.get("status"),
                "priority": t.get("priority"),
                "kind": t.get("kind"),
            }
        )
        tid = t.get("id")
        if tid is not None:
            event_ids.add(str(tid))

    # Edge filter — only include depends_on / blocks edges where BOTH
    # endpoints are in the events set (per the brief). Keeps the wire
    # payload bounded and the FE's draw loop O(visible).
    edges: list[dict] = []
    for t in tasks:
        tid = t.get("id")
        if tid is None or str(tid) not in event_ids:
            # The TARGET (depends_on direction: source = dep, target = tid)
            # could still be in-window via the source's row — we walk both
            # rows so a single visit suffices either way.
            pass
        for dep in t.get("depends_on", []) or []:
            sd = str(dep)
            st = str(tid) if tid is not None else ""
            if sd in event_ids and st in event_ids:
                edges.append({"source": sd, "target": st, "kind": "depends_on"})
        for target in t.get("blocks", []) or []:
            sd = str(tid) if tid is not None else ""
            st = str(target)
            if sd in event_ids and st in event_ids:
                edges.append({"source": sd, "target": st, "kind": "blocks"})

    # De-dup edges (a depends_on edge stored on both ends would otherwise
    # double up). Order-preserving so the FE draws deterministically.
    seen: set[tuple[str, str, str]] = set()
    unique_edges: list[dict] = []
    for e in edges:
        key = (e["source"], e["target"], e["kind"])
        if key in seen:
            continue
        seen.add(key)
        unique_edges.append(e)

    lanes = sorted({e["lane"] for e in events})

    return {
        "events": events,
        "edges": unique_edges,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "lane_by": lane_by,
        "lanes": lanes,
    }


# ---------------------------------------------------------------------------
# Django view.
# ---------------------------------------------------------------------------


def timeline_view(request: HttpRequest) -> HttpResponse:
    """Serve the operator-facing JSON timeline.

    Reads through :func:`scitex_cards._django.services.get_board` — the board's
    mtime-keyed cache — NOT through a bare ``load_tasks``.

    That distinction is the whole performance story of this endpoint. The FE
    polls /timeline every 30 s, and a direct ``load_tasks`` re-parsed the entire
    5 MB YAML on EVERY poll. Measured on the operator's live store (1,352 cards):

        load_tasks()    1.22 s      <- 99% of the response time
        _build_payload  0.01 s      <- the actual timeline work

    The endpoint was ~6 s and essentially none of it was timeline computation.
    ``get_board`` already caches on MAX(mtime) across the store and every lane,
    so it re-reads exactly when something has actually been written and serves
    from memory otherwise.

    It also fixes a CORRECTNESS gap: ``load_tasks`` returned only the GLOBAL
    store, while the board page renders the UNION via ``get_board``. The two
    surfaces could therefore disagree about which cards exist. Now they cannot.

    Fail-loud is preserved: ``get_board`` calls ``load_tasks`` on the global
    store, so an unreadable store still raises into Django's 500 handler rather
    than degrading to ``events: []``.
    """
    if request.method != "GET":
        return JsonResponse(
            {"error": "method-not-allowed", "method": request.method},
            status=405,
        )

    from ..services import get_board

    window_hours = _parse_window_hours(request.GET.get("window_hours"))
    lane_by_raw = (request.GET.get("lane_by") or "agent").strip()
    lane_by = lane_by_raw if lane_by_raw in _VALID_LANE_BY else "agent"

    board = get_board()
    tasks = board.tasks

    payload = _build_payload(tasks, window_hours=window_hours, lane_by=lane_by)
    payload["store_path"] = str(board.store_path)
    # HONEST EMPTY STATE: the store was READ and holds no cards — a legitimate
    # 0-event timeline, not an error (see BoardState.empty_store). Fail-loud is
    # intact for every other shape: a store that cannot be read raises out of
    # ``get_board`` above, and this view deliberately does not catch it.
    payload["empty_store"] = board.empty_store
    return JsonResponse(payload, json_dumps_params={"default": str})


__all__ = ["timeline_view"]

# EOF
