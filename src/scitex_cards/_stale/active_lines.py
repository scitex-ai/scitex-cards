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

        BACKLOG: N deferred card(s) waiting >Nh [aged by <field>; owner by
        agent] — start or triage (begin, re-prioritise, or close): <id>, …

    THE VERB MUST MATCH THE CLOCK. It said "untouched" until 2026-08-17, beside
    a bracket reading ``[aged by deferred_at]`` — two different predicates in one
    sentence, the second contradicting the first. ``deferred_at`` is when a card
    ENTERED deferred; ``last_activity`` is when it was last touched, and a card
    can be deferred for a month and worked an hour ago.

    Reported by dotfiles with a counterexample, verified here before acting:
    ``dotfiles-absolute-symlink-debt-20260712`` had ``deferred_at``
    2026-07-16 and ``last_activity`` 4.5 hours before the nudge that called it
    untouched — and that activity was a full triage pass, not a token edit.

    "Waiting" is what ``deferred_at`` actually measures, so the sentence now
    corroborates its own bracket instead of arguing with it. Note the shape of
    the original defect: the bracket was ADDED to fix an ambiguity and the stale
    word was left standing next to it. A correction applied ADDITIVELY to prose
    leaves the falsehood in place — the reader now has two claims and no way to
    know which the code honours.

    THE CLOCK ITSELF IS DELIBERATE AND WAS NOT CHANGED. Ageing by
    ``last_activity`` instead — or by both — would silence exactly the card this
    sweep exists to find: one deferred long ago and revisited with a comment
    every few weeks without ever being started. A touch is not a start.
    ``last_activity`` measures whether anyone LOOKED; ``deferred_at`` measures
    how long it has WAITED, and waiting is the subject.

    The wording names ``deferred`` — the backlog status since the pending
    abolition. A nudge telling an agent about "pending cards" it cannot find
    (or write) is an instruction it cannot follow.

    IT STATES ITS OWN PREDICATE, and that bracket is not decoration. On
    2026-08-16 one question — "how many backlog cards does this owner have" —
    produced four different true answers on ONE database: 62 from the sweep,
    103 from ``last_activity > 24h``, 163 from ``deferred_at > 24h``, and 583
    from a reader on a stale replica. Two agents spent an hour discovering that
    none of them disagreed; they were four different predicates wearing one
    sentence. A count is not a fact until it says what it counted.

    The two axes it names are exactly the two that differed. The CLOCK, because
    "untouched" meant `last_activity` in this sweep and `deferred_at` in the CLI
    triage surface. And the OWNER field, because :func:`_owner_of` resolves
    ``agent`` before ``assignee``, and for one owner that day those fields held
    656, 645 and 549 cards — three populations, one word.

    The field name is READ FROM THE CLOCK the sweep actually uses rather than
    written here as prose, so the two cannot drift apart. A hand-written label
    is the failure this docstring is describing, one level up: it would be
    documentation asserting something the code stopped doing, which is how
    ``_inbox.py`` came to tell readers the inbox defaults to SQLite.
    """
    from .active import _pending_nudge_hours
    from .active_clocks import BACKLOG_AGE_FIELD

    thr = f"{_pending_nudge_hours(pending_hours):g}"
    return (
        f"BACKLOG: {len(cards)} deferred card(s) waiting >{thr}h "
        f"[aged by {BACKLOG_AGE_FIELD}; owner by agent] — "
        f"start or triage (begin, re-prioritise, or close): {_cap_ids(cards)}"
    )
