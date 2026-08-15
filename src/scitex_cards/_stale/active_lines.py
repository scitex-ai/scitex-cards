#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nudge-line composition for the stale/backlog/blocked sweeps.

The PRESENTATION half of :mod:`scitex_cards._stale_active` (which keeps the
detection policy). Split out when the third sweep pushed the combined module
past the line limit — the two concerns had no reason to share a file.

Each sweep gets DISTINCT WORDING, and that is the whole point of having three:

* ``STALE-ACTIVE``  — "you can act on this now; why haven't you?"
  Fires on the TIGHT clock, and ONLY on cards the owner can actually move
  (in_progress, or blocked with no blocker named).
* ``BLOCKED-CHECK`` — "has your blocker cleared?"
  Fires on the LENIENT clock, on cards blocked on something outside the
  owner's control. It is a QUESTION, not a reprimand: telling an owner to
  "reconcile or update" a card they are powerless to move is an instruction
  they cannot follow, and 12 such nudges a day is how a channel gets tuned
  out — which is how the REAL nudge gets missed.
* ``BACKLOG``       — "start or triage what you accepted but never began."

The id-cap/"+K more" tail is implemented ONCE here (:func:`_cap_ids`); it was
previously copy-pasted verbatim into every composer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .active import StaleCard

#: Cap on ids rendered per owner line so a runaway lane doesn't produce
#: a multi-kilobyte nudge body.
NUDGE_ID_CAP = 12


def _cap_ids(cards: list[StaleCard]) -> str:
    """Render the card ids for a nudge line, capped with a "+K more" tail.

    Extracted so the cap is enforced in ONE place: three composers each
    carrying their own copy is three chances for one to drift and emit an
    unbounded body.

    Cards with no id are skipped; an empty result renders ``"(no ids)"``
    rather than an empty string, so a malformed row is visible instead of
    producing a nudge line that trails off into nothing.
    """
    ids = [c.id for c in cards if c.id]
    if not ids:
        return "(no ids)"
    shown = ids[:NUDGE_ID_CAP]
    tail = f", +{len(ids) - NUDGE_ID_CAP} more" if len(ids) > NUDGE_ID_CAP else ""
    return ", ".join(shown) + tail


def stale_active_nudge_line(
    owner: str,
    cards: list[StaleCard],
    *,
    stale_hours: float | None = None,
) -> str:
    """Compose the per-owner STALE-ACTIVE line (tight clock).

    Shape (single line; caller wraps / delivers):

        STALE-ACTIVE: N stale card(s) you can act on now (in_progress, or
        blocked with no blocker named; untouched >Nh) — reconcile or
        update: <id>, <id>, …

    The wording names the scope precisely, because the scope IS the point:
    every card in this line is one the owner can move right now.
    Externally-blocked cards are deliberately absent — they get
    :func:`blocked_external_nudge_line` on the lenient clock instead.
    """
    from .active import _stale_active_hours

    thr = f"{_stale_active_hours(stale_hours):g}"
    return (
        f"STALE-ACTIVE: {len(cards)} stale card(s) you can act on now "
        f"(in_progress, or blocked with no blocker named; "
        f"untouched >{thr}h) — reconcile or update: {_cap_ids(cards)}"
    )


def blocked_external_nudge_line(
    owner: str,
    cards: list[StaleCard],
    *,
    blocked_hours: float | None = None,
) -> str:
    """Compose the per-owner BLOCKED-CHECK line (lenient clock).

    Deliberately a QUESTION, not a reprimand. These cards are blocked on
    something the owner cannot move (a dependency, a compute job, another
    agent, an operator decision), so "reconcile or update" would be an
    instruction they cannot follow. The only useful ask is whether the
    blocker is still real — blockers lift SILENTLY (the dependency shipped,
    the compute job died, the operator answered somewhere else) and nobody
    re-checks a blocker they set and forgot. That is how a card rots for
    weeks behind a wall that came down long ago.

    Shape (single line; caller wraps / delivers):

        BLOCKED-CHECK: N card(s) blocked >Nh on something outside your
        control — has the blocker cleared? If so, unblock; if not, leave
        it: <id>, <id>, …
    """
    from .active import _blocked_nudge_hours

    thr = f"{_blocked_nudge_hours(blocked_hours):g}"
    return (
        f"BLOCKED-CHECK: {len(cards)} card(s) blocked >{thr}h on something "
        f"outside your control — has the blocker cleared? If so, unblock; "
        f"if not, leave it: {_cap_ids(cards)}"
    )


def pending_backlog_nudge_line(
    owner: str,
    cards: list[StaleCard],
    *,
    pending_hours: float | None = None,
) -> str:
    """Compose the per-owner BACKLOG line (lenient clock).

    Distinct wording from :func:`stale_active_nudge_line`: stale-active says
    "reconcile/update the work you said you were doing"; backlog says "start
    or triage the cards you accepted but never began".

    Shape (single line; caller wraps / delivers):

        BACKLOG: N untouched deferred card(s) (>Nh) — start or triage
        (begin, re-prioritise, or close): <id>, <id>, …

    The wording names ``deferred`` — the backlog status since the pending
    abolition. A nudge telling an agent about "pending cards" it cannot find
    (or write) is an instruction it cannot follow.
    """
    from .active import _pending_nudge_hours

    thr = f"{_pending_nudge_hours(pending_hours):g}"
    return (
        f"BACKLOG: {len(cards)} untouched deferred card(s) (>{thr}h) — "
        f"start or triage (begin, re-prioritise, or close): {_cap_ids(cards)}"
    )
