#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Which side of a two-store disagreement wins, and WHY — a pure function.

A BRIDGE, WITH AN EXIT CONDITION
--------------------------------
This exists because the card store does not use an oplog-backed store primitive. It
has no oplog, so directed replay has nothing to replay, and three hosts
holding one logical board drift apart with nothing to reconcile them. On
2026-08-10 that drift reached 2,341 differing rows across two hosts and was
closed by hand.

When scitex-cards adopts the primitive, DELETE THIS MODULE. Reconciling a
foreign schema from outside is strictly worse than an oplog: it can only
compare end states, so it cannot distinguish "never sent" from "deleted",
which is the exact ambiguity the primitive was built to remove. Keeping two
reconcilers alive would also violate SSoT. This one is a stopgap that must
lose its reason to exist.

WHY A PURE FUNCTION
-------------------
Every dangerous decision in reconciliation is this one choice, and it is
worth testing without a database, a host, or a network. The I/O around it
is mechanical; this is where data gets destroyed.

THE THREE-VALUED RULE
---------------------
The outcome is not "A or B". It is A, B, or UNRESOLVED — and collapsing
UNRESOLVED into either pole is how a reconciler silently picks wrong. On
2026-08-10 two cards landed exactly there: each host had COMPLETED a
different card, and completion does not bump ``last_activity``, so
last-writer-wins was blind to both. They were resolvable only by a second
signal, and had the code guessed instead of flagging, one completion would
have been reverted with no trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

__all__ = ["Side", "Verdict", "decide"]


class Side(str, Enum):
    """Which store holds the value that should win."""

    A = "a"
    B = "b"
    #: Both sides already agree — nothing to do.
    NEITHER = "neither"
    #: They disagree and no rule settles it. NOT a synonym for "pick A".
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class Verdict:
    """A decision plus the reason for it.

    ``reason`` is carried because a reconciler that cannot explain a choice
    cannot be audited after it overwrites something. Every apply path logs
    it, so a wrong rule is traceable to the row it damaged.
    """

    side: Side
    reason: str

    @property
    def actionable(self) -> bool:
        """Whether this verdict names a side to copy from."""
        return self.side in (Side.A, Side.B)


def _completed_at(card: Mapping[str, Any]) -> str:
    return str(((card.get("_log_meta") or {}).get("completed_at")) or "")


def _last_activity(card: Mapping[str, Any]) -> str:
    return str(card.get("last_activity") or "")


def decide(
    a: "Mapping[str, Any] | None", b: "Mapping[str, Any] | None"
) -> Verdict:
    """Choose between two versions of one card. No I/O, no mutation.

    Rules, in order, each one only firing when the previous cannot:

    1. ABSENT ON ONE SIDE -> copy it there. Absence is NEVER read as
       deletion. The card store has no tombstone, so "missing here" and
       "deleted there" are indistinguishable, and the destructive reading
       is the one that cost this fleet 2,159 rows on 2026-07-19/21.
    2. IDENTICAL -> nothing to do.
    3. DIFFERENT ``last_activity`` -> the later one wins.
    4. EQUAL ``last_activity``, exactly one side carries
       ``_log_meta.completed_at`` -> that side wins. Completion is an
       explicit record of a later act, and it does not bump
       ``last_activity``, so rule 3 is blind to it.
    5. Otherwise -> UNRESOLVED. Report it; do not guess.
    """
    if a is None and b is None:
        return Verdict(Side.NEITHER, "absent from both")
    if a is None:
        return Verdict(Side.B, "absent on A; absence is not deletion")
    if b is None:
        return Verdict(Side.A, "absent on B; absence is not deletion")
    if a == b:
        return Verdict(Side.NEITHER, "identical")

    ta, tb = _last_activity(a), _last_activity(b)
    if ta > tb:
        return Verdict(Side.A, f"A newer by last_activity ({ta} > {tb})")
    if tb > ta:
        return Verdict(Side.B, f"B newer by last_activity ({tb} > {ta})")

    ca, cb = _completed_at(a), _completed_at(b)
    if ca and not cb:
        return Verdict(Side.A, f"equal last_activity; only A completed ({ca})")
    if cb and not ca:
        return Verdict(Side.B, f"equal last_activity; only B completed ({cb})")

    return Verdict(
        Side.UNRESOLVED,
        f"equal last_activity ({ta}) and no completion tiebreak; "
        f"differing fields: {sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))}",
    )

# EOF
