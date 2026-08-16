#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fleet-dashboard Phase 4 — TIMING TELEMETRY (pure compute).

Operator's intent (TG, relayed by lead a2a ``74db4f2d`` + ``10afa799``,
2026-06-14): "record what took how long → self-improvement" — the fleet
should see its own bottlenecks. Phase 4 ships the BACKEND DATA; Phase 5
will render the chart.

Design principles (HARD):

- **fail-loud / no-silent-fallback** — the view layer raises on store
  read failure; this module is a pure function so it can be tested with
  synthetic dicts without any I/O.
- **registry-sourced** — derives durations from the timestamps the task
  store ALREADY carries (``created_at`` + ``_log_meta.started_at`` +
  ``_log_meta.completed_at``). No duplicate state, no new fields.
- **NO hardcoded proper nouns** — agent / project / group names come
  from the task data; this module never enumerates them.

Per-task metrics derived (seconds; ``None`` if either endpoint is
missing):

- ``created_to_started`` — queue wait: how long the card sat before an
  agent claimed it (``_log_meta.started_at`` − ``created_at``).
- ``started_to_done`` — work duration: wall-clock from claim to done
  (``_log_meta.completed_at`` − ``_log_meta.started_at``).
- ``created_to_done`` — end-to-end queue+work
  (``_log_meta.completed_at`` − ``created_at``).

Aggregates emitted (keyed by agent / project / group / "<ungrouped>"):

- ``n_tasks_done`` — how many done tasks contributed to the row.
- ``median_started_to_done_s`` — typical work duration.
- ``p95_started_to_done_s`` — slow-tail work duration.
- ``median_created_to_started_s`` — typical queue wait.

Aggregates draw only from tasks whose ``_log_meta.completed_at`` falls
within ``window_days`` (default 30) — the chart is meant to show the
recent past, not all-time.

# CARD(phase-4.b): a2a-log scraping for agent-turn-level durations
# (the lead's per-message latency). That source is the SAC mesh log,
# not the card store — separate adapter, separate PR.
# CARD(phase-4.b): histograms / CDF arrays for the chart's distribution
# overlay; the floor here is median + p95 only.
# CARD(phase-4.b): p50/p75/p99 percentile knobs — the chart can request
# more once Phase 5 lands.
"""

from __future__ import annotations

import datetime as _dt
import statistics
from typing import Iterable, Optional

#: Default sliding-window in days; matches the operator brief.
_DEFAULT_WINDOW_DAYS: int = 30

#: Sentinel key for tasks with no ``group`` value. Not a content literal
#: — purely a display string surfaced in the aggregate so the chart can
#: render an "ungrouped" row.
_UNGROUPED_KEY: str = "<ungrouped>"


def _parse_iso(ts: Optional[str]) -> Optional[_dt.datetime]:
    """Parse an ISO-8601 string into a tz-aware ``datetime`` or ``None``.

    Mirrors the lenient parser the rest of the fleet handlers use:
    ``"Z"`` becomes ``+00:00``; naive strings get ``UTC`` attached so
    all comparisons stay tz-aware.
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


def _log_meta_ts(task: dict, key: str) -> Optional[str]:
    """Pull ``task['_log_meta'][key]`` defensively. Returns ``None`` if
    ``_log_meta`` is absent or not a dict."""
    meta = task.get("_log_meta")
    if not isinstance(meta, dict):
        return None
    val = meta.get(key)
    if not val:
        return None
    return str(val)


def _delta_seconds(
    end: Optional[_dt.datetime], start: Optional[_dt.datetime]
) -> Optional[float]:
    """Return ``(end - start).total_seconds()`` or ``None`` if either
    endpoint is ``None``. Negative deltas are returned verbatim — the
    chart can decide how to surface clock skew."""
    if end is None or start is None:
        return None
    return (end - start).total_seconds()


def task_durations(task: dict) -> dict:
    """Compute the three per-task duration metrics. Pure function.

    Returns a dict with::

        {"created_to_started": <s | None>,
         "started_to_done":    <s | None>,
         "created_to_done":    <s | None>}

    None whenever either endpoint is missing or unparseable — the
    fail-loud principle applies at the VIEW layer (store read); per-row
    holes here are reported via ``n_tasks_missing_timestamps`` so the
    chart can show "K tasks dropped" without silently lying about the
    sample size.
    """
    created = _parse_iso(task.get("created_at"))
    started = _parse_iso(_log_meta_ts(task, "started_at"))
    done = _parse_iso(_log_meta_ts(task, "completed_at"))
    return {
        "created_to_started": _delta_seconds(started, created),
        "started_to_done": _delta_seconds(done, started),
        "created_to_done": _delta_seconds(done, created),
    }


def _p95(values: list[float]) -> float:
    """95th-percentile via nearest-rank. ``statistics.quantiles`` needs
    at least 2 points; for n=1 we return the single value. Bracket via
    nearest-rank (rather than linear-interpolation) so the result is
    always a value that actually occurred — the operator can scroll the
    board and find the card that produced it."""
    if not values:
        raise ValueError("p95 of empty sequence")
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    # nearest-rank index for p95
    idx = max(0, min(len(ordered) - 1, int(round(0.95 * len(ordered))) - 1))
    return float(ordered[idx])


def _agg_for(rows: list[dict]) -> dict:
    """Aggregate a list of per-task duration dicts into the standard
    median / p95 / queue-median shape. ``rows`` is the list of
    ``task_durations(t)`` results for the bucket's tasks."""
    started_to_done = [
        float(r["started_to_done"])
        for r in rows
        if r.get("started_to_done") is not None
    ]
    created_to_started = [
        float(r["created_to_started"])
        for r in rows
        if r.get("created_to_started") is not None
    ]
    median_work = (
        float(statistics.median(started_to_done)) if started_to_done else None
    )
    p95_work = _p95(started_to_done) if started_to_done else None
    median_queue = (
        float(statistics.median(created_to_started))
        if created_to_started
        else None
    )
    return {
        "n_tasks_done": len(rows),
        "median_started_to_done_s": median_work,
        "p95_started_to_done_s": p95_work,
        "median_created_to_started_s": median_queue,
    }


def _bucket_key(task: dict, dimension: str) -> Optional[str]:
    """Project a task onto its bucket key for the given dimension.

    Returns ``None`` for ``agent`` / ``project`` when the field is
    missing — those rows are simply excluded from the agent / project
    aggregates (we don't fabricate an "<unknown>" key). For ``group``
    a missing value rolls into :data:`_UNGROUPED_KEY` so the chart has
    a single deterministic row for "no group set".
    """
    if dimension == "agent":
        val = task.get("agent") or task.get("assignee")
    elif dimension == "project":
        val = task.get("project")
    elif dimension == "group":
        val = task.get("group")
        if val is None or str(val).strip() == "":
            return _UNGROUPED_KEY
    else:
        return None
    if val is None or str(val).strip() == "":
        return None
    return str(val)


def compute_timing(
    tasks: Iterable[dict],
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    now: Optional[_dt.datetime] = None,
) -> dict:
    """Derive the fleet timing telemetry payload from a raw task list.

    Pure function — no Django, no I/O. The view layer wraps this with
    ``resolve_tasks_path`` + ``load_tasks`` and JSON-serialises the
    result.

    Args:
        tasks: iterable of task dicts (as returned by ``load_tasks``).
        window_days: include only tasks whose
            ``_log_meta.completed_at`` falls within the last N days.
        now: pin "now" for deterministic tests. Defaults to UTC now.

    Returns:
        dict with ``window_days`` / ``window_start`` / ``window_end`` /
        ``per_agent`` / ``per_project`` / ``per_group`` /
        ``n_tasks_in_window`` / ``n_tasks_missing_timestamps``.
    """
    cur = now or _dt.datetime.now(tz=_dt.timezone.utc)
    if window_days <= 0:
        window_days = _DEFAULT_WINDOW_DAYS
    window_start = cur - _dt.timedelta(days=int(window_days))
    window_end = cur

    in_window: list[tuple[dict, dict]] = []
    missing = 0
    for t in tasks:
        completed_raw = _log_meta_ts(t, "completed_at")
        completed = _parse_iso(completed_raw)
        if completed is None:
            # Not a done task (or done but the writer didn't stamp the
            # log_meta) — it doesn't contribute to the window aggregates.
            # We only count "done but incomplete metadata" against the
            # missing counter when the task IS in a done status — see below.
            status = (t.get("status") or "").lower()
            if status == "done" and completed_raw is None:
                # Done card with no timestamp = data hole we want the
                # operator to see, not silently swallow.
                missing += 1
            continue
        if not (window_start <= completed <= window_end):
            continue
        durs = task_durations(t)
        in_window.append((t, durs))

    # Bucket by each dimension and aggregate.
    def _bucket(dim: str) -> dict:
        out: dict[str, list[dict]] = {}
        for t, d in in_window:
            key = _bucket_key(t, dim)
            if key is None:
                continue
            out.setdefault(key, []).append(d)
        return {k: _agg_for(rows) for k, rows in out.items()}

    return {
        "window_days": int(window_days),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "per_agent": _bucket("agent"),
        "per_project": _bucket("project"),
        "per_group": _bucket("group"),
        "n_tasks_in_window": len(in_window),
        "n_tasks_missing_timestamps": missing,
    }


__all__ = [
    "compute_timing",
    "task_durations",
]

# EOF
