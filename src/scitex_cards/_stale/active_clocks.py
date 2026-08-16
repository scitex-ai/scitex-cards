#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sweep clocks — *which quantity each sweep measures*, and owner resolution.

Extracted from :mod:`scitex_cards._stale_active` (which re-exports every public
name here, so no importer moves). The two clocks live side by side ON PURPOSE:
picking the wrong one is the defect this module exists to prevent, and that
choice should be made where both are visible.

The distinction is NOT that one sweep sees fewer cards. Both inspect every card
and return an answer for every card. They measure DIFFERENT QUANTITIES:

* :func:`_age_hours` — "when was this last touched?" Correct for stale-active,
  whose question is "why haven't you acted?", because a comment IS acting.
* :func:`_blocked_age_hours` — "how long has the blocker been uncleared?"
  Correct for the blocked-check, whose question is "has your blocker cleared?"
  A comment does not clear a blocker, so it must not reset this clock.

Keying the blocked-check on ``last_activity`` made that alarm SILENCEABLE BY
TYPING: a diligently-annotated stuck card became indistinguishable from an
unstuck one, which inverted the incentive — recording evidence on a stuck card
is exactly the behaviour we want, and doing it hid the card. (Reported by grant
2026-07-30 with three consecutive sweeps: of five cards that dropped off, three
had genuinely been reclassified and two had merely been COMMENTED on, the
awaited transition still unposted. They ended up deliberately NOT commenting on
seven genuine external waits to keep them visible.)

RULE: never key a sweep on a field that any mutation touches. Ask what the sweep
is *asking*, then measure that.
"""

from __future__ import annotations

import datetime as _dt

from scitex_cards._throughput import _parse_iso

__all__ = [
    "FIELD_BLOCKED_AT",
    "_owner_of",
    "_age_hours",
    "_blocked_age_hours",
    "_deferred_age_hours",
    "BACKLOG_AGE_FIELD",
]

#: When the card entered its CURRENT ``(status, blocker)`` pair — the
#: blocked-check's own clock. Stamped by ``_store_mutate._stamp_blocked_at``.
#:
#: ``deferred_at`` already encoded this same lesson for the backlog sweep; its
#: docstring warns that stamping on "any passing mutation (a comment, a
#: reassign) would silently reset the rot clock … which is the one thing this
#: field exists to prevent." The blocked sweep was added later and never got its
#: equivalent, so this is an omission being closed, not a new idea.
FIELD_BLOCKED_AT = "blocked_at"


def _owner_of(task: dict) -> str:
    """The card's owner = the USER the nudge is addressed to.

    ``agent`` is the canonical owner field; ``assignee`` is the
    fallback for cards that predate the ``agent`` rename. Empty owner
    surfaces as ``"(unassigned)"`` so the gap is visible, never
    silently dropped (mirrors ``_throughput.aggregate``).
    """
    owner = (task.get("agent") or task.get("assignee") or "").strip()
    return owner or "(unassigned)"


def _age_hours(task: dict, now: _dt.datetime) -> float | None:
    """Hours since the card was last TOUCHED. The stale-active clock.

    ``last_activity`` is authoritative; ``created_at`` is the fallback
    for cards that have never been touched since creation. Returns
    ``None`` only when BOTH are missing/unparseable — such a card is
    treated as stale (we can't prove it's fresh).

    Correct for stale-active, WRONG for the blocked-check — see the module
    docstring before reaching for this one.
    """
    ts = task.get("last_activity") or task.get("created_at")
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    return (now - parsed).total_seconds() / 3600.0


def _blocked_age_hours(task: dict, now: _dt.datetime) -> float | None:
    """Hours in the card's CURRENT ``(status, blocker)`` pair. The blocked clock.

    Falls back to ``created_at``, and NEVER to ``last_activity``. Cards blocked
    before :data:`FIELD_BLOCKED_AT` shipped carry no stamp, and routing those
    through ``last_activity`` would reproduce the original defect for the entire
    existing population — the two cards that motivated this fix would stay
    silenced by the fix.

    ``created_at`` makes an unstamped card read as maximally stale, so the alarm
    errs toward FIRING. That is the safe direction: a spurious "has your blocker
    cleared?" costs a glance, a suppressed one costs a card.

    THE FALLBACK IS PERMANENT. IT IS NOT A MIGRATION RAMP. DO NOT DELETE IT ONCE
    "the fleet is current" — that condition is not establishable, and the whole
    reason is grant's, 2026-07-30: **a stamp's presence encodes the WRITER's
    version, not the card's history.** Every agent writes this store from its own
    container (measured that day: 0.13.5 / 0.17.5 / 0.18.0 / 0.22.0 all live), so
    two cards blocked at the same moment report different ages purely by which
    agent last touched them. "Unstamped" conflates *born blocked* with *last
    written by an older agent*, and nothing in the row separates them.

    So requiring the stamp would make an unstamped row read as an error or as age
    zero — i.e. FRESH — which is the original bug wearing a new mechanism. The
    fallback is not scaffolding around the field; it is what makes the field safe
    to read at all.
    """
    ts = task.get(FIELD_BLOCKED_AT) or task.get("created_at")
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    return (now - parsed).total_seconds() / 3600.0


#: The field :func:`_deferred_age_hours` ages by, named ONCE so the nudge text
#: can quote the clock instead of describing it. ``pending_backlog_nudge_line``
#: prints this, so changing the clock changes the message in the same edit —
#: a hand-written label is how a doc comes to assert what the code stopped
#: doing, which is the defect this whole change is about.
BACKLOG_AGE_FIELD = "deferred_at"


def _deferred_age_hours(task: dict, now: _dt.datetime) -> float | None:
    """Hours since the card ENTERED the backlog. The backlog clock.

    Reads ``deferred_at``, falling back to ``created_at`` and NEVER to
    ``last_activity`` — the same shape as :func:`_blocked_age_hours`, for the
    same reason, and every paragraph of that docstring applies here verbatim.

    WHY THIS EXISTS, given ``deferred_at`` has been written since 2026-08-13.
    It was written and never read. ``detect_pending_backlog`` passed no clock,
    so it inherited the default :func:`_age_hours`, which measures
    ``last_activity`` — "when was this TOUCHED". The result is the defect
    ``_store_clocks`` warns about in the module that WRITES the field: "key any
    of them on ``last_activity`` and the sweep becomes SILENCEABLE BY TYPING —
    a comment refreshes the clock, the alarm resets, and the card rots while
    reading as fresh." Commenting on a rotting deferred card silenced its own
    backlog nudge for a day, and every agent annotating its cards was doing it.

    MEASURED BLAST RADIUS before this landed (2026-08-16, 1854 deferred cards):
    1193 nudged under ``last_activity``, 1354 under this clock — +161, and ZERO
    cards stop being nudged. The change is monotonic: it can only ADD coverage,
    never remove it, because a card's entry into the backlog cannot be later
    than its last touch. That is what makes it safe to land in one step.
    """
    ts = task.get(BACKLOG_AGE_FIELD) or task.get("created_at")
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    return (now - parsed).total_seconds() / 3600.0


# EOF
