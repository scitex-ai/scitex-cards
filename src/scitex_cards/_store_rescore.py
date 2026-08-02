#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``rescore_task`` — the rank engine's write verb (ADR-0011 §1/§8, v5-lite).

A drag in the matrix view (or any client) sets a card's two axes; THIS verb
is the only writer of ``rank``. Contract agreed on card
``scitex-cards-schema-v5-axes-rank-rescore-verb-20260717``:

- Both axes land on ONE card; the TOTAL ORDER over every scored card is
  recomputed server-side and persisted in the SAME locked write. Rank is
  COMPUTED, never asserted (ADR-0011 §1) — no client may write it.
- Score: ``2*importance + urgency`` — importance strictly dominates, so
  quadrant II (important, not urgent) always outranks III (urgent, not
  important): worst-II ``(u=1,i=3)`` scores 7, best-III ``(u=5,i=2)``
  scores 9... which would invert II/III on raw score alone; the guarantee
  therefore comes from the ORDER KEY, not the scalar: cards sort by
  ``(importance-is-high, score)`` so every II precedes every III, per the
  operator's ruling. Ties break by ``scored_at`` (older first — the aging
  component: waiting longer never costs position) then by id (deterministic
  under the 5s poller). Time-DECAY aging (positions improving without any
  write) is a dispatcher-era recompute concern, deliberately not simulated
  with a static stored int — documented, not smuggled.
- Cards in a terminal state ({done, cancelled, failed}) keep their axes but
  hold NO rank: rank is dispatch order, and finished work is not in the
  queue. Their rank key is removed during recompute.
- ONLY the rescored card gets ``last_activity`` + the audit entry.
  Every other card's ``rank`` int changes SILENTLY — a re-rank is engine
  metadata, not owner activity, and restamping activity fleet-wide would
  reset every inactivity-nudge clock (the priority.py lesson, binding).
- AUDIT: an append-only ``comments[]`` entry with ``kind: "rescore"`` and a
  structured ``rescore`` payload ``{urgency: [old,new], importance:
  [old,new], rank: [old,new], of: N}`` — the replay source for the matrix
  lane's occupancy-over-time (their PR 3 derives history from THIS).
- EVENT: one ``rank_changed`` per rescore, for the rescored card only —
  emitted after the write is durable, outside the lock. Neighbours shifting
  rank emit nothing (an N-event cascade per drag is the notification storm
  the two-nudger design forbids); consumers read the new order from the
  store.
"""

from __future__ import annotations

from pathlib import Path

from ._store_events import _emit_card_event
from ._store_list import _resolved_store

#: The axis scale (matches the matrix view's AXIS_MIN/AXIS_MAX).
AXIS_MIN = 1
AXIS_MAX = 5

#: Importance dominates urgency in the scalar; the II-over-III guarantee
#: itself comes from the order key below, not from these weights.
W_IMPORTANCE = 2
W_URGENCY = 1

#: The matrix view's inclusive threshold (>=3 is HIGH) — mirrored here so
#: the order key's importance-is-high component agrees with the drawn
#: quadrants. One constant on each side, pinned equal by test.
HIGH_THRESHOLD = 3

#: Terminal states hold axes but never a rank — finished work is not queued.
UNRANKED_STATUSES = frozenset({"done", "cancelled", "failed"})


def _axis_or_raise(name: str, value) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"rescore_task: {name} must be an int, got {value!r}")
    if not (AXIS_MIN <= value <= AXIS_MAX):
        raise ValueError(
            f"rescore_task: {name} must be {AXIS_MIN}..{AXIS_MAX}, got {value}"
        )
    return value


def _scored(task: dict) -> bool:
    u, i = task.get("urgency"), task.get("importance")
    return (
        isinstance(u, int)
        and isinstance(i, int)
        and AXIS_MIN <= u <= AXIS_MAX
        and AXIS_MIN <= i <= AXIS_MAX
    )


def _order_key(task: dict):
    """Sort key: II-before-III by construction, then score, then aging.

    Descending on (importance-is-high, score); ascending on scored_at
    (older first — waiting never costs position) and id (determinism).
    """
    importance = task["importance"]
    score = W_IMPORTANCE * importance + W_URGENCY * task["urgency"]
    return (
        -(importance >= HIGH_THRESHOLD),
        -score,
        str(task.get("scored_at") or "9999"),
        str(task.get("id") or ""),
    )


def recompute_ranks(tasks: list[dict]) -> int:
    """Assign ``rank`` 1..N over scored, non-terminal cards; strip the rest.

    Mutates in place; returns N. Pure over its input (no I/O) so the
    engine's order is testable without a store.
    """
    ranked = [
        t for t in tasks if _scored(t) and t.get("status") not in UNRANKED_STATUSES
    ]
    ranked.sort(key=_order_key)
    for position, task in enumerate(ranked, start=1):
        task["rank"] = position
    # Identity, not equality: `in ranked` would compare dict CONTENTS, and
    # two identical-content cards would alias — stripping a rank from the
    # wrong row. id() names the exact objects ranked above.
    ranked_ids = {id(t) for t in ranked}
    for task in tasks:
        if id(task) not in ranked_ids and "rank" in task:
            del task["rank"]
    return len(ranked)


def rescore_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    *,
    urgency: int,
    importance: int,
    by: str | None = None,
    entry_points=None,
) -> dict:
    """Set one card's axes and recompute the whole rank order — one write.

    Returns ``{"task": <card copy>, "rank": r, "of": N}`` where ``r`` is the
    card's new rank (``None`` when the card is terminal) and ``N`` the
    scored-set size.
    """
    from . import _model, _task
    from ._store import (
        TaskNotFoundError,
        _default_agent,
        _read_write_doc,
        _utc_now_iso,
    )

    tasks_path = _resolved_store(store)
    if not task_id:
        raise ValueError("rescore_task: 'task_id' is required")
    new_u = _axis_or_raise("urgency", urgency)
    new_i = _axis_or_raise("importance", importance)
    actor = _default_agent(by)

    with _model._store_lock(tasks_path):
        doc, tasks = _read_write_doc(tasks_path)
        # See `_task._is_tombstoned`: a deleted card's row is retained
        # forever but must behave as ABSENT here.
        target = _task._find_live_task(tasks, task_id)
        if target is None:
            raise TaskNotFoundError(f"task id {task_id!r} not found in {tasks_path}")

        now = _utc_now_iso()
        old_u = target.get("urgency")
        old_i = target.get("importance")
        old_r = target.get("rank")
        target["urgency"] = new_u
        target["importance"] = new_i
        # Queue-entry proxy for the aging tie-break: stamped once, on the
        # FIRST scoring — a later re-drag must not reset seniority.
        target.setdefault("scored_at", now)
        # Activity + audit land ONLY on the rescored card (see module doc).
        target["last_activity"] = now

        of = recompute_ranks(tasks)
        new_r = target.get("rank")

        target.setdefault("comments", []).append(
            {
                "author": actor,
                "ts": now,
                "text": (
                    f"rescore: urgency {old_u}->{new_u}, "
                    f"importance {old_i}->{new_i}, "
                    f"rank {old_r}->{new_r} (of {of})"
                ),
                "kind": "rescore",
                "rescore": {
                    "urgency": [old_u, new_u],
                    "importance": [old_i, new_i],
                    "rank": [old_r, new_r],
                    "of": of,
                },
            }
        )
        _model._save_doc_unlocked(doc, tasks_path, tasks=tasks)
        result_task = dict(target)

    _emit_card_event(
        "rank_changed",
        task_id,
        actor=actor,
        ts=now,
        extra={
            "urgency": [old_u, new_u],
            "importance": [old_i, new_i],
            "rank": [old_r, new_r],
            "of": of,
        },
        store=tasks_path,
        entry_points=entry_points,
    )
    return {"task": result_task, "rank": new_r, "of": of}


__all__ = [
    "AXIS_MAX",
    "AXIS_MIN",
    "HIGH_THRESHOLD",
    "UNRANKED_STATUSES",
    "recompute_ranks",
    "rescore_task",
]

# EOF
