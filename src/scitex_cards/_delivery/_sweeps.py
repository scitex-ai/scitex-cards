#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The DETECT side of the notifyd loop — the sweeps that feed delivery.

:mod:`scitex_cards._delivery._daemon` owns the loop (single-instance lock,
signals, delivery ticks); this module owns the two things that RUN inside a
tick but are not delivery:

* :func:`_run_reminder_sweep` — every tick: enqueue any DUE owner digests +
  escalations (:func:`scitex_cards._reminders.sweep_reminders`) so the SAME
  tick's ``deliver_pending`` sends them.
* :func:`_run_stale_nudge_sweep` — on its OWN, much slower cadence
  (:data:`ENV_NUDGE_SWEEP_MINUTES`): the fleet-liveness sweep
  (:func:`scitex_cards._stale_active_nudge.sweep_and_nudge`). It scans the whole
  store, so it has no business in the 60 s delivery path. Until this landed the
  sweep had NO scheduled caller at all (only the interactive ``print-stats``
  verb), so idle owners were never nudged. Like the reminder sweep it ENQUEUES
  into each owner's pull-inbox (it used to push on the turn-url wire, which is
  unprovisioned for nearly every agent — so once scheduled it reached NOBODY).

Both READ the store and release it — no lock is held across a sweep (a
lock-holding sweep in this loop is what produced the store-lock convoy) — and
both are FULLY GUARDED: an exception is logged and swallowed so a bad sweep can
never kill the always-on delivery loop.

SWALLOWED IS NOT UNCOUNTED (2026-07-28/29). Both sweeps now RETURN the fault
they swallowed (``None`` when clean) so the caller can count it. Guarding the
loop against a bad sweep is right; letting the tick summary then print
``sent=0 failed=0`` — indistinguishable from a healthy idle tick — is what hid
a day-long delivery outage. The guard keeps the daemon alive; the return value
keeps it honest.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from pathlib import Path

from ._tick import fault_text

logger = logging.getLogger("scitex_cards.delivery.notifyd")

#: Cadence (MINUTES) of the fleet-liveness sweep. ``<= 0`` disables it.
ENV_NUDGE_SWEEP_MINUTES = "SCITEX_CARDS_NUDGE_SWEEP_MINUTES"
DEFAULT_NUDGE_SWEEP_MINUTES = 30.0


def _sweep_store(store: "str | Path | None"):
    """The STORE the sweep's BOOKKEEPING belongs in — never the local task file.

    ``_resolved_store`` is an alias for :func:`scitex_cards._paths.local_store_path`,
    whose own docstring warns that it answers "which local FILE", not "which
    store": on a PostgreSQL deployment it returns ``…/tasks.yaml`` while the data
    lives in ``postgresql://…``. Both sweeps used to pass that label straight into
    ``sweep_reminders(store=…)`` / ``sweep_and_nudge(store=…)``, and those hand it
    to ``_db_sweep_state`` → ``_db_target`` → ``database_for``, which maps a label
    to its SIBLING DATABASE — ``~/.scitex/cards/cards.db``, SQLite.

    MEASURED 2026-08-23 on compute-04, both ledgers live at once:

        Postgres sweep_state   612 rows   nudges    newest 15:06:31Z
                                          reminders newest 08-18 (frozen 5 days)
        SQLite   cards.db      445 rows   reminders newest 15:06:50Z
                                          nudges    newest 14:42:42Z

    Nineteen seconds apart. ``_cli/_stats.py`` calls ``sweep_and_nudge(store=store)``
    with the raw argument and therefore reached Postgres, while notifyd reached
    SQLite — so nudge dedup was split across two ledgers and a suppression recorded
    by one driver could not suppress the other. That is the exact harm
    ``_db_sweep_state`` says it exists to prevent, happening twice on ONE host.

    ``None`` (notifyd's default) now resolves to the real store target. An explicit
    value is still honoured verbatim, because a caller naming a store means it.
    """
    if store is not None:
        return store
    from .._store_target import resolve_store_target  # noqa: PLC0415 -- cycle

    return resolve_store_target(None)


def _run_reminder_sweep(*, store, now) -> "str | None":
    """Enqueue any DUE owner digests + operator escalations for this tick.

    Fully guarded: loads the task list, runs the escalating-cadence sweep
    (:func:`scitex_cards._reminders.sweep_reminders`), and logs a one-line
    summary. Any error (bad store, etc.) is logged and swallowed so the
    reminder sweep can NEVER block the delivery pass that follows it.

    Returns
    -------
    str | None
        ``None`` when the sweep ran clean; otherwise the rendered fault, for
        the caller to COUNT. This is the exact guard that hid the 2026-07-28
        outage: it logged ``RuntimeError: REFUSING TO READ …`` on every tick
        for a day while the tick summary kept printing a healthy-looking
        ``sent=0 failed=0``.
    """
    try:
        from .._model import load_tasks
        from .._reminders import sweep_reminders

        # Resolve the store BEFORE use: notifyd's `store` is None by default
        # (deliver_pending resolves it internally) and load_tasks trips on None.
        # It must resolve to the STORE, not to the local task file — see
        # _sweep_store for the two-ledger split that the local path caused.
        resolved = _sweep_store(store)
        tasks = load_tasks(resolved)
        result = sweep_reminders(tasks, store=resolved, now=now)
        if result["digested"] or result["escalated"]:
            logger.info(
                "notifyd nag sweep: %d owner digest(s), %d escalated, %d not-yet-due",
                len(result["digested"]),
                len(result["escalated"]),
                len(result["skipped"]),
            )
    except Exception as exc:  # noqa: BLE001 — must never block delivery
        logger.exception("notifyd reminder sweep raised; continuing to delivery")
        return fault_text(exc, where="reminder_sweep")
    return None


def _nudge_sweep_minutes() -> float:
    """Cadence of the liveness sweep, in minutes (env-overridable)."""
    raw = os.environ.get(ENV_NUDGE_SWEEP_MINUTES)
    try:
        return float(raw) if raw is not None else DEFAULT_NUDGE_SWEEP_MINUTES
    except (TypeError, ValueError):
        return DEFAULT_NUDGE_SWEEP_MINUTES


def _nudge_sweep_due(
    last_at: _dt.datetime | None, now: _dt.datetime, *, minutes: float
) -> bool:
    """True when the low-cadence liveness sweep is due (never run → due now)."""
    if minutes <= 0:
        return False
    if last_at is None:
        return True
    return (now - last_at).total_seconds() / 60.0 >= minutes


def _run_stale_nudge_sweep(*, store, now) -> "str | None":
    """Low-cadence fleet-liveness sweep: nudge owners of untouched work.

    Runs :func:`scitex_cards._stale_active_nudge.sweep_and_nudge`, which is
    deliver-on-change (an owner whose stale card set is unchanged is skipped
    until the floor elapses), so a SCHEDULED sweep does not become the hourly
    spam that made the digest stream ignorable — ~30 owners are stale at any
    moment, and pushing all 30 every tick trains them to ignore the one signal
    that must stay un-ignorable.

    Fully guarded: any error is logged and swallowed so the sweep can NEVER
    kill the delivery loop. Every result line (including the SUPPRESSED owners)
    is logged, so a running daemon always shows who was skipped and why.

    Returns
    -------
    str | None
        ``None`` when the sweep ran clean; otherwise the rendered fault, so the
        caller can COUNT what the guard swallowed.
    """
    try:
        from .._model import load_tasks
        from .._stale.active_nudge import sweep_and_nudge

        resolved = _sweep_store(store)
        tasks = load_tasks(resolved)
        for line in sweep_and_nudge(tasks, store=resolved, now=now):
            logger.info("notifyd liveness sweep: %s", line.strip())
    except Exception as exc:  # noqa: BLE001 — must never block delivery
        logger.exception("notifyd liveness sweep raised; continuing to delivery")
        return fault_text(exc, where="liveness_sweep")
    return None


__all__ = [
    "DEFAULT_NUDGE_SWEEP_MINUTES",
    "ENV_NUDGE_SWEEP_MINUTES",
    "_nudge_sweep_due",
    "_nudge_sweep_minutes",
    "_run_reminder_sweep",
    "_run_stale_nudge_sweep",
]

# EOF
