#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CARD-DATA invariants — "is the DATA telling the truth?"

Split out of ``_health`` (PURE MOVE for the existing check — no behaviour change),
which re-exports every name below so ``from scitex_todo._health import
_check_terminal_state_honest`` keeps working. Same contract as the ``_store_write``
and ``_mcp_server`` splits.

``_health`` asks whether the INSTALLATION is wired up (store resolvable, agent id
resolvable, notifyd alive, channel draining). This module asks a different
question with different inputs and different fixes: do the CARDS CONTRADICT
THEMSELVES?

    _check_terminal_state_honest   a card that is closed must not also be open,
                                   and a card that is not `done` must not claim a
                                   completion stamp.
    _check_no_falsely_blocked      a card blocked on dependencies that have ALL
                                   finished is not blocked — it is unstarted.

Both are the same species of defect, and it is this repo's most persistent one:
A FACT WRITTEN IN ONE PLACE AND BELIEVED IN ANOTHER, WITH NOTHING FORCING THEM TO
AGREE. Every check here is one query rather than a note in a file nobody re-reads,
because AN INVARIANT NOBODY RUNS IS NOT AN INVARIANT.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: A card that carries ``_log_meta.closed_at`` has been CLOSED. These are the
#: statuses that mean it is still OPEN. The two sets must not intersect.
_OPEN_STATUSES = ("goal", "in_progress", "blocked", "deferred")

#: Every ``_log_meta`` key that means "this card was closed". If you add one,
#: ADD IT HERE **and** give it a rule in _check_terminal_state_honest — that
#: check reported ok on 5 real cards because it knew `closed_at` and had never
#: been told about `completed_at`. test__completion_stamp_honesty pins that
#: every marker listed here is actually enforced, so a new entry cannot be
#: added decoratively.
_CLOSURE_MARKERS = ("closed_at", "completed_at")

#: The only status that means the work was DELIVERED. `cancelled` and `failed`
#: are terminal but are NOT completions, and must never carry `completed_at`.
_COMPLETED_STATUS = "done"


def _check_terminal_state_honest(store: str | Path | None) -> dict[str, Any]:
    """ok when no card is CLOSED and OPEN at the same time.

    THE INVARIANT: a card that carries ``_log_meta.closed_at`` OR
    ``_log_meta.completed_at`` was closed. It cannot also be sitting in
    ``deferred`` / ``in_progress`` / ``blocked`` / ``goal``. If it is, the
    close DID NOT STICK, and the card is a ZOMBIE: finished work that keeps
    nagging its owner in every digest, forever.

    *** ``completed_at`` WAS ADDED ON 2026-07-14 AND THAT OMISSION IS THE
    POINT. *** This check originally named ``closed_at`` alone — one marker of
    closure — and was therefore silent on the other. Five cards on the live
    board carried ``completed_at`` while sitting in ``deferred`` /
    ``in_progress`` / ``cancelled``, and this check, whose whole job is to find
    exactly that, reported ok. A guard that enumerates ONE cause of a condition
    is an implicit claim that it is the only one; the enumeration reads as a
    promise of completeness even when nobody meant it as one. If you add a
    third closure marker, add it HERE, or this check will lie again.

    ``completed_at`` is the worse of the two to leak, because it is not merely
    ignored downstream — it is BELIEVED: ``_django/handlers/fleet/timing.py``
    and ``timeline.py`` compute throughput from ``completed_at`` alone and
    never consult ``status``. So a stamped-but-open card is counted as
    delivered work AND nags as backlog, simultaneously.

    *** THIS EXISTS BECAUSE IT ALREADY HAPPENED, TWICE, AND NOBODY NOTICED FOR
    TWO DAYS. *** (2026-07-13: `selftest-card-20260701` and
    `todo-board-reads-stale-project-store-not-canonical-20260706` both carried
    `closed_at` and both sat in `deferred`. Both had COMMENTS saying they had
    been moved to a terminal state — the prose claimed the change; the FIELD
    never took it. They were found only by hand-scanning all 1,467 rows.)

    A zombie is invisible precisely BECAUSE it looks like ordinary backlog. It
    is a signal that keeps emitting after it stopped carrying information —
    which is this codebase's recurring defect, and the reason the check is one
    query rather than a note in a file nobody re-reads. AN INVARIANT NOBODY RUNS
    IS NOT AN INVARIANT.

    Never raises: an unreadable store is reported, not thrown.
    """
    try:
        from ._store import load_tasks

        tasks = load_tasks(store)
    except Exception as exc:  # noqa: BLE001 — an unreadable store is a reportable state
        return {
            "ok": False,
            "detail": f"cannot read the task store ({type(exc).__name__}: {exc})",
            "hint": "check the store path with `scitex-todo resolve-store`.",
        }

    # Two DISTINCT lies, deliberately not merged — they corrupt different
    # readers and carry different fixes.
    #
    #   zombie          closed_at + an OPEN status. The close did not stick, so
    #                   the card nags its owner forever as work already done.
    #
    #   false-completion  completed_at + any status that is NOT `done`. This one
    #                   is not merely ignored downstream, it is BELIEVED:
    #                   _django/handlers/fleet/timing.py and timeline.py compute
    #                   throughput from completed_at ALONE and never look at
    #                   status. A `cancelled` card carrying the stamp is counted
    #                   as delivered work — the precise corruption that closing a
    #                   killed card as "done" was supposed to avoid.
    #
    # Note a `cancelled` card is caught by the SECOND rule and not the first: it
    # is terminal (it does not nag), yet it still inflates throughput.
    zombies = [
        str(t.get("id") or "?")
        for t in tasks
        if (t.get("_log_meta") or {}).get("closed_at")
        and t.get("status") in _OPEN_STATUSES
    ]
    false_completions = [
        str(t.get("id") or "?")
        for t in tasks
        if (t.get("_log_meta") or {}).get("completed_at")
        and t.get("status") != _COMPLETED_STATUS
    ]
    if zombies or false_completions:
        parts, hints = [], []
        if zombies:
            shown = ", ".join(zombies[:5])
            more = f" (+{len(zombies) - 5} more)" if len(zombies) > 5 else ""
            parts.append(
                f"{len(zombies)} card(s) are CLOSED and OPEN at once — they carry "
                f"_log_meta.closed_at but still sit in an open status, so they nag "
                f"their owner forever as work that is already done: {shown}{more}"
            )
            hints.append(
                "the close did not stick. Set the honest terminal state — `done` if "
                "the work landed, `cancelled` if it was closed as not-planned — with "
                "`scitex-todo update <id> --status done|cancelled`. A comment saying "
                "a card is closed is NOT a decision; the STATUS FIELD is."
            )
        if false_completions:
            shown = ", ".join(false_completions[:5])
            more = (
                f" (+{len(false_completions) - 5} more)"
                if len(false_completions) > 5
                else ""
            )
            parts.append(
                f"{len(false_completions)} card(s) carry _log_meta.completed_at "
                f"while NOT being `done` — the throughput and timeline surfaces "
                f"aggregate on completed_at alone, so these count as DELIVERED "
                f"WORK that was never delivered: {shown}{more}"
            )
            hints.append(
                "un-completing a card must clear its stamp: use "
                "`scitex_todo._store_lifecycle.clear_completion_stamp(task)`. "
                "Moving the STATUS without clearing the STAMP fixes what the "
                "sweeps read and leaves what the throughput reads still lying."
            )
        return {"ok": False, "detail": " | ".join(parts), "hint": " ".join(hints)}
    return {
        "ok": True,
        "detail": f"no zombie cards ({len(tasks)} scanned): closed cards are closed",
        "hint": None,
    }



#: A dependency in one of these states can no longer gate anything.
_TERMINAL_STATUSES = ("done", "cancelled", "failed")


def _check_no_falsely_blocked(store: str | Path | None) -> dict[str, Any]:
    """ok when no card is ``blocked`` on dependencies that have ALL finished.

    THE INVARIANT: ``blocked`` is a CLAIM — "something outside my control is
    stopping me". When every card in ``depends_on`` has reached a terminal state,
    that claim is FALSE. The card is not blocked; it is merely not started. Saying
    otherwise converts "I have not done this" into "I am PREVENTED from doing
    this", which is what makes a backlog untouchable and a digest unreadable.
    (Framing owed to scitex-agent-container, 2026-07-14, who noticed they had
    deflected their own card for 109 hours by treating a status as a decision.)

    *** WHY THIS IS NOT ALREADY IMPOSSIBLE. *** The store DOES drive the unblock:
    ``_store_events._emit_unblock_for_dependents`` fires when a card completes and
    emits an "your task is now unblocked" event naming every dependent it freed.
    But AN EMIT IS A NOTIFICATION, NOT A MECHANISM — it tells the owner, and then
    nothing enforces that the owner acts. Measured on the live board 2026-07-14:
    **10 cards across 5 agents sat `blocked` with every dependency already done.**
    The notification had done its job and the field still lied.

    Same species as the completion-stamp bug fixed the same day: a fact written in
    one place and believed in another, with nothing forcing them to agree.

    DELIBERATELY REPORTS RATHER THAN AUTO-FLIPS. An owner may have an unstated
    reason, and silently rewriting another agent's card is a worse sin than naming
    the contradiction. Fail loud; do not fail clever.

    Cards blocked with NO ``depends_on`` are ignored — they name a blocker
    (``operator-decision``, ``compute``, ...) rather than a card, and this check
    has no way to know whether that gate is still real. A dangling dep id is also
    skipped: that is a different defect and conflating the two would make both
    harder to read.

    Never raises: an unreadable store is reported, not thrown.
    """
    try:
        from ._store import load_tasks

        tasks = load_tasks(store)
    except Exception as exc:  # noqa: BLE001 — an unreadable store is a reportable state
        return {
            "ok": False,
            "detail": f"cannot read the task store ({type(exc).__name__}: {exc})",
            "hint": "check the store path with `scitex-todo resolve-store`.",
        }

    by_id = {t.get("id"): t for t in tasks}
    liars: list[str] = []
    for task in tasks:
        if task.get("status") != "blocked":
            continue
        deps = task.get("depends_on") or []
        if not deps:
            continue
        resolved = [by_id.get(d) for d in deps]
        if any(d is None for d in resolved):
            continue
        if all(d.get("status") in _TERMINAL_STATUSES for d in resolved):
            liars.append(str(task.get("id") or "?"))

    if liars:
        shown = ", ".join(liars[:5])
        more = f" (+{len(liars) - 5} more)" if len(liars) > 5 else ""
        return {
            "ok": False,
            "detail": (
                f"{len(liars)} card(s) are BLOCKED ON NOTHING — every card in their "
                f"depends_on has already finished, so the gate they name is gone and "
                f"the `blocked` status is false: {shown}{more}"
            ),
            "hint": (
                "the unblock event fired when the dependency completed and nobody "
                "acted on it. Move each card to the honest status — `in_progress` if "
                "it is being worked, `deferred` if it can wait — with `scitex-todo "
                "update <id> --status in_progress|deferred`. A card blocked on a "
                "finished card is not blocked, it is unstarted."
            ),
        }
    return {
        "ok": True,
        "detail": (
            f"no falsely-blocked cards ({len(tasks)} scanned): every blocked card "
            "names a gate that is still open"
        ),
        "hint": None,
    }


__all__ = [
    "_OPEN_STATUSES",
    "_CLOSURE_MARKERS",
    "_COMPLETED_STATUS",
    "_TERMINAL_STATUSES",
    "_check_terminal_state_honest",
    "_check_no_falsely_blocked",
]
