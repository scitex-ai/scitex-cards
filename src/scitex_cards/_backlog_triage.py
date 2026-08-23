#!/usr/bin/env python3
"""Deferred-backlog triage: consume the fresh, expire the rotten.

Operator doctrine (2026-07-10): *"deferred が負債なんですよね... 消費が蓄積を
上回らないといけない... 古くなれば古くなるほど腐ります；タスクは鮮度が重要
です."* A deferred card is debt, and debt that nobody looks at is debt that
compounds.

Two mechanisms, deliberately NOT one sample:

**Pick-for-action** draws a handful of deferred cards and demands a decision
on each. It is weighted toward RECENCY. The first design here was the
opposite — oldest-first — and the operator caught it: drawing the oldest cards
feeds an agent the least valuable work it owns, so the backlog ends up eating
the agent. Fresh deferred work still has value; consume it before it rots.

**Expiry** handles the other tail. Old cards are never offered for action.
Past :data:`DEFAULT_EXPIRY_DAYS` the default outcome is cancellation, and an
owner must actively rescue a card to keep it. Age is a reason to discard, not
a reason to lovingly re-triage.

The anti-rot invariant
----------------------
A "keep deferred" decision must never restamp the age clock. If it did, a card
re-deferred every week would read as permanently young and would never expire —
the rot would be real and invisible at the same time. So the age clock reads
:func:`deferred_since`, which is stamped once on entry into ``deferred`` and
never reset, and the re-draw cooldown reads ``last_triaged_at``, which is a
separate field that exists only to stop the same card being drawn twice in a
row. One field for truth, one for politeness.
"""

from __future__ import annotations

import datetime as _dt
import math
import os
import random
from dataclasses import dataclass

from ._throughput import _now_utc, _parse_iso

#: The status this module governs.
BACKLOG_STATUS = "deferred"

#: How many cards one triage sweep puts in front of an owner. Small on
#: purpose: a list of 50 "decide these" cards is itself ignorable.
ENV_TRIAGE_SAMPLE = "SCITEX_CARDS_TRIAGE_SAMPLE"
DEFAULT_TRIAGE_SAMPLE = 10

#: Recency half-life. A card deferred this many hours ago is half as likely to
#: be drawn as one deferred just now.
#:
#: KEPT AT 7 DAYS DELIBERATELY, now that the expiry horizon is also 7 days: a
#: card is drawn at full weight the day it is deferred and at HALF weight by
#: the day it expires, so attention fades across the window instead of falling
#: off a cliff at the end. That IS the operator's 「7日間の間でもう忘れるように
#: 重み付け」 — the forgetting curve is this decay, and the horizon below is
#: where it stops mattering.
#:
#: SHORTENING IT IS THE INTUITIVE MOVE AND IT BACKFIRES. The weight feeds
#: `u ** (1/w)`, an A-Res weighted-reservoir key: it is a SELECTION
#: PROBABILITY, not a lifetime. A shorter half-life makes the draw favour
#: recent cards MORE sharply, so old cards are shown LESS while continuing to
#: count — "work the last N days and never see the rest", which is the failure
#: the horizon exists to prevent. Change the HORIZON to forget; change this
#: only to re-balance which live cards get offered.
ENV_HALF_LIFE_HOURS = "SCITEX_CARDS_TRIAGE_HALF_LIFE_HOURS"
DEFAULT_HALF_LIFE_HOURS = 24.0 * 7

#: A card drawn and kept-deferred is not drawn again for this long. Without a
#: cooldown the weighting alone would re-draw the same fresh cards every sweep
#: and never show the owner anything else.
ENV_COOLDOWN_HOURS = "SCITEX_CARDS_TRIAGE_COOLDOWN_HOURS"
DEFAULT_COOLDOWN_HOURS = 72.0

#: Past this age a deferred card is expired: proposed for cancellation rather
#: than offered for action.
#:
#: SEVEN DAYS, ON THE OPERATOR'S INSTRUCTION, 2026-08-19 (TG), verbatim:
#: 「期限はそうですね。じゃあ1週間したら完全に赤い風にしようしましょうか？なので、
#: 3日ルールは7日ルールにしてください。」 — and the reason, in his words:
#: 「カードが多すぎるとその管理コストが増える割に実際にはうまくワークしない…
#: アクションの方が大事…忘れたもので本当に必要なものはもう一回私から必要に応じて
#: 必ず上がってくる」.
#:
#: WAS 30. THAT IS A 4.3x SHORTENING AND THE BLAST RADIUS IS DELIBERATE, not a
#: side effect — DRY-RUN against the live board 2026-08-19, 1748 deferred cards
#: (141 parked and therefore exempt), aged by this same clock:
#:      30d -> 356 expired     14d -> 800 expired     7d -> 935 expired
#: So this moves 579 MORE cards from "offered for action" to "proposed for
#: cancellation", spread across every owner — dev 248, hub 222, cards 181,
#: neurovista 81, clew 40, todo 39. That is the POINT: he asked for fewer
#: cards, not for a gentler number. Whoever revisits this must re-measure
#: rather than quote these figures; they are a reading, not a constant.
#:
#: EXPIRY IS A PROPOSAL, NOT A DELETION, and that is what makes the change safe
#: to ship at this size. `expired()` feeds a nudge whose text says plainly
#: that nothing cancels them for you; nothing in this package writes
#: `status=cancelled` on its own. Verified 2026-08-18: `expired()`'s only
#: consumer is `_cli/_triage.py` building nudge text.
#:
#: PARKED CARDS ARE STILL EXEMPT (`is_expired` returns False for them), so a
#: standing goal does not get swept up by the shorter horizon. That exemption
#: matters MORE at 7 days than it did at 30.
ENV_EXPIRY_DAYS = "SCITEX_CARDS_DEFERRED_EXPIRY_DAYS"
DEFAULT_EXPIRY_DAYS = 7.0

#: Field names. ``deferred_at`` is the age clock (stamped once, never reset);
#: ``last_triaged_at`` is the cooldown clock.
FIELD_DEFERRED_AT = "deferred_at"
FIELD_LAST_TRIAGED_AT = "last_triaged_at"

#: ``parked`` is the standing-card exemption: free text saying WHY the card is
#: deliberately standing. See :func:`park_reason` for why it is a reason and not
#: a boolean.
FIELD_PARKED = "parked"


def park_reason(task: dict) -> str | None:
    """The stated reason this card is deliberately standing, or None.

    A card is parked ONLY when it carries a non-empty, non-whitespace reason.
    An empty string, a whitespace string, ``True``, or any non-string is NOT a
    park — such a card sweeps normally.

    *** THE REASON IS THE POINT. ***

    This exemption exists because the sweep's predicate (``deferred`` +
    untouched) cannot tell two very different cards apart: one nobody got to —
    the case the sweep exists to catch — and one deliberately parked as a
    standing goal, whose real work lives in its children. For the second the
    nudge is unanswerable BY CONSTRUCTION: nothing to start, no gate to clear,
    and "untouched" is its steady state. It fires forever and says nothing.

    A boolean flag would have solved that and created something worse: a MUTE
    BUTTON. Mute buttons get pressed. Then every inconvenient card is muted, the
    sweep stops catching the abandoned cards it was built for, and the alarm is
    dead while still appearing to work — this codebase's recurring failure, a
    signal that keeps emitting after it stopped carrying information.

    Demanding a written reason means a card must PAY for its exemption, in
    words, where the next reader sees them. A park with no stated reason is
    precisely the abandonment the sweep should still catch, so it is not a park.
    """
    raw = task.get(FIELD_PARKED)
    if not isinstance(raw, str):
        return None
    reason = raw.strip()
    return reason or None


def is_parked(task: dict) -> bool:
    """True when the card states a reason for standing. See :func:`park_reason`."""
    return park_reason(task) is not None


def _env_float(name: str, default: float) -> float:
    """Env override read at CALL time; a junk value falls back, never raises."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def deferred_since(task: dict) -> _dt.datetime | None:
    """When this card entered the backlog — the age clock.

    Prefers the explicit ``deferred_at`` stamp. Falls back to ``created_at``
    for the cards that predate the stamp, and NEVER to ``last_activity``: a
    comment, a re-defer, or a bulk migration all touch ``last_activity``, and
    reading it here would reset the rot clock on exactly the cards that have
    been sitting longest.
    """
    return _parse_iso(task.get(FIELD_DEFERRED_AT) or task.get("created_at"))


def age_hours(task: dict, now: _dt.datetime | None = None) -> float | None:
    """Hours since the card entered the backlog. None when undatable."""
    since = deferred_since(task)
    if since is None:
        return None
    cur = now or _now_utc()
    return (cur - since).total_seconds() / 3600.0


def is_expired(
    task: dict,
    *,
    now: _dt.datetime | None = None,
    expiry_days: float | None = None,
) -> bool:
    """True when a deferred card has aged past the expiry horizon.

    An undatable card is NOT expired. We refuse to propose destroying a card
    on the basis of a timestamp we could not read.

    A PARKED card is never expired, and this is the exemption that matters most.
    Expiry PROPOSES cancellation as the default outcome; nothing in this
    module performs it (see the horizon note above) — so without this line a
    standing north-star card would be proposed for cancellation at the horizon
    for the sole crime of being a north star, which is the exact opposite of
    what its owner asked for. Age is a reason to discard work nobody is doing;
    it is not a reason to discard a goal nobody has abandoned.
    """
    if task.get("status") != BACKLOG_STATUS:
        return False
    if is_parked(task):
        return False
    age = age_hours(task, now or _now_utc())
    if age is None:
        return False
    days = expiry_days if expiry_days is not None else _env_float(ENV_EXPIRY_DAYS, DEFAULT_EXPIRY_DAYS)
    return age >= days * 24.0


def _in_cooldown(task: dict, now: _dt.datetime, cooldown_hours: float) -> bool:
    """True when this card was triaged too recently to draw again."""
    last = _parse_iso(task.get(FIELD_LAST_TRIAGED_AT))
    if last is None:
        return False
    return (now - last).total_seconds() / 3600.0 < cooldown_hours


def recency_weight(age_h: float | None, half_life_h: float) -> float:
    """Exponential decay: newer cards weigh more.

    ``w = 2 ** (-age / half_life)`` — a card one half-life old is drawn half
    as often as a brand-new one. Undatable cards get the weight of a card
    exactly one half-life old: neither favoured nor silently excluded.
    """
    if half_life_h <= 0:
        return 1.0
    if age_h is None:
        return 0.5
    return math.pow(2.0, -max(0.0, age_h) / half_life_h)


@dataclass(frozen=True)
class TriageCard:
    """One card put in front of its owner, with the numbers behind the draw."""

    id: str
    title: str
    owner: str
    age_hours: float | None
    weight: float


def _owner_of(task: dict) -> str:
    owner = (task.get("agent") or task.get("assignee") or "").strip()
    return owner or "(unassigned)"


def candidates(
    tasks: list[dict],
    *,
    owner: str | None = None,
    now: _dt.datetime | None = None,
    cooldown_hours: float | None = None,
    expiry_days: float | None = None,
) -> list[dict]:
    """Deferred cards eligible to be DRAWN — fresh enough, off cooldown, not parked.

    A PARKED card is never drawn: the nudge it would produce is unanswerable, and
    an alarm nobody can satisfy is an alarm everybody learns to ignore — taking
    the abandoned cards down with it.
    """
    cur = now or _now_utc()
    cool = cooldown_hours if cooldown_hours is not None else _env_float(ENV_COOLDOWN_HOURS, DEFAULT_COOLDOWN_HOURS)
    out = []
    for t in tasks:
        if t.get("status") != BACKLOG_STATUS:
            continue
        if owner is not None and _owner_of(t) != owner:
            continue
        if is_parked(t):
            continue
        if is_expired(t, now=cur, expiry_days=expiry_days):
            continue
        if _in_cooldown(t, cur, cool):
            continue
        out.append(t)
    return out


def expired(
    tasks: list[dict],
    *,
    owner: str | None = None,
    now: _dt.datetime | None = None,
    expiry_days: float | None = None,
) -> list[dict]:
    """Deferred cards past the horizon — proposed for cancellation."""
    cur = now or _now_utc()
    return [
        t
        for t in tasks
        if t.get("status") == BACKLOG_STATUS
        and (owner is None or _owner_of(t) == owner)
        and is_expired(t, now=cur, expiry_days=expiry_days)
    ]


def sample_for_triage(
    tasks: list[dict],
    *,
    owner: str | None = None,
    n: int | None = None,
    now: _dt.datetime | None = None,
    rng: random.Random | None = None,
    half_life_hours: float | None = None,
    cooldown_hours: float | None = None,
    expiry_days: float | None = None,
) -> list[TriageCard]:
    """Draw up to ``n`` deferred cards, weighted toward RECENCY, no repeats.

    Weighted sampling WITHOUT replacement via Efraimidis–Spirakis: give each
    item the key ``u ** (1 / w)`` for ``u`` uniform on (0, 1], then take the
    ``n`` largest keys. This gives each item an inclusion probability
    proportional to its weight in a single pass, which a naive
    "``random.choices`` then dedupe" does not.
    """
    cur = now or _now_utc()
    r = rng or random.Random()
    size = n if n is not None else _env_int(ENV_TRIAGE_SAMPLE, DEFAULT_TRIAGE_SAMPLE)
    half = half_life_hours if half_life_hours is not None else _env_float(ENV_HALF_LIFE_HOURS, DEFAULT_HALF_LIFE_HOURS)

    pool = candidates(tasks, owner=owner, now=cur, cooldown_hours=cooldown_hours, expiry_days=expiry_days)
    keyed = []
    for t in pool:
        age = age_hours(t, cur)
        w = recency_weight(age, half)
        if w <= 0:
            continue
        # u == 0 would make the key 0 for every weight; nudge off the boundary.
        u = r.random() or 1e-12
        keyed.append((math.pow(u, 1.0 / w), t, age, w))

    keyed.sort(key=lambda row: row[0], reverse=True)
    return [
        TriageCard(
            id=t.get("id", ""),
            title=t.get("title", ""),
            owner=_owner_of(t),
            age_hours=age,
            weight=w,
        )
        for _key, t, age, w in keyed[: max(0, size)]
    ]


def _days(age_h: float | None) -> str:
    return "?" if age_h is None else f"{age_h / 24.0:.0f}d"


def build_triage_body(
    drawn: list[TriageCard],
    expired_cards: list[dict],
    *,
    expiry_days: float | None = None,
) -> str:
    """The nudge an owner receives. Every line demands a decision."""
    horizon = expiry_days if expiry_days is not None else _env_float(ENV_EXPIRY_DAYS, DEFAULT_EXPIRY_DAYS)
    lines: list[str] = []
    if drawn:
        lines.append(
            f"BACKLOG TRIAGE — {len(drawn)} deferred card(s) drawn (newest weigh most). "
            f"Decide each one now: start it, name its blocker, cancel it, or keep it "
            f"deferred (kept cards keep ageing — the clock does not reset):"
        )
        for c in drawn:
            lines.append(f"  - {c.id} [{_days(c.age_hours)} deferred] {c.title[:70]}")
    if expired_cards:
        lines.append("")
        lines.append(
            f"EXPIRED — deferred > {horizon:.0f}d. Cancellation is the "
            f"INTENDED default, but NOTHING CANCELS THESE FOR YOU: silence "
            f"leaves them exactly where they are. Rescue the ones you want, "
            f"and set the rest to status=cancelled explicitly:"
        )
        for t in expired_cards:
            lines.append(f"  - {t.get('id', '')} {str(t.get('title', ''))[:70]}")
    return "\n".join(lines)


__all__ = [
    "BACKLOG_STATUS",
    "FIELD_DEFERRED_AT",
    "FIELD_LAST_TRIAGED_AT",
    "FIELD_PARKED",
    "park_reason",
    "is_parked",
    "ENV_TRIAGE_SAMPLE",
    "ENV_HALF_LIFE_HOURS",
    "ENV_COOLDOWN_HOURS",
    "ENV_EXPIRY_DAYS",
    "DEFAULT_TRIAGE_SAMPLE",
    "DEFAULT_HALF_LIFE_HOURS",
    "DEFAULT_COOLDOWN_HOURS",
    "DEFAULT_EXPIRY_DAYS",
    "TriageCard",
    "deferred_since",
    "age_hours",
    "is_expired",
    "recency_weight",
    "candidates",
    "expired",
    "sample_for_triage",
    "build_triage_body",
]
