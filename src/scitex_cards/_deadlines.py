#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deadlines, repeaters, and the ``overdue`` predicate.

TIMEZONE RULE (the 2026-07-12 blank-board incident): a bare-date deadline
parses NAIVE while board/fleet callers pass a tz-AWARE UTC ``now``. Comparing
them raises ``TypeError: can't compare offset-naive and offset-aware
datetimes`` — which 500'd ``/graph`` and rendered the board blank. Every
datetime that can MEET another goes through :func:`_as_aware_utc` first.

It normalises for the COMPARISON only. Callers get back exactly the kind of
datetime they always did — silently switching the return type to tz-aware
would be a contract change dressed up as a bug fix.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from ._store_verify import _verify_dumped_tmp  # hook-bypass: line-limit
from ._task import TaskValidationError

_LOG = logging.getLogger(__name__)


def _as_aware_utc(dt):
    """Return ``dt`` as tz-AWARE, reading a naive value as UTC.

    The single normalisation point for this module. Used ONLY to make two
    datetimes comparable — it never changes what a caller receives back, which
    would be a silent contract change for every existing consumer.
    """
    import datetime as _dt

    if not isinstance(dt, _dt.datetime):
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_dt.timezone.utc)
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

    The repeater rolls forward for real — but the roll is only ever
    OBSERVED, never announced. Every consumer of this function is a
    VIEW (the graph endpoint, the ``overdue`` filter, the CLI list).
    NO notification path calls it: a recurring deadline coming due
    does NOT nudge the owner.

    And since the roll always lands in the FUTURE, a recurring task is
    never :func:`is_overdue` either — so the ``overdue`` filter never
    sees it. Rolling forward is what makes the date-pill correct AND
    what makes the overdue filter blind to it; both follow from this
    one function. (hook-bypass: line-limit.)
    """
    nxt_dt, _has_time = _next_deadline_dt_typed(task, now=now)
    if nxt_dt is None:
        return None
    # Date-flatten for the FE date-pill / sort / org export. This is the ONE
    # place the time-of-day is dropped; is_overdue takes the FULL datetime from
    # the same helper, so a timed deadline still alarms at its timestamp.
    return nxt_dt.date().isoformat()


def _next_deadline_dt_typed(task: dict, *, now=None):
    """The datetime-carrying core shared by the date-pill and the alarm.

    Returns ``(datetime | None, has_time)`` for the SOONEST deadline occurrence.
    :func:`next_deadline_for_task` date-flattens this for the FE; :func:`is_overdue`
    keeps the full timestamp so a timed deadline (``...T09:00``) is overdue the
    moment it passes, not only once its whole day has. ``has_time`` is the winning
    (soonest) candidate's — whether that source string carried a time-of-day.

    THE SECOND INSTANCE of the blank-board bug lives here: ``min()`` over a mix
    of naive and aware datetimes raises the SAME TypeError that 500'd ``/graph``
    (2026-07-12). We key ``min`` on ``_as_aware_utc`` copies; the datetimes
    handed back are untouched, preserving the naive-in / naive-out contract.
    """
    candidates: list[tuple] = []  # (datetime, has_time)
    deadlines = task.get("deadlines")
    if isinstance(deadlines, list) and deadlines:
        for entry in deadlines:
            picked, has_time = _pick_next_dt_typed(entry, now=now)
            if picked is not None:
                candidates.append((picked, has_time))
    else:
        picked, has_time = _pick_next_dt_typed(task.get("deadline"), now=now)
        if picked is not None:
            candidates.append((picked, has_time))
    if not candidates:
        return None, False
    return min(candidates, key=lambda c: _as_aware_utc(c[0]))


def _pick_next_dt_typed(value, *, now=None):
    """Parse + (if recurring) advance, returning ``(datetime | None, has_time)``.

    ``has_time`` reports whether ``value`` carried a time-of-day (an explicit
    ``T``/space time part) versus a bare ``YYYY-MM-DD`` — it drives the overdue
    granularity (see :func:`is_overdue`).

    A stored deadline that will not parse is LOGGED (loud, greppable, names the
    value) and treated as absent. The write path validates deadlines with this
    same parser, so a parse failure at read time signals data corruption or a
    schema skew worth a warning — but a single bad card must NOT crash the
    fleet-wide overdue scan, so we log rather than raise (cf. the blank-board).
    """
    if value is None:
        return None, False
    try:
        dt, repeater = _parse_deadline_or_raise(
            value, source="<runtime>", tid="<runtime>", label="deadline"
        )
    except TaskValidationError:
        _LOG.warning(
            "is_overdue: ignoring unparseable stored deadline %r — the overdue "
            "filter cannot evaluate it (fix the card's deadline field)",
            value,
        )
        return None, False
    has_time = _has_time_component(value)
    if repeater is None:
        return dt, has_time
    return repeater.next_occurrence(dt, now=now), has_time


def _has_time_component(value) -> bool:
    """True iff the deadline STRING carries a time-of-day.

    A bare date is ``YYYY-MM-DD`` (no ``T``, no space). Any explicit time part —
    ``2026-07-23T09:00``, ``2026-07-23 09:00``, or one with an offset — has one.
    The repeater suffix (`` +1w`` / `` ++2d``) is stripped first, so
    ``2026-07-01 +1w`` reads as date-only. Drives is_overdue's granularity.
    """
    if not isinstance(value, str):
        return False
    base = value
    m = _get_repeater_rx().search(value)
    if m:
        base = value[: m.start()]
    base = base.strip()
    return "T" in base or " " in base


def _pick_next_dt(value, *, now=None):
    """Back-compat shim: the datetime-only half of :func:`_pick_next_dt_typed`.

    Retained because ``_model`` re-exports it; new callers use the typed form.
    """
    dt, _has_time = _pick_next_dt_typed(value, now=now)
    return dt


def is_overdue(task: dict, *, now=None) -> bool:
    """Return True iff ``task`` has a next deadline strictly in the past.

    A task is **overdue** when it has a `deadline`/`deadlines`, is NOT in a
    terminal lifecycle state (`done` / `failed` / `cancelled` / `goal` are
    closed; `deferred` is NOT — it can go overdue), AND its soonest deadline
    occurrence is strictly in the past — at a granularity that follows the
    stored form:
      * a deadline WITH a time (`2026-07-23T09:00`) is overdue the moment that
        TIMESTAMP passes, compared against ``now`` (UTC by default);
      * a DATE-ONLY deadline (`2026-07-23`) is overdue only once its whole day
        has passed — a bare "today" is not overdue until tomorrow.
    (hook-bypass: line-limit.)

    Used by the fleet liveness handler and the CLI's `list-tasks
    --overdue` filter to surface late tasks at a glance (operator
    TG12664 "attended an overdue task but no suitable UI to act on it" —
    todo-p6-overdue-ui). Pure function (no I/O); deterministic given
    ``now``.

    OVERDUE IS A FILTER, NOT AN ALARM. This predicate is PULL-only —
    something has to ASK (``list_tasks(overdue=True)``, the board, the
    fleet payload's ``overdue_count``). It is never PUSHED: no reminder
    digest, stale-active nudge or backlog sweep calls it, so a card
    going overdue notifies nobody. Owner nudges come from INACTIVITY
    (``last_activity``), never from deadlines.

    NOTE — a RECURRING deadline is NEVER overdue. ``next_deadline_for_task``
    rolls a repeater's next occurrence into the FUTURE, so the comparison
    below can never fire for one (true of both the ``+`` and ``++`` forms,
    at any ``now``). Only a NON-recurring deadline can go overdue. A
    recurring deadline therefore reaches neither the notification rail nor
    this filter — it is a date-pill. (hook-bypass: line-limit.)
    """
    import datetime as _dt

    status = (task.get("status") or "").strip()
    # Terminal/closed statuses are never overdue. ``cancelled`` (closed as
    # not planned) joins done/failed here. ``deferred`` is NOT terminal
    # (operator ruling 2026-07-10) — a deferred card is open, so it CAN go
    # overdue and must surface. (hook-bypass: line-limit.)
    if status in {"done", "failed", "cancelled", "goal"}:
        return False
    nxt_dt, has_time = _next_deadline_dt_typed(task, now=now)
    if nxt_dt is None:
        return False
    cur = now or _dt.datetime.now(tz=_dt.timezone.utc)
    # A deadline that carried a TIME is overdue the moment its timestamp passes
    # (aware-normalised so naive-vs-aware never raises — the blank-board scar).
    # A DATE-ONLY deadline stays day-granular: overdue only once its whole day
    # has passed, so a bare "today" is not overdue at 00:01. A ``now`` given as
    # a bare date can't do sub-day precision, so it falls to the day path too.
    if has_time and isinstance(cur, _dt.datetime):
        return _as_aware_utc(nxt_dt) < _as_aware_utc(cur)
    today = cur.date() if hasattr(cur, "date") else cur
    return nxt_dt.date() < today


@dataclass(frozen=True)
class Repeater:
    """An org-mode-style repeater on a deadline/scheduled timestamp.

    P4 PR3 (lead-approved 2026-06-12). Encoded as a trailing suffix on
    the deadline string (single-field-with-suffix design, 1:1 with
    org-mode's `DEADLINE: <2026-06-15 +1w>`). Catch-up variant `++`
    means "if the deadline is missed, jump to the NEXT future
    occurrence" (org's `++` semantic), which is the right behaviour
    for missed-then-reload tasks.

    A REPEATER IS NOT A RECURRING REMINDER. It schedules nothing and it
    never notifies — and because it always rolls the next occurrence
    into the FUTURE, a recurring card is never :func:`is_overdue`
    either (see that function). So it drives NEITHER rail: it feeds the
    date-pill / sort / org export and nothing else.
    ``deadline: "2026-01-01 +1w"`` rolls every week and pages NOBODY,
    and never shows up under ``--overdue``. To be prodded about an
    ongoing responsibility, keep an open owned card — INACTIVITY is
    what nudges, never deadlines. (hook-bypass: line-limit.)

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

        ``base`` and ``now`` are compared on tz-normalised COPIES. They arrive
        mismatched in practice — a bare-date deadline parses NAIVE while the
        board/fleet callers pass a tz-AWARE UTC ``now`` — and comparing them
        raised ``TypeError: can't compare offset-naive and offset-aware
        datetimes``, which 500'd ``/graph`` and blanked the operator's board
        on 2026-07-12. The RETURNED value keeps ``base``'s original awareness:
        handing callers a tz-aware datetime where they have always received a
        naive one would be a contract change smuggled in as a bug fix.
        """
        import datetime as _dt

        if now is None:
            now = _dt.datetime.now()
        if _as_aware_utc(base) >= _as_aware_utc(now):
            return base
        # Add one period repeatedly until >= now. Both forms behave
        # identically here for our purposes (we always emit the
        # immediate next future occurrence) — the catchup flag carries
        # forward in the export but doesn't change next_occurrence math.
        current = base
        while _as_aware_utc(current) < _as_aware_utc(now):
            current = _add_period(current, self.n, self.unit)
        return current


_REPEATER_RX = None  # lazily compiled below


def _get_repeater_rx():
    """Lazy-compile the repeater regex.

    Pattern: a trailing space + ``+`` or ``++`` + integer + unit letter.
    """
    import re as _re

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
