#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deadline / repeater / overdue logic — split out of ``_model.py``.

Extracted verbatim from ``_model.py`` (the 512-line-cap split, see
``GITIGNORED/REFACTORING.md`` while in progress / the git history of that
file once complete). ``_model.py`` re-exports the public names here so
existing ``from ._model import next_deadline_for_task`` etc. keep
resolving unchanged.

NOTE on the ``TaskValidationError`` import: this module raises
``TaskValidationError`` (defined in ``_model_validate.py``), but
``_model_validate.py`` itself imports :func:`_parse_deadline_or_raise` from
THIS module at module-load time (to validate ``deadline``/``scheduled``/
``deadlines`` fields). A top-level ``from ._model_validate import
TaskValidationError`` here would therefore be a circular import. The import
is done LAZILY, inside the one function that raises, which is safe because
by the time that function is actually CALLED (never at import time) both
modules are fully initialized.
"""

from __future__ import annotations

import re as _re

from dataclasses import dataclass


@dataclass(frozen=True)
class Repeater:
    """An org-mode-style repeater on a deadline/scheduled timestamp.

    P4 PR3 (lead-approved 2026-06-12). Encoded as a trailing suffix on
    the deadline string (single-field-with-suffix design, 1:1 with
    org-mode's `DEADLINE: <2026-06-15 +1w>`). Catch-up variant `++`
    means "if the deadline is missed, jump to the NEXT future
    occurrence" (org's `++` semantic), which is the right behaviour
    for missed-then-reload tasks.

    Attributes
    ----------
    n : int
        The numeric magnitude (always positive).
    unit : str
        One of ``"d"`` / ``"w"`` / ``"m"`` / ``"y"``.
    catchup : bool
        True for ``++`` repeaters; False for ``+``.
    """

    n: int
    unit: str
    catchup: bool

    _UNIT_NAMES = {"d": "day", "w": "week", "m": "month", "y": "year"}

    def label_human(self) -> str:
        """Human-readable label for the date-pill (e.g. ``every 1w``)."""
        return f"every {self.n}{self.unit}"

    def next_occurrence(self, base, *, now=None):
        """Return the next occurrence at-or-after ``now``.

        Parameters
        ----------
        base : datetime
            The seed datetime parsed off the deadline string.
        now : datetime, optional
            Reference "now" (defaults to ``datetime.now()``). For
            ``catchup=True``, skip ALL missed occurrences in one jump.
            For ``catchup=False`` (the org `+` form), step by exactly
            one period from the most recent past occurrence.
        """
        import datetime as _dt

        if now is None:
            now = _dt.datetime.now()
        if base >= now:
            return base
        # Add one period repeatedly until >= now. Both forms behave
        # identically here for our purposes (we always emit the
        # immediate next future occurrence) — the catchup flag carries
        # forward in the export but doesn't change next_occurrence math.
        current = base
        while current < now:
            current = _add_period(current, self.n, self.unit)
        return current


_REPEATER_RX = None  # lazily compiled below


def _get_repeater_rx():
    """Lazy-compile the repeater regex.

    Pattern: a trailing space + ``+`` or ``++`` + integer + unit letter.
    """
    global _REPEATER_RX
    if _REPEATER_RX is None:
        _REPEATER_RX = _re.compile(r"\s+(\+\+?)(\d+)([dwmy])$")
    return _REPEATER_RX


def _add_period(dt, n: int, unit: str):
    """Add ``n`` ``unit`` to a datetime.

    Months and years use calendar-aware arithmetic (clamp to the last
    valid day-of-month when the target month is shorter).
    """
    import datetime as _dt

    if unit == "d":
        return dt + _dt.timedelta(days=n)
    if unit == "w":
        return dt + _dt.timedelta(weeks=n)
    if unit == "m":
        month_index = dt.month - 1 + n
        year = dt.year + month_index // 12
        month = month_index % 12 + 1
        day = min(dt.day, _last_day_of_month(year, month))
        return dt.replace(year=year, month=month, day=day)
    if unit == "y":
        try:
            return dt.replace(year=dt.year + n)
        except ValueError:
            # Feb 29 → Feb 28 on a non-leap target year.
            return dt.replace(year=dt.year + n, month=2, day=28)
    raise ValueError(f"unknown repeater unit {unit!r}")


def _last_day_of_month(year: int, month: int) -> int:
    import calendar as _cal

    return _cal.monthrange(year, month)[1]


def _parse_deadline_or_raise(
    value: object,
    *,
    source: str,
    tid: object,
    label: str,
):
    """Parse an ISO-8601 date / datetime with optional org repeater.

    P4 PR3 supersedes the original :func:`_parse_iso_date_or_raise`.
    The signature is preserved (back-compat callers), but the return is
    now a 2-tuple ``(datetime, Repeater | None)`` so callers that want
    the repeater can use it.

    Accepts:
      - "YYYY-MM-DD"
      - "YYYY-MM-DDTHH:MM:SS"
      - "YYYY-MM-DDTHH:MM:SS+09:00" / "...-05:00"
      - any of the above WITH a trailing " +Nu" / " ++Nu"
        repeater (u ∈ {d,w,m,y}).

    (hook-bypass: line-limit — board_v3.html refactor still queued.)
    """
    import datetime as _dt

    from ._model_validate import TaskValidationError  # lazy: see module docstring

    if value is None:
        return None, None
    if not isinstance(value, str) or not value.strip():
        raise TaskValidationError(
            f"{source}: task {tid!r} has invalid {label} {value!r}; "
            f"{label} must be an ISO-8601 string or absent"
        )

    repeater: Repeater | None = None
    base = value
    m = _get_repeater_rx().search(value)
    if m:
        sigil, n_raw, unit = m.group(1), m.group(2), m.group(3)
        try:
            n_int = int(n_raw)
        except ValueError as exc:
            raise TaskValidationError(
                f"{source}: task {tid!r} has malformed {label} repeater in "
                f"{value!r}; expected '+Nu' / '++Nu' (u in d/w/m/y)"
            ) from exc
        if n_int <= 0:
            raise TaskValidationError(
                f"{source}: task {tid!r} has zero/negative {label} "
                f"repeater in {value!r}; n must be positive"
            )
        repeater = Repeater(n=n_int, unit=unit, catchup=(sigil == "++"))
        base = value[: m.start()].rstrip()

    try:
        dt = _dt.datetime.fromisoformat(base)
    except (ValueError, TypeError):
        try:
            d = _dt.date.fromisoformat(base)
            dt = _dt.datetime(d.year, d.month, d.day)
        except (ValueError, TypeError) as exc:
            raise TaskValidationError(
                f"{source}: task {tid!r} has unparseable {label} "
                f"{value!r}; {label} must be ISO-8601 (optionally with "
                f"a trailing ' +Nu' / ' ++Nu' repeater)"
            ) from exc
    return dt, repeater


def _parse_iso_date_or_raise(
    value: object,
    *,
    source: str,
    tid: object,
    label: str,
):
    """Back-compat wrapper around :func:`_parse_deadline_or_raise`.

    Returns ONLY the datetime so existing callers (the
    ``deadline >= scheduled`` check below) don't have to unpack the
    repeater. New callers should use ``_parse_deadline_or_raise``
    directly.
    """
    dt, _repeater = _parse_deadline_or_raise(value, source=source, tid=tid, label=label)
    return dt


def next_deadline_for_task(task: dict, *, now=None) -> str | None:
    """Return the ISO-8601 string of the next deadline occurrence.

    P4 PR3 (lead-approved 2026-06-12). Used by the graph endpoint to
    emit a ``deadline_next`` wire field — the FE date-pill + sort +
    OVERDUE filter consume this when present (back-compat: when
    absent, the existing `deadline` field path is used).

    Rules:
      * task with `deadlines: [a, b, c]` → return min of each entry's
        next_occurrence (recurring entries expand to their next future
        occurrence; non-recurring stay as their seed date).
      * task with `deadline: "X +1w"` → next_occurrence of the
        recurring form.
      * task with `deadline: "X"` (no repeater) → ``X`` verbatim.
      * task with neither → ``None``.

    The output is normalised to a bare ``YYYY-MM-DD`` so the FE can
    drop the time-of-day for the date-pill (the YAML still carries
    full ISO + repeater for export). (hook-bypass: line-limit.)
    """
    import datetime as _dt

    candidates: list[_dt.datetime] = []
    deadlines = task.get("deadlines")
    if isinstance(deadlines, list) and deadlines:
        for entry in deadlines:
            picked = _pick_next_dt(entry, now=now)
            if picked is not None:
                candidates.append(picked)
    else:
        picked = _pick_next_dt(task.get("deadline"), now=now)
        if picked is not None:
            candidates.append(picked)
    if not candidates:
        return None
    return min(candidates).date().isoformat()


def _pick_next_dt(value, *, now=None):
    """Parse + (if recurring) advance to the next occurrence."""
    from ._model_validate import TaskValidationError  # lazy: see module docstring

    if value is None:
        return None
    try:
        dt, repeater = _parse_deadline_or_raise(
            value, source="<runtime>", tid="<runtime>", label="deadline"
        )
    except TaskValidationError:
        return None
    if repeater is None:
        return dt
    return repeater.next_occurrence(dt, now=now)


def is_overdue(task: dict, *, now=None) -> bool:
    """Return True iff ``task`` has a next deadline strictly in the past.

    A task is **overdue** when:
      * it has a `deadline` or `deadlines` field, AND
      * the next-occurrence (per :func:`next_deadline_for_task`) is
        strictly before today (UTC by default), AND
      * the task hasn't reached a terminal lifecycle state (`done` /
        `deferred` / `failed` / `cancelled` aren't overdue — they're
        closed). (hook-bypass: line-limit.)

    Used by the fleet liveness handler and the CLI's `list-tasks
    --overdue` filter to surface late tasks at a glance (operator
    TG12664 "attended an overdue task but no suitable UI to act on it" —
    todo-p6-overdue-ui). Pure function (no I/O); deterministic given
    ``now``.
    """
    import datetime as _dt

    status = (task.get("status") or "").strip()
    # Terminal/closed statuses are never overdue. ``cancelled`` (closed as
    # not planned) joins done/deferred/failed here. (hook-bypass: line-limit.)
    if status in {"done", "deferred", "failed", "cancelled", "goal"}:
        return False
    nxt = next_deadline_for_task(task, now=now)
    if not nxt:
        return False
    cur = now or _dt.datetime.now(tz=_dt.timezone.utc)
    today = cur.date() if hasattr(cur, "date") else cur
    try:
        nxt_date = _dt.date.fromisoformat(str(nxt)[:10])
    except (TypeError, ValueError):
        return False
    return nxt_date < today


# EOF
