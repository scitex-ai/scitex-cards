#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The may-stop DETECTOR — "does this agent have runnable work right now?"

The never-stop infrastructure's cards half (operator directives 2026-07-18;
card ``may-stop-hook-cards-runnable-work-20260718``). ONE pure query with
TWO consumers on the sac side (their actuator card, edge-linked):

1. the turn-end Stop hook — blocks an agent's stop while work exists
   (exit 2 + stderr hints), because a notification can be ignored but a
   hook cannot;
2. the idle-at-prompt watchdog — the case no Stop event ever reaches; the
   stderr hints become the injected resume prompt.

RUNNABLE means, per the locked contract:

* an ``in_progress`` card owned by the agent — work it or update it;
* a ``blocked`` card with NO named gate (blocker absent/empty/``none``) —
  either resume it or name what exactly blocks it;
* a ``deferred`` card whose ``scheduled`` time has arrived;
* any owned card past a non-recurring deadline (``is_overdue``);
* unread inbox notifications.

A ``blocked`` card WITH a named external gate is NOT runnable here — that
is the one legitimate wait. (Stop-cause #6, waiting-as-posture — counting
a blocked card's un-gated SLICES as runnable — needs the cards to name
what the gate covers; it lands with that convention, documented on the
card, not silently guessed here.)

This module never writes: detection is a read, actuation is sac's.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from ._deadlines import is_overdue
from ._inbox import poll_inbox
from ._store_list import list_tasks

#: Statuses that can carry runnable work for their owner.
_ACTIVE_STATUSES = ("in_progress", "blocked", "deferred")

#: Blocker values that mean "no gate is actually named".
_UNGATED_BLOCKERS = {None, "", "none"}


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _parse_ts(value) -> "_dt.datetime | None":
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Bare dates / naive stamps (the store's common shapes) are UTC by
    # convention — normalize so comparisons against the aware clock work.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _owned(agent: str, store) -> list[dict]:
    """The agent's non-terminal cards (owner = ``agent`` or ``assignee``)."""
    rows = list_tasks(store, statuses=list(_ACTIVE_STATUSES))
    return [t for t in rows if agent in (t.get("agent"), t.get("assignee"))]


def may_stop(
    agent: str,
    store: str | Path | None = None,
    *,
    now: "_dt.datetime | None" = None,
) -> dict:
    """Return the runnable-work verdict for ``agent``.

    Returns ``{"agent", "runnable", "items": [{card_id, reason,
    next_action}, ...], "idle_seconds"}``. ``runnable`` is True iff
    ``items`` is non-empty. ``idle_seconds`` is the age of the newest
    ``last_activity`` across the agent's non-terminal cards (None when the
    agent owns none) — the staleness signal the delegation chain and the
    watchdog share.
    """
    if not agent or not str(agent).strip():
        raise ValueError("may_stop: 'agent' is required")
    agent = str(agent).strip()
    moment = now or _utc_now()
    items: list[dict] = []
    newest_activity: "_dt.datetime | None" = None

    for task in _owned(agent, store):
        tid = str(task.get("id") or "(no-id)")
        status = task.get("status")
        ts = _parse_ts(task.get("last_activity"))
        if ts and (newest_activity is None or ts > newest_activity):
            newest_activity = ts
        if status == "in_progress":
            items.append(
                {
                    "card_id": tid,
                    "reason": "in_progress card",
                    "next_action": "work it, update it, or close it",
                }
            )
        elif status == "blocked":
            blocker = task.get("blocker")
            if (
                blocker.strip().lower() if isinstance(blocker, str) else blocker
            ) in _UNGATED_BLOCKERS:
                items.append(
                    {
                        "card_id": tid,
                        "reason": "blocked with no named gate",
                        "next_action": "resume it, or name exactly what blocks it",
                    }
                )
        elif status == "deferred":
            scheduled = _parse_ts(task.get("scheduled"))
            if scheduled is not None and scheduled <= moment:
                items.append(
                    {
                        "card_id": tid,
                        "reason": "scheduled time reached",
                        "next_action": "start it, or re-schedule with a reason",
                    }
                )
        if is_overdue(task, now=moment):
            items.append(
                {
                    "card_id": tid,
                    "reason": "past its deadline",
                    "next_action": "finish it, or move the deadline deliberately",
                }
            )

    unread = poll_inbox(agent, unseen_only=True, store=store)
    if unread:
        # NAME THE SENDER AND SHOW THE TEXT. A bare count is unactionable by
        # construction: it cannot distinguish the operator asking a direct
        # question from a card-status echo, so the rational response to it is
        # to defer — and that is exactly what happened. On 2026-07-28 the
        # operator asked sac for its top-5 tasks TWICE and told it they were
        # migrating off Telegram onto cards DMs; all three arrived here as the
        # number 4, went unread for two hours, and the operator reasonably
        # believed they had been messaged while the agent believed nothing had
        # arrived. Both were right, which is the worst shape a delivery bug can
        # take, and it blocks the migration rather than merely annoying.
        #
        # The records already carry everything needed — _threads.py's
        # dm-dispatch enqueues actor, card_id (the thread) and the full body —
        # so this was discarding data it already had. sac cannot fix it on
        # their side by design: their decider forwards this `reason` VERBATIM
        # to the agent, deliberately, so it need not know our output format.
        # That makes the wording here the whole user-visible signal.
        latest = unread[-1]
        who = (latest.get("actor") or "").strip() or "unknown sender"
        text = " ".join((latest.get("body") or "").split())
        snippet = text[:80] + ("…" if len(text) > 80 else "")
        detail = f" — latest from {who}" + (f": {snippet}" if snippet else "")
        items.append(
            {
                "card_id": "(inbox)",
                "reason": f"{len(unread)} unread notification(s){detail}",
                "next_action": "poll_notifications and act on them",
            }
        )

    idle_seconds = (
        max(0, int((moment - newest_activity).total_seconds()))
        if newest_activity is not None
        else None
    )
    return {
        "agent": agent,
        "runnable": bool(items),
        "items": items,
        "idle_seconds": idle_seconds,
    }


__all__ = ["may_stop"]

# EOF
