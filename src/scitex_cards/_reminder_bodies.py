#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure message-body formatters for the reminder engine.

Extracted from :mod:`scitex_cards._reminders` (which stays the sweep state
machine) so the human-readable NOTE BODY strings live in one focused, pure
module — no engine state, no I/O, just ``StaleCard`` → text. The engine
imports these back; the public API is unchanged.
"""

from __future__ import annotations

#: How many cards the digest actually asks the owner to ACT ON. Small on
#: purpose. Operator, 2026-07-14 (angry, and right): a digest that says "you own
#: 98 cards" then lists 15 is unreadable, so it gets skimmed, so it stops being a
#: signal — "a list of 98 is a list of 0. Give me THREE. I will act on three."
#: The total is not hidden; it is demoted to a one-line footnote.
DIGEST_ACT_ON = 3

#: Back-compat alias — some callers/tests still import DIGEST_CARD_CAP.
DIGEST_CARD_CAP = DIGEST_ACT_ON


def _rank_key(sc):
    """Digest ranking: highest PRIORITY first, then OLDEST first.

    Priority is the primary axis (P1 before P2; a card with no priority sorts
    last, as 9999). Within one priority, oldest-first — the card that has been
    ignored longest at that priority rises. This makes slot #1 the "oldest
    un-started high-priority card", which is the first of the three signals the
    operator asked to lead with. (The other two axes sac named need data that
    StaleCard does not yet carry; they fold in once it does — the detail is on
    card cards-nudges-need-force-rank-digest-escalate-to-operator-20260714, kept
    OUT of this delivery module on purpose. Ranking by priority+age is already a
    categorical improvement over "the 15 oldest regardless of priority".)

    NB: this module is on the delivery surface, which is contractually forbidden
    from reading a card's deadline (a deadline is a VIEW, never a notifier). The
    ranking here reads priority and age ONLY — never a due date — and
    test__deadline_never_notifies pins that. That is why the second axis is
    described abstractly above rather than by its natural name.
    """
    pri = sc.priority if isinstance(sc.priority, int) else 9999
    age = sc.age_hours if sc.age_hours is not None else float("inf")
    return (pri, -age)


def _card_line(sc) -> str:
    title = (sc.title or "").strip() or "(untitled)"
    age = "" if sc.age_hours is None else f", ~{sc.age_hours:.0f}h"
    return f"  - {sc.id} [{sc.status}{age}] \"{title}\""


def _digest_body(cards: list, attempt: int) -> str:
    """One digest that LEADS with the few cards worth acting on, ranked.

    Reworked 2026-07-14 on the operator's direct complaint that the old digest —
    "you own N open cards" followed by the 15 oldest — was unreadable and so got
    skimmed into meaninglessness. Now it names the :data:`DIGEST_ACT_ON`
    highest-priority, longest-ignored cards and demotes the total to a single
    footnote line.

    Ranking is by :func:`_rank_key` (priority, then age). The digest still does
    not DICTATE a single next card — it surfaces the top few — but it no longer
    pretends a 98-item list is an actionable prompt.
    """
    total = len(cards)
    ranked = sorted(cards, key=_rank_key)
    shown = ranked[:DIGEST_ACT_ON]
    lines = [_card_line(sc) for sc in shown]
    head = (
        f"Assigned-card digest #{attempt}: ACT ON THESE {len(shown)} "
        f"(highest priority, longest ignored) — work it, update it, reassign, "
        f"or close:"
    )
    body = head + "\n" + "\n".join(lines)
    remaining = total - len(shown)
    if remaining > 0:
        # The total is a FOOTNOTE, not the headline. It says "there is more" and
        # how to see it, without drowning the three cards that matter.
        body += (
            f"\n  ({remaining} more open — the point is these {len(shown)}, not "
            f"the pile; `scitex-cards list-tasks --status in_progress,blocked,"
            f"deferred` for all {total}.)"
        )
    return body


def _escalation_body(sc, owner: str, count: int) -> str:
    title = (sc.title or "").strip() or "(untitled)"
    return (
        f"ESCALATION: high-priority card {sc.id} owned by {owner} has been "
        f"digested {count}x and is still {sc.status} and untouched. Needs "
        f"attention. \"{title}\""
    )


def _humanize_age(age_seconds: "int | None") -> str:
    """Compact 'how long since last_seen' for a creator-escalation body.

    ``None`` (owner never seen → liveness ``"unknown"``) reads as "never
    seen"; otherwise the largest sensible unit (min / h / d). Pure + total.
    """
    if age_seconds is None:
        return "never seen"
    if age_seconds < 3600:
        return f"~{max(age_seconds // 60, 0)}m ago"
    if age_seconds < 86400:
        return f"~{age_seconds // 3600}h ago"
    return f"~{age_seconds // 86400}d ago"


def _creator_escalation_body(sc, owner: str, age_seconds: "int | None") -> str:
    """A liveness-triggered nudge to a stuck card's CREATOR.

    Names the card, its DEAD owner + how long since ``last_seen``, and the
    ask: the assignee isn't running, so reassign / drive / close.
    """
    title = (sc.title or "").strip() or "(untitled)"
    seen = _humanize_age(age_seconds)
    return (
        f"CREATOR ESCALATION: card {sc.id} is still {sc.status} but its "
        f"assignee {owner} is not running (last seen {seen}) — they will not "
        f"pick it up. As the creator: reassign it, drive it yourself, or "
        f"close it. \"{title}\""
    )


__all__ = [
    "DIGEST_ACT_ON",
    "DIGEST_CARD_CAP",
    "_rank_key",
    "_card_line",
    "_creator_escalation_body",
    "_digest_body",
    "_escalation_body",
    "_humanize_age",
]

# EOF
