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
    "_stamp_cancellation_attribution",
    "_stamp_deferred_at",
]

#: Written on entry into ``cancelled``; removed on exit. See
#: :func:`_stamp_cancellation_attribution`.
FIELD_CANCELLED_AT = "cancelled_at"
FIELD_CANCELLED_BY = "cancelled_by"

#: The status this attribution governs. Named rather than inlined so the two
#: comparisons in the function below cannot drift apart.
CANCELLED_STATUS = "cancelled"

#: What :data:`FIELD_CANCELLED_BY` records when the actor cannot be resolved.
#: A SENTINEL, not an omission — see the function's docstring for why the
#: difference is the entire point of this field.
UNRESOLVED_ACTOR = "unresolved"


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


def _stamp_cancellation_attribution(
    task: dict, prior_status: str | None, actor: str | None
) -> None:
    """Record WHO cancelled a card and WHEN, on the transition into ``cancelled``.

    WHY THIS EXISTS, measured 2026-08-20. Two bulk cancellations ran on
    2026-08-19 with zero overlapping minutes. The 07:39-07:42 sweep tagged
    every one of its 111 cards with ``cancelled_by_rule`` naming the rule and
    the operator's own words. The 23:08-23:26 operation cancelled 844 cards —
    including ``sec-three-live-vulns-from-refuted-prs-20260723`` and 213
    priority-1 cards — and left NOTHING: no rule, no comment, no actor. 438 of
    that day's cancellations carry no comment at all.

    The store has no mutation audit, so those 844 are unattributable BY
    CONSTRUCTION. No query will ever name them. This stamp is the narrow fix
    that makes the NEXT one attributable, placed beside the other clocks at the
    one point a status change is applied.

    IT NEVER REFUSES THE WRITE, and that is a ruling rather than a preference.
    The operator settled it 2026-07-10: 「カードが書けないということはなしで
    大丈夫です、warning で十分です」 — a card must always be writable. Refusing
    a cancel that carries no reason would make a card unwritable, so this
    RECORDS instead. My own card proposed "make it refuse"; that proposal
    contradicted a standing ruling and is withdrawn.

    ``UNRESOLVED_ACTOR`` IS THE LOAD-BEARING CASE, not the fallback. The
    fail-loud resolver (:func:`_store._resolve_creator_or_raise`) raises
    ``creator unresolved`` — the exact error scitex-dev measured 347 times on
    2026-08-20 from a supervisor process with no agent id in its environment.
    Using it here would make cancels FAIL for precisely the unattended callers
    whose cancellations most need attributing. So the actor is resolved
    best-effort by the caller and an unresolvable one is WRITTEN DOWN:

        cancelled_by = "unresolved"   a cancel happened, nobody signed it
        (field absent)                this card predates the stamp

    Those are different facts and the sentinel is what keeps them different.
    An omitted field would have made a new anonymous sweep indistinguishable
    from the 844 already on the board.

    WHAT IT GIVES THE FLEET, and scitex-dev asked for exactly this: after this
    lands, "swept away anonymously" and "measured and judged unnecessary" are
    distinguishable at a glance — a considered cancellation carries the
    deciding agent, a rule-driven one carries ``cancelled_by_rule``, and an
    unsigned one says so.

    THE STAMPS ARE CLEARED ON EXIT, in this same function rather than a
    separate one, because a stale attribution is worse than none: a card
    reopened and later re-cancelled would otherwise carry the FIRST
    cancellation's actor and time while looking freshly stamped. The pair must
    move together, so it lives in one place — the lesson
    ``_clear_completion_stamp_on_leaving_done`` was written to record.

    Pure, per this module's contract: the caller resolves ``actor`` and owns
    the write. No env is read here.
    """
    from ._store import _utc_now_iso

    now_cancelled = task.get("status") == CANCELLED_STATUS
    was_cancelled = prior_status == CANCELLED_STATUS

    if now_cancelled and not was_cancelled:
        task[FIELD_CANCELLED_AT] = _utc_now_iso()
        resolved = (actor or "").strip()
        task[FIELD_CANCELLED_BY] = resolved or UNRESOLVED_ACTOR
        return

    if was_cancelled and not now_cancelled:
        # Leaving cancelled: drop both, together.
        task.pop(FIELD_CANCELLED_AT, None)
        task.pop(FIELD_CANCELLED_BY, None)



# EOF
