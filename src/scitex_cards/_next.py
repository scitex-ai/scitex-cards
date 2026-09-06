#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single canonical "what to pick up next" predicate for fleet agents.

Used by the ``scitex-cards next [--mine|--assignee X]`` CLI verb to
return the top runnable task for an agent. One source of truth so
every fleet agent applies the SAME filter + sort rules — no risk of
drift between agents.

Filter (RUNNABLE = picks up cleanly):
  * ``agent`` matches the requested name (or any when ``None``)
  * ``status`` ∈ {``pending``, ``in_progress``}
  * ``blocker`` is None (a blocked row is NOT runnable; a different
    agent / the operator needs to clear it first)

Sort (lowest = picked first):
  1. ``priority`` ASC, with ``None`` ranking LAST
     (explicit priorities beat unrated ones).
  2. ``last_activity`` DESC — recency favours tasks the agent was
     already working on.
  3. ``created_at`` DESC — newer requests beat older ones at
     equal priority.
  4. ``id`` ASC — deterministic tiebreak.

The ``next_task`` function is the public API for the CLI and for any
agent harness that wants to implement the self-consumption loop in
Python directly (skip the CLI subprocess).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


# Status values that are eligible for "next pickup". Imported, not restated:
# this was a hand-copied duplicate of _runnable's set, and when ``pending`` was
# abolished only one of the two copies got updated — leaving `next` and
# `runnable` silently disagreeing about what an agent may pick up.
from ._runnable import RUNNABLE_STATUSES  # noqa: E402  (SSOT, not a restatement)


@dataclass(frozen=True)
class NextPick:
    """Result of :func:`next_task` — the picked dict + diagnostic stats.

    Attributes
    ----------
    task : dict | None
        The picked task (verbatim from the store), or ``None`` when
        the agent's runnable backlog is empty.
    candidate_count : int
        How many tasks matched the runnable filter (before the sort
        + slice). Useful for a "queue depth: N" log line.
    """

    task: Optional[dict]
    candidate_count: int


def next_task(
    tasks: Iterable[dict],
    *,
    assignee: Optional[str] = None,
    project: Optional[str] = None,
) -> NextPick:
    """Return the next runnable task for an agent.

    Parameters
    ----------
    tasks : iterable of dict
        The full task list (e.g. ``load_tasks(path)``).
    assignee : str, optional
        Agent name to filter on. When ``None``, ALL agents'
        tasks compete (rare; only useful for the lead-side cron).
    project : str, optional
        Project name to scope the query (e.g. ``"scitex-cards"``).
        ``None`` = no project filter.

    Returns
    -------
    NextPick
        The picked task + candidate count.
    """
    candidates = [t for t in tasks if _is_runnable(t, assignee, project)]
    if not candidates:
        return NextPick(task=None, candidate_count=0)
    candidates.sort(key=_sort_key)
    return NextPick(task=candidates[0], candidate_count=len(candidates))


def _is_runnable(
    task: dict,
    assignee: Optional[str],
    project: Optional[str],
) -> bool:
    """Inclusive filter: True iff the task is "an agent should pick this"."""
    if not isinstance(task, dict):
        return False
    status = task.get("status")
    if status not in RUNNABLE_STATUSES:
        return False
    if task.get("blocker"):
        return False
    if assignee is not None:
        # Accept either `agent` (operator-co-designed, ADR-0007) OR the
        # legacy `assignee` field. agents wired with SCITEX_CARDS_AGENT_ID
        # will match either spelling so older tasks aren't orphaned.
        agent_match = task.get("agent") == assignee
        legacy_match = task.get("assignee") == assignee
        if not (agent_match or legacy_match):
            return False
    if project is not None and task.get("project") != project:
        return False
    return True


def _sort_key(task: dict) -> tuple:
    """Sort key tuple: priority asc, last_activity desc, created_at desc, id asc."""
    priority = task.get("priority")
    # None ranks LAST: use a sentinel that's larger than any plausible
    # integer priority (matches the existing _priority_key convention).
    priority_rank = 10_000_000 if priority is None else int(priority)
    # last_activity / created_at: ISO-8601 strings sort lexically; we
    # want NEWEST first, so negate by mapping to the "lexically larger"
    # side via a wrapping comparator. Use the negated form: an empty
    # string sorts BEFORE any real timestamp, so we use a sentinel
    # before the negate.
    last_activity = task.get("last_activity") or ""
    created_at = task.get("created_at") or ""
    # Python sorts tuples element-by-element; to make DESC for strings
    # we negate via the helper _NegStr — Python doesn't have built-in
    # reverse-string ordering in a single key.
    return (
        priority_rank,
        _NegStr(last_activity),
        _NegStr(created_at),
        str(task.get("id") or ""),
    )


@dataclass(frozen=True)
class _NegStr:
    """Wraps a string to invert its lexical comparison (for DESC sorts).

    Used in :func:`_sort_key` to reverse the ordering of
    ``last_activity`` + ``created_at`` so newer timestamps come first.
    """

    value: str

    def __lt__(self, other: "_NegStr") -> bool:  # type: ignore[override]
        return self.value > other.value

    def __eq__(self, other: object) -> bool:  # type: ignore[override]
        return isinstance(other, _NegStr) and self.value == other.value

    def __hash__(self) -> int:
        return hash((self.__class__, self.value))


# EOF
