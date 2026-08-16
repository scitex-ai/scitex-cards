#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Throughput aggregator — single source of truth for stats / WIP / notify.

Shared by:
  * ``scitex-todo print-stats [--by ...] [--since ...] [--notify]`` (CLI/MCP)
  * ``scitex-todo sync-github`` (permanent version of the lead's
    one-time GitHub → board sync)
  * ``_store.add_task``'s WIP-validation gate (env-bounded throttle
    that warns / refuses based on the owning agent's open task count)

Lead-approved spec a2a:
  * ``dbb65f451d6945dfad201d2b46e3d11e`` (2026-06-12) — initial spec
  * ``6f24a75208864fe2a81b573cc9c2754f`` (2026-06-12) — ``--notify``
    per-agent push via ``sac agents send``
  * ``5263c8d9585c4e26b15b7ca3760215cf`` (2026-06-12) — ``--notify``
    body must include CONTENT (per-task lines, not just counts)
  * ``02b71bd0900c4893983137bd23ddf558`` + ``130cc5ac7f7c4dc6a3b02012d548d21b``
    (2026-06-12) — RUNNABLE / BLOCKED dependency gating per line;
    unknown-id deps are defensive BLOCKED.

The matching semantics for RUNNABLE / BLOCKED mirror
``_skills/scitex-todo/40_task-harvest.md``.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass, field

DONE_STATUSES = frozenset({"done"})
# Closed/terminal states — a card here is NOT open and does NOT count as
# backlog. ``cancelled`` (GitHub "closed as not planned") joins done/failed
# so a cancelled card drops out of ``open_count`` exactly like ``done``
# (see the open-count gate in ``aggregate``).
#
# ``deferred`` is NOT terminal (operator ruling, 2026-07-10: "deferred は終了
# ではない"). A deferred card is OPEN — consciously not being worked right now,
# but still carried. It was wrongly listed here because ``deferred`` had been
# overloaded as the close status (``scitex-todo close`` and the board's close
# button both wrote status=deferred, since no ``closed`` value existed). That
# overload made 354 open cards silently vanish from every active view.
# Closing now writes ``cancelled``; ``deferred`` means "not now", and shows.
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})

# OPEN vs WIP are two different questions, and conflating them is what jammed
# the add gate shut (incident 2026-07-10; sac refused at "88 open" while its
# board digest showed 2).
#
#   OPEN  = "still on my plate, show it to me" — everything not terminal, minus
#           goal-tier umbrellas (which accumulate by design). A ``deferred``
#           card IS open: consciously not-now, still carried.
#   WIP   = "work actually in flight, consuming capacity" — ``in_progress``
#           only. A ``blocked`` card is not progressing; a ``deferred`` card is
#           explicitly not being worked. Charging either against an agent's WIP
#           punishes it for the honesty of saying so.
#
# Both predicates now derive from TERMINAL_STATUSES, so a future status value
# lands in the right bucket by construction instead of by a second hand-edit.
OPEN_EXCLUDED_STATUSES = TERMINAL_STATUSES | frozenset({"goal"})
WIP_STATUSES = frozenset({"in_progress"})

# Deprecated alias. The name says "WIP" but every caller used it for OPEN, and
# it excluded only {done, goal} — so ``failed`` and ``cancelled`` cards kept
# nagging in digests and kept consuming gate budget forever. Kept as an alias
# (its real, always-intended meaning) so out-of-tree importers do not break.
WIP_EXCLUDED_STATUSES = OPEN_EXCLUDED_STATUSES

# ``--notify`` truncation constants. The first 10 RUNNABLE-first tasks
# always render; remainder collapses to a "+ N more" line. Recent-done
# section is uncapped (a chatty hour is data; not noise).
NOTIFY_OPEN_CAP = 10
TITLE_TRUNC = 60
SHORT_ID_TRUNC = 24

# WIP-validation thresholds (env-bounded, lead spec
# ``d99b8de6839d46e586e4ee692f43c1d9``).
ENV_WIP_LIMIT = "SCITEX_CARDS_WIP_LIMIT"
DEFAULT_WIP_LIMIT = 20

ENV_STALE_HOURS = "SCITEX_CARDS_STALE_HOURS"
DEFAULT_STALE_HOURS = 24


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _parse_iso(ts: str | None) -> _dt.datetime | None:
    """Lenient ISO-8601 parser — returns None on missing / unparseable.

    Always returns a **UTC-aware** ``datetime``. Strings without an explicit
    timezone (e.g. ``"2026-06-08T00:42:30"``) are treated as UTC — the
    canonical assumption for stored timestamps. Without this coercion
    a single naive ``last_activity`` field anywhere in the store makes the
    subsequent ``_now_utc() - parsed`` subtraction raise
    ``TypeError: can't subtract offset-naive and offset-aware datetimes``
    and kills the entire ``--notify`` / ``--nudge-quiet`` loop before any
    POST happens (the cron then dies silently every tick — proj-scitex-todo
    P3a(c) pilot, 2026-06-13).
    """
    if not ts:
        return None
    try:
        # Accept both 'Z' suffix and explicit offset.
        s = ts.replace("Z", "+00:00")
        parsed = _dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.timezone.utc)


def _days_since(ts: str | None, *, now: _dt.datetime | None = None) -> int | None:
    """Whole-day count since ``ts``. None when ts is missing/invalid."""
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    cur = now or _now_utc()
    delta = cur - parsed
    return max(0, delta.days)


def _hours_since(ts: str | None, *, now: _dt.datetime | None = None) -> float | None:
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    cur = now or _now_utc()
    return (cur - parsed).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Per-group aggregation (stats CLI)
# ---------------------------------------------------------------------------


@dataclass
class GroupStats:
    """Throughput row emitted by :func:`aggregate`."""

    name: str
    created: int = 0
    completed: int = 0
    open_count: int = 0
    stale_count: int = 0  # in_progress with last_activity older than threshold
    velocity_per_day: float = 0.0  # completed / window_days

    @property
    def delta(self) -> int:
        return self.created - self.completed

    @property
    def ratio(self) -> float:
        """Completion ratio (0.0 – 1.0). 0.0 when nothing created."""
        return (self.completed / self.created) if self.created else 0.0


def aggregate(
    tasks: list[dict],
    *,
    by: str = "agent",
    since: str | None = None,
    now: _dt.datetime | None = None,
    stale_hours: float | None = None,
) -> list[GroupStats]:
    """Group ``tasks`` by ``by`` field (one of ``agent`` / ``project`` /
    ``host``) and return per-group throughput stats.

    ``since`` is an ISO-8601 date (YYYY-MM-DD) used to:
      * scope ``created`` to tasks whose ``created_at >= since``
      * scope ``completed`` to tasks whose ``last_activity >= since``
        AND ``status == "done"``
      * compute ``velocity_per_day = completed / max(1, since→now days)``

    ``stale_hours`` (default from env or :data:`DEFAULT_STALE_HOURS`)
    flags ``status == in_progress`` rows whose ``last_activity`` is
    older than the threshold; counted as ``stale_count``.
    """
    if by not in ("agent", "project", "host"):
        raise ValueError(f"unknown grouping axis: {by!r}")
    if stale_hours is None:
        stale_hours = float(os.environ.get(ENV_STALE_HOURS, DEFAULT_STALE_HOURS))
    cur = now or _now_utc()
    since_dt = _parse_iso(since) if since else None
    window_days = max(1, (cur - since_dt).days) if since_dt else 1

    groups: dict[str, GroupStats] = {}

    def _g(name: str) -> GroupStats:
        # Empty group key surfaces as "(unassigned)" so the row exists,
        # not silently dropped — operator's directive is "see the gap."
        key = name or "(unassigned)"
        return groups.setdefault(key, GroupStats(name=key))

    for t in tasks:
        # Goal-tier umbrellas don't participate in throughput accounting.
        if t.get("status") == "goal":
            continue
        group_value = t.get(by)
        g = _g(group_value)
        created_at = t.get("created_at")
        last_activity = t.get("last_activity")
        status = t.get("status")

        in_window = True
        if since_dt:
            ct = _parse_iso(created_at)
            in_window = ct is not None and ct >= since_dt
        if in_window:
            g.created += 1
            if status == "done":
                lt = _parse_iso(last_activity)
                if since_dt is None or (lt and lt >= since_dt):
                    g.completed += 1

        if status not in OPEN_EXCLUDED_STATUSES:
            g.open_count += 1
            if status == "in_progress":
                age_h = _hours_since(last_activity, now=cur)
                if age_h is None or age_h > stale_hours:
                    g.stale_count += 1

    for g in groups.values():
        g.velocity_per_day = g.completed / window_days

    return sorted(groups.values(), key=lambda r: (-r.completed, r.name))


# ---------------------------------------------------------------------------
# WIP-validation gate (write side)
# ---------------------------------------------------------------------------


@dataclass
class WipReport:
    agent: str
    wip_count: int
    open_count: int
    limit: int

    @property
    def is_warn(self) -> bool:
        return self.wip_count >= self.limit

    @property
    def is_refuse(self) -> bool:
        return self.wip_count >= (2 * self.limit)


def count_open_for_agent(tasks: list[dict], agent: str) -> int:
    """Tasks owned by ``agent`` (``agent`` field) that are still open.

    "Open" = ``status`` not in :data:`OPEN_EXCLUDED_STATUSES` — i.e. not
    terminal, and not a ``goal`` umbrella. ``deferred`` counts as open
    (operator ruling 2026-07-10: deferred is not an ending).
    """
    if not agent:
        return 0
    return sum(
        1
        for t in tasks
        if t.get("agent") == agent and t.get("status") not in OPEN_EXCLUDED_STATUSES
    )


def count_wip_for_agent(tasks: list[dict], agent: str) -> int:
    """Tasks owned by ``agent`` that are actively in flight.

    This — not :func:`count_open_for_agent` — is what the add gate bounds.
    A backlog of 300 deferred cards costs an agent nothing; three
    simultaneous ``in_progress`` cards cost it its attention.
    """
    if not agent:
        return 0
    return sum(
        1 for t in tasks if t.get("agent") == agent and t.get("status") in WIP_STATUSES
    )


def evaluate_wip(tasks: list[dict], agent: str | None) -> WipReport | None:
    """Return a WipReport when ``agent`` is set; None when no agent
    attribution (the gate is opt-in, not retroactive)."""
    if not agent:
        return None
    limit = int(os.environ.get(ENV_WIP_LIMIT, DEFAULT_WIP_LIMIT))
    return WipReport(
        agent=agent,
        wip_count=count_wip_for_agent(tasks, agent),
        open_count=count_open_for_agent(tasks, agent),
        limit=limit,
    )


# ---------------------------------------------------------------------------
# RUNNABLE / BLOCKED classifier (notify body)
# ---------------------------------------------------------------------------


@dataclass
class GateInfo:
    """Per-task RUNNABLE/BLOCKED decision per lead spec
    ``02b71bd0900c4893983137bd23ddf558`` + ``130cc5ac7f7c4dc6a3b02012d548d21b``."""

    label: str  # 'RUNNABLE' or 'BLOCKED'
    reason: str = ""  # blocker name OR first blocking dep id, "" when RUNNABLE


def classify(task: dict, by_id: dict[str, dict]) -> GateInfo:
    """Decide the gate label for ``task`` against the store map ``by_id``.

    Rules (in order):
      1. ``status == "blocked"`` → ``BLOCKED(<blocker>)``. The closed-enum
         ``blocker`` field is the operator-facing reason; empty / null
         falls back to ``BLOCKED`` with no parenthetical.
      2. Any ``depends_on`` entry that ISN'T in ``by_id`` → defensive
         ``BLOCKED(→ unknown:<id>)`` (lead-confirmed ``130cc5ac``: don't
         offer a possibly-non-existent task as RUNNABLE).
      3. Any ``depends_on`` entry whose referent is NOT in
         :data:`DONE_STATUSES` → ``BLOCKED(→ <first-blocking-dep-id>)``.
      4. Otherwise → ``RUNNABLE``.
    """
    if task.get("status") == "blocked":
        blocker = task.get("blocker") or ""
        return GateInfo(label="BLOCKED", reason=blocker)

    for dep_id in task.get("depends_on") or []:
        target = by_id.get(dep_id)
        if target is None:
            return GateInfo(label="BLOCKED", reason=f"→ unknown:{dep_id}")
        if target.get("status") not in DONE_STATUSES:
            return GateInfo(label="BLOCKED", reason=f"→ {dep_id}")

    return GateInfo(label="RUNNABLE")


# ---------------------------------------------------------------------------
# --notify body assembly
# ---------------------------------------------------------------------------


def _short_id(tid: str) -> str:
    if len(tid) <= SHORT_ID_TRUNC:
        return tid
    return tid[: SHORT_ID_TRUNC - 1] + "…"


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def build_notify_body(
    agent: str,
    tasks: list[dict],
    *,
    since: str | None = None,
    now: _dt.datetime | None = None,
    stale_hours: float | None = None,
) -> str:
    """Compose the per-agent notify body per lead spec.

    Layout:

      HEADER  agent · open N · stale ⚠M · done K (since X) · ratio R%
      ----    (separator)
      OPEN    each line `<short-id> · <title 60c> · <status> · <Nd> · <gate>`
              RUNNABLE first, then BLOCKED (depends_on order, then
              status=blocked). ⚠ on stale in_progress. Cap at 10 + "+ N more".
      ----
      RECENT  each line `<short-id> · <title 60c> · done <Nd ago>`

    Returned as a single newline-joined string (no trailing newline).
    Callers prepend / wrap as they wish.
    """
    cur = now or _now_utc()
    if stale_hours is None:
        stale_hours = float(os.environ.get(ENV_STALE_HOURS, DEFAULT_STALE_HOURS))

    mine = [t for t in tasks if t.get("agent") == agent]
    by_id = {t["id"]: t for t in tasks if t.get("id")}

    open_tasks = [t for t in mine if t.get("status") not in OPEN_EXCLUDED_STATUSES]
    done_tasks = [t for t in mine if t.get("status") == "done"]

    # Window filter
    since_dt = _parse_iso(since) if since else None
    if since_dt:
        done_window = [
            t
            for t in done_tasks
            if (
                _parse_iso(t.get("last_activity"))
                or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)
            )
            >= since_dt
        ]
    else:
        done_window = done_tasks

    # Stale = in_progress AND last_activity is None or older than threshold.
    def _is_stale(t: dict) -> bool:
        if t.get("status") != "in_progress":
            return False
        age = _hours_since(t.get("last_activity"), now=cur)
        return age is None or age > stale_hours

    stale_count = sum(1 for t in open_tasks if _is_stale(t))
    open_n = len(open_tasks)
    done_n = len(done_window)
    ratio = f"{done_n / (open_n + done_n) * 100:.0f}%" if (open_n + done_n) else "—"

    since_phrase = f" since {since}" if since else " all-time"
    header = (
        f"{agent} · open {open_n} · stale ⚠{stale_count}"
        f" · done {done_n}{since_phrase} · ratio {ratio}"
    )

    # Classify + sort open tasks: RUNNABLE before BLOCKED, then by oldest first.
    classified: list[tuple[GateInfo, dict]] = [
        (classify(t, by_id), t) for t in open_tasks
    ]

    def _sort_key(item):
        gi, t = item
        # RUNNABLE first, then BLOCKED.
        rank = 0 if gi.label == "RUNNABLE" else 1
        # Oldest last_activity first inside each group.
        age = _hours_since(t.get("last_activity"), now=cur)
        # Fallback: created_at, then id (stable order).
        return (rank, -(age or 0.0), t.get("id") or "")

    classified.sort(key=_sort_key)

    lines: list[str] = [header, "—" * 8]

    rendered = 0
    for gi, t in classified:
        if rendered >= NOTIFY_OPEN_CAP:
            remaining = len(classified) - rendered
            if remaining > 0:
                lines.append(f"…+ {remaining} more open")
            break
        short = _short_id(t.get("id") or "")
        ttl = _truncate(t.get("title") or "(untitled)", TITLE_TRUNC)
        status = t.get("status") or "?"
        age_d = _days_since(t.get("last_activity"), now=cur)
        age_s = f"{age_d}d" if age_d is not None else "—"
        gate = gi.label
        if gi.reason:
            gate = f"{gi.label}({gi.reason})"
        mark = "⚠ " if _is_stale(t) else ""
        lines.append(f"{mark}{short} · {ttl} · {status} · {age_s} · {gate}")
        rendered += 1

    if done_window:
        lines.append("—" * 8)
        for t in sorted(
            done_window,
            key=lambda x: _hours_since(x.get("last_activity"), now=cur) or 0.0,
        ):
            short = _short_id(t.get("id") or "")
            ttl = _truncate(t.get("title") or "(untitled)", TITLE_TRUNC)
            age_d = _days_since(t.get("last_activity"), now=cur)
            age_s = f"{age_d}d ago" if age_d is not None else "—"
            lines.append(f"{short} · {ttl} · done {age_s}")

    return "\n".join(lines)


__all__ = [
    "DONE_STATUSES",
    "TERMINAL_STATUSES",
    "WIP_EXCLUDED_STATUSES",
    "OPEN_EXCLUDED_STATUSES",
    "WIP_STATUSES",
    "count_wip_for_agent",
    "NOTIFY_OPEN_CAP",
    "TITLE_TRUNC",
    "SHORT_ID_TRUNC",
    "ENV_WIP_LIMIT",
    "DEFAULT_WIP_LIMIT",
    "ENV_STALE_HOURS",
    "DEFAULT_STALE_HOURS",
    "GroupStats",
    "WipReport",
    "GateInfo",
    "aggregate",
    "count_open_for_agent",
    "evaluate_wip",
    "classify",
    "build_notify_body",
]
