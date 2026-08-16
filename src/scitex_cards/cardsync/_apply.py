#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply reconciliation verdicts. Compare-and-set, and no delete verb.

The decision lives in :mod:`._decide`; this is the part that writes, and it
is deliberately small. Two properties are enforced here rather than
documented, because both have already cost this fleet data.

**Every write is compare-and-set.** A reconciler reads both stores, thinks,
then writes — and in the gap an agent can change the row. A plain UPDATE
would silently discard that. So each write carries the value we READ, and
the store applies it only if the row still holds exactly that. A row that
moved is SKIPPED and counted, never overwritten. On 2026-08-10 this was run
against 2,338 live rows with 0 skips; the guard cost nothing and would have
been the only thing standing between a concurrent edit and its loss.

**There is no delete.** Not "delete is discouraged" — the interface has no
verb for it. Reconciling by comparing end states cannot tell "never sent to
me" from "removed there", so a delete path here would be acting on an
inference that is wrong half the time. Reading absence as deletion is what
destroyed 2,159 rows on 2026-07-19/21.

Dry-run is the DEFAULT. A caller must pass ``apply=True`` to write, so the
dangerous mode is the one you have to ask for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from ._decide import Side, Verdict, decide

__all__ = ["CardStore", "ReconcileReport", "reconcile"]


class CardStore(Protocol):
    """The two operations reconciliation needs. Deliberately not a DB API.

    Keeping this to read-all + compare-and-set means the same logic drives a
    local Postgres, a psql-over-ssh peer, or an in-memory fake in a test,
    and none of them can express a delete.
    """

    name: str

    def read_all(self) -> "Mapping[str, str]":
        """Every card as ``{id: raw_json}``. Raw text, not parsed: the
        compare-and-set compares bytes, and re-serialising would change
        them."""

    def write(self, card_id: str, new_raw: str, expected_raw: "str | None") -> bool:
        """Set ``card_id`` to ``new_raw`` only if it currently holds
        ``expected_raw`` (``None`` meaning "must not exist").

        Returns True if applied, False if the row moved underneath us.
        Never raises for a lost race — a skip is an ordinary outcome and the
        caller counts it.
        """


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """What a run did, as named counts rather than a prose summary.

    ``unresolved`` is carried separately and never folded into ``skipped``:
    a row nobody could decide is a different fact from a row someone else
    changed, and collapsing them hides the one a human must look at.
    """

    dry_run: bool
    inspected: int = 0
    already_equal: int = 0
    applied_to_a: int = 0
    applied_to_b: int = 0
    skipped_changed: int = 0
    unresolved: tuple[tuple[str, str], ...] = field(default=())

    @property
    def applied(self) -> int:
        return self.applied_to_a + self.applied_to_b

    def describe(self) -> str:
        mode = "DRY RUN" if self.dry_run else "APPLIED"
        return (
            f"{mode}: {self.inspected} inspected, {self.already_equal} equal, "
            f"{self.applied} written ({self.applied_to_a} -> A, "
            f"{self.applied_to_b} -> B), {self.skipped_changed} skipped "
            f"(changed under us), {len(self.unresolved)} unresolved"
        )


def reconcile(
    a: CardStore, b: CardStore, *, apply: bool = False
) -> ReconcileReport:
    """Converge two card stores. Dry-run unless ``apply=True``.

    Reads both stores ONCE, decides per card, then writes. The value read is
    carried into the write as the compare-and-set expectation, so anything
    changed in between is skipped rather than clobbered.
    """
    import json

    raw_a, raw_b = dict(a.read_all()), dict(b.read_all())
    ids = sorted(set(raw_a) | set(raw_b))

    equal = to_a = to_b = skipped = 0
    unresolved: list[tuple[str, str]] = []

    for cid in ids:
        ra, rb = raw_a.get(cid), raw_b.get(cid)
        pa = json.loads(ra) if ra is not None else None
        pb = json.loads(rb) if rb is not None else None
        verdict: Verdict = decide(pa, pb)

        if verdict.side is Side.NEITHER:
            equal += 1
            continue
        if verdict.side is Side.UNRESOLVED:
            unresolved.append((cid, verdict.reason))
            continue

        if verdict.side is Side.A:
            target, winner, expected = b, ra, rb
        else:
            target, winner, expected = a, rb, ra

        if not apply:
            # Count what WOULD be written, to the side that would receive it.
            if verdict.side is Side.A:
                to_b += 1
            else:
                to_a += 1
            continue

        assert winner is not None  # side A/B implies that side has the row
        if target.write(cid, winner, expected):
            if verdict.side is Side.A:
                to_b += 1
            else:
                to_a += 1
        else:
            skipped += 1

    return ReconcileReport(
        dry_run=not apply,
        inspected=len(ids),
        already_equal=equal,
        applied_to_a=to_a,
        applied_to_b=to_b,
        skipped_changed=skipped,
        unresolved=tuple(unresolved),
    )

# EOF
