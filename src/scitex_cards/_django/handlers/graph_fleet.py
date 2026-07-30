#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fleet-liveness payload builder for the ``/graph`` wire (ADR-0008).

Extracted VERBATIM from ``handlers/graph.py`` (line-limit split; the mirrored
test file ``tests/scitex_cards/_django/handlers/test_graph_fleet.py`` predates
and pins this module's behavior through the views layer). ``graph.py`` imports
:func:`_build_fleet` and embeds its output as the payload's ``fleet`` key.
"""

from __future__ import annotations

# Statuses that exclude a task from the "runnable" count for liveness.
# Mirrors the task-harvest skill's non-runnable set (40_task-harvest.md):
# blocked / done / deferred / failed / cancelled are not "could be
# progressed now"; `goal` rows are umbrella nodes the harvest doesn't
# escalate either. ``cancelled`` (closed as not planned) is terminal, so
# it never counts toward runnable liveness — same as done/failed.
_LIVENESS_NONRUNNABLE: frozenset[str] = frozenset(
    {"blocked", "done", "deferred", "failed", "cancelled", "goal"}
)


def _priority_key(t: dict) -> tuple[int, str]:
    """Sort key: priority (lower = earlier; None sinks to the end), then id.

    Tasks without an explicit `priority` should rank LAST so the
    "current_task" derivation prefers explicitly-prioritized rows.
    """
    p = t.get("priority")
    return (10_000_000 if p is None else int(p), str(t.get("id") or ""))


def _last_activity_key(t: dict) -> str:
    """Sort key for "most recent activity": ISO-8601 strings sort lexically.

    Tasks without `last_activity` rank LAST (empty string sorts before any
    real ISO timestamp, so we negate by returning empty when present; the
    consumer reverses ordering). Returns the timestamp str verbatim — the
    `max()` caller uses it as a comparison key, not a parsed datetime.
    """
    return str(t.get("last_activity") or "")


def _build_fleet(tasks: list[dict], *, now=None) -> list[dict]:
    """Return a list of {agent, status, current_task, ...} summaries.

    Grouping field: `agent` (fall back to `assignee` for older rows that
    pre-date the operator-co-designed field rename — both are forwarded
    to the FE on every node payload too). Tasks WITHOUT an agent are
    excluded so the dot-strip stays small + readable.

    Status precedence (most attention-demanding first), per the
    task-harvest skill's 4-value blocker enum + the operator's
    "blocking-me" lens, plus the **working-status decay** rule
    (operator TG12739, lead a2a ``f556b755``, 2026-06-13):

      1. ``blocking-operator``  any task is blocker=operator-decision
      2. ``working``            any task is status=in_progress *AND* the
                                agent's most-recent ``last_activity`` is
                                within ``SCITEX_TODO_FLEET_WORKING_MIN``
                                minutes (default 10). Without the
                                freshness gate, agents that forgot to
                                flip in_progress→pending stay "working"
                                forever and the UI lies.
      3. ``stale``              any task is status=in_progress but the
                                agent's most-recent ``last_activity`` is
                                older than the working window (or absent).
                                This is the **decay** state — surfaces
                                the "forgot-to-flip" case as a distinct
                                signal so the operator can prune it.
      4. ``active``             no in_progress task, but the agent's
                                most-recent ``last_activity`` is within
                                ``SCITEX_TODO_FLEET_ACTIVE_MIN`` minutes
                                (default 60). Activity badge derived
                                from FRESHNESS, not manual status.
      5. ``idle``               otherwise.

    The two windows are env-configurable so the operator can tune
    "what counts as live" without a code change. They default
    ``working_min`` < ``active_min`` so the badges read as
    nested-confidence intervals: tight green-light "working", looser
    yellow-light "active", everything else "idle".

    Per-agent fields:
      name                    the agent's id (e.g. scitex-clew)
      status                  one of the five above
      current_task            title of the agent's most-urgent task
      current_task_id         id of the same
      last_activity           max(last_activity) across the agent's tasks
      task_count              total tasks owned
      runnable_count          tasks NOT in the non-runnable set (a proxy
                              for "what's queued waiting to be picked up";
                              feeds the task-harvest sweep's ESCALATE list)
      blocked_count           tasks with status=blocked
      blocking_operator_count count of the "waiting-on-operator" queue:
                              cards matching the board's BLOCKING-YOU
                              predicate (status=blocked AND
                              blocker=operator-decision), the "stuck on
                              YOU" subset the operator needs to see jump
                              out. Derived from the SAME predicate as
                              ``list_tasks(blocking_me=True)`` (the
                              ``_match(..., blocking_me=True)`` SSOT) — NOT
                              a re-implemented check.
      blocking_operator_ids   the ids of those same cards, so the FE can
                              link straight to the queue without re-walking
                              the store.
    """
    import datetime as _dt
    import os

    def _env_minutes(key: str, default: int) -> float:
        try:
            return float(os.environ.get(key, str(default)))
        except (TypeError, ValueError):
            return float(default)

    working_window_s = _env_minutes("SCITEX_TODO_FLEET_WORKING_MIN", 10) * 60.0
    active_window_s = _env_minutes("SCITEX_TODO_FLEET_ACTIVE_MIN", 60) * 60.0
    cur = now or _dt.datetime.now(tz=_dt.timezone.utc)

    def _seconds_since(ts: str) -> float | None:
        if not ts:
            return None
        try:
            parsed = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return (cur - parsed).total_seconds()

    from ..._owner import card_owner

    by_agent: dict[str, list[dict]] = {}
    for t in tasks:
        # Owner SSOT (agent||assignee). Owner-less rows are excluded from the
        # liveness dot-strip by design (keeps it small/readable); add_task now
        # REJECTS owner-less cards at creation, so this only ever skips legacy
        # rows pending re-home.
        a = card_owner(t)
        if not a:
            continue
        by_agent.setdefault(str(a), []).append(t)

    out: list[dict] = []
    for agent, items in sorted(by_agent.items()):
        # Status precedence.
        has_blocking_operator = any(
            t.get("blocker") == "operator-decision" for t in items
        )
        has_in_progress = any(t.get("status") == "in_progress" for t in items)
        last_activity = max(
            (str(t.get("last_activity") or "") for t in items),
            default="",
        )
        age_s = _seconds_since(last_activity)
        fresh_working = age_s is not None and age_s <= working_window_s
        fresh_active = age_s is not None and age_s <= active_window_s
        if has_blocking_operator:
            status = "blocking-operator"
        elif has_in_progress and fresh_working:
            status = "working"
        elif has_in_progress:
            # decay: in_progress but quiet for > working window → stale.
            status = "stale"
        elif fresh_active:
            status = "active"
        else:
            status = "idle"

        # current_task — prefer in_progress, then most-recent activity, then
        # highest-priority pending. Lets the dot-strip's tooltip answer
        # "what are they on right now" with the most-relevant single row.
        in_progress = [t for t in items if t.get("status") == "in_progress"]
        if in_progress:
            current = sorted(in_progress, key=_priority_key)[0]
        else:
            with_activity = [t for t in items if t.get("last_activity")]
            if with_activity:
                current = max(with_activity, key=_last_activity_key)
            else:
                pending = [t for t in items if t.get("status") == "pending"]
                pool = pending or items
                current = sorted(pool, key=_priority_key)[0]

        # `overdue_count` = tasks past their next deadline AND not in a
        # terminal state. Feeds the operator UX (todo-p6-overdue-ui):
        # "attended an overdue task but no suitable UI to act" — the
        # fleet strip + filter bar can now surface a per-agent overdue
        # tally without re-walking the store on the client side.
        from scitex_cards._model import is_overdue as _is_overdue

        # "Waiting-on-operator" queue (operator P1
        # todo-operator-blocking-queue-view): cards stuck on a
        # human decision. SSOT — reuse the board's BLOCKING-YOU
        # predicate (``_match(..., blocking_me=True)`` == the same
        # filter ``list_tasks(blocking_me=True)`` uses) so the count
        # and id list never drift from the canonical
        # ``status==blocked AND blocker==operator-decision`` rule.
        from ..._store import _match

        blocking_operator_ids = [
            str(t.get("id"))
            for t in items
            if _match(t, blocking_me=True) and t.get("id") is not None
        ]

        out.append(
            {
                "name": agent,
                "status": status,
                "current_task": current.get("task") or current.get("title"),
                "current_task_id": current.get("id"),
                "last_activity": last_activity or None,
                "task_count": len(items),
                "runnable_count": sum(
                    1
                    for t in items
                    if str(t.get("status") or "") not in _LIVENESS_NONRUNNABLE
                ),
                "blocked_count": sum(1 for t in items if t.get("status") == "blocked"),
                "blocking_operator_count": len(blocking_operator_ids),
                "blocking_operator_ids": blocking_operator_ids,
                "overdue_count": sum(1 for t in items if _is_overdue(t, now=cur)),
            }
        )
    return out


__all__ = [
    "_LIVENESS_NONRUNNABLE",
    "_build_fleet",
    "_last_activity_key",
    "_priority_key",
]

# EOF
