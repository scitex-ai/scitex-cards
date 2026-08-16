#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lifecycle clocks — stamped when a card ENTERS or LEAVES a state, never on a touch.

Three helpers with one shape: ``(task, prior_status, ...) -> None | bool``, all
called from the single point in :func:`scitex_cards._store_mutate.update_task`
where a status change is applied, immediately before the save.

WHY THEY LIVE TOGETHER. Each answers a question no other field can, and each is
wrong if it fires on a passing mutation rather than a transition:

    deferred_at    when did this card ENTER the backlog?
    blocked_at     when did the (status, blocker) PAIR last move?
    completion     is the completion stamp still true after this write?

The shared failure mode is the reason for the shared home: key any of them on
``last_activity`` and the sweep that reads it becomes SILENCEABLE BY TYPING — a
comment refreshes the clock, the alarm resets, and the card rots while reading
as fresh. Every one of these is therefore keyed on a TRANSITION.

Pure: dict in, dict mutated. No I/O, no lock, no env. The caller owns the write.
"""

from __future__ import annotations

__all__ = [
    "_clear_completion_stamp_on_leaving_done",
    "_stamp_blocked_at",
    "_stamp_deferred_at",
]


def _stamp_deferred_at(task: dict, prior_status: str | None) -> None:
    """Set ``deferred_at`` when a card ENTERS the backlog, and only then.

    Fires on the TRANSITION only. A card that was already ``deferred`` is left
    untouched — including the legacy cards that carry no stamp at all, whose
    age ``deferred_since`` reads from ``created_at``. Stamping those on any
    passing mutation (a comment, a reassign) would silently reset the rot clock
    on the entire existing backlog, which is the one thing this field exists to
    prevent. A card that leaves and later returns is re-stamped, because that
    genuinely is a new spell in the backlog.
    """
    from ._backlog_triage import BACKLOG_STATUS, FIELD_DEFERRED_AT
    from ._store import _utc_now_iso

    if task.get("status") != BACKLOG_STATUS or prior_status == BACKLOG_STATUS:
        return
    task[FIELD_DEFERRED_AT] = _utc_now_iso()


def _stamp_blocked_at(
    task: dict, prior_status: str | None, prior_blocker: str | None
) -> None:
    """Set ``blocked_at`` when the ``(status, blocker)`` PAIR moves, and only then.

    The blocked-check's clock, exactly parallel to :func:`_stamp_deferred_at` but
    keyed on the pair rather than the status alone — because re-blocking the same
    card on a DIFFERENT blocker genuinely starts a new wait, while commenting on
    it does not. A comment changes ``last_activity`` and neither element of the
    pair, so it must leave this stamp alone: keying the sweep on a field every
    mutation touches is what made the alarm silenceable by typing.

    Cards already blocked before this shipped carry no stamp; they are left
    untouched here rather than back-filled on a passing mutation, and
    ``_blocked_age_hours`` reads their age from ``created_at`` instead. That
    makes them read as maximally stale, so the alarm errs toward firing.
    """
    from ._stale.active_clocks import FIELD_BLOCKED_AT
    from ._store import _utc_now_iso

    if task.get("status") != "blocked":
        return
    if prior_status == "blocked" and task.get("blocker") == prior_blocker:
        return  # Pair unchanged — not a new wait.
    task[FIELD_BLOCKED_AT] = _utc_now_iso()


def _clear_completion_stamp_on_leaving_done(
    task: dict, prior_status: str | None
) -> bool:
    """Drop ``_log_meta.completed_{at,by}`` when a card LEAVES ``done``.

    The third clock, and it exists because the invariant was WRITTEN DOWN AND
    STILL BROKEN. ``_store_lifecycle.clear_completion_stamp``'s own docstring
    says "Call this from ANY transition that takes a card OUT of ``done``", and
    until now it had exactly ONE production caller — ``reopen_task``. Every other
    exit from ``done`` went through :func:`update_task`, which never called it,
    so the stamp survived the status change.

    WHY THAT IS NOT COSMETIC. ``_django/handlers/fleet/timing.py`` and
    ``_django/handlers/timeline.py`` aggregate throughput SOLELY on
    ``completed_at`` and never consult ``status``. A stamped-open card is
    therefore counted as delivered work forever WHILE ALSO nagging its owner as
    backlog — one card that is two contradictory facts to two readers. Measured
    5 such cards on 2026-07-14 and 10 on 2026-08-16, so the population grows
    with every un-complete anyone performs.

    AND WHY IT LIVES HERE RATHER THAN AT THE CALL SITES. ``reopen_task`` forces
    ``status=blocked`` + ``blocker=operator-decision``, which is simply wrong for
    a card being deferred or cancelled — so the honest transitions are exactly
    the ones that COULD NOT use the only path that unstamped. Beside the other
    two clocks, at the one place a status change is applied, a future exit from
    ``done`` gets this without its author knowing the invariant exists.

    Returns True when a stamp was actually removed, so the caller can say so.
    """
    from ._store_lifecycle import COMPLETED_STATUS, clear_completion_stamp

    if prior_status != COMPLETED_STATUS:
        return False  # Never was complete; nothing to unstamp.
    if task.get("status") == COMPLETED_STATUS:
        return False  # Still done — a passing edit, not an exit.
    return clear_completion_stamp(task)


# EOF
