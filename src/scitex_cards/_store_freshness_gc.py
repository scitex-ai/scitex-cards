#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Card forgetting, owned by the store: ONE locked transaction, no per-card writes.

WHY THIS LIVES HERE. Forgetting is a property of the CARD STORE, not of the
harness that happens to schedule it: which statuses are forgettable, what
"stale" means for a row whose clock is `last_activity` with a `created_at`
fallback, and what a flip to `cancelled` must leave untouched are all decisions
only the store can make and only the store can enforce. So the operator's
division of labour is: **scitex-dev owns the organization-wide default number of
days and passes a cutoff IN**; this module owns the selection and the write.

WHAT IT DOES, in one transaction:

    SELECT pg_advisory_xact_lock(<key>)          -- serialise forgetters
    SELECT id FROM tasks WHERE <predicate>       -- the targets
    UPDATE tasks SET status='cancelled', blocker=NULL, card_json=<mirrored>
      WHERE id = ANY(<targets>)                  -- ONE statement, not N
    SELECT count(*) FROM tasks WHERE <predicate> -- the readback

WHAT IT DELIBERATELY DOES NOT DO:
  * no per-card comments — the operator ruled them out, and 1,267 closes that
    each append a comment is 1,267 writes to a document that is read by the
    whole fleet;
  * no shadow audit file — state outside the database is disqualified, and a
    separate ledger of "cards we forgot" would be exactly that;
  * no whole-store read-modify-write — the O(N) document cycle is how this
    suite rebuilt the production board three times (2026-07-19); this primitive
    issues two statements and one UPDATE against a row set, so its cost tracks
    the number of MATCHES, not the number of cards in the store;
  * no DELETE. Rows and history are preserved: the card stops being work, it
    does not stop being a record.

THE CLOCK IS INJECTABLE, and that is what makes the selection testable at all:
`now` is a parameter rather than a call to `time.time()`, so "a card last
touched 31 days ago, with an org-wide default of 30" is a deterministic test
instead of a test that runs at a particular hour.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

#: Serialises every client that runs a freshness GC. Arbitrary but FIXED: two
#: keys in the same range would silently not exclude each other. Chosen in the
#: same band as the other store-level rungs (see `_db_sweep_state`) so the
#: ordering between them stays visible in one place.
FRESHNESS_GC_LOCK_KEY = 0x5C1E_FC1E

#: The statuses an ordinary freshness sweep may forget. `goal` is included
#: because an unreviewed goal is still work nobody is doing; `done`, `failed`
#: and `cancelled` are excluded because they are already terminal, and
#: `blocked` is included only because the flip CLEARS THE BLOCKER — a card is
#: not forgotten while it still claims to be waiting on something.
FORGETTABLE_STATUSES: tuple[str, ...] = (
    "goal",
    "in_progress",
    "blocked",
    "deferred",
)

#: The row clock: `last_activity` when it is present and non-empty, else
#: `created_at`. A card that was never touched since creation has an empty or
#: null `last_activity` in the fleet's rows, and treating that as "no clock"
#: would make it invisible to every sweep forever.
#: THE CLOCK IS TEXT IN THIS SCHEMA, and that is why both sides are CAST rather
#: than compared as strings. Rows carry `2026-09-17T18:11:48Z` while a cutoff
#: built from `datetime.isoformat()` carries `+00:00` and microseconds: a
#: lexicographic comparison of those two spellings is wrong at every boundary
#: (and silently right in the middle, which is worse). Casting to `timestamptz`
#: compares instants, so the spelling of either side stops mattering.
_ROW_CLOCK = "COALESCE(NULLIF(last_activity, ''), created_at)::timestamptz"

_PLACEHOLDERS = ", ".join("?" * len(FORGETTABLE_STATUSES))


@dataclass(frozen=True)
class FreshnessGCResult:
    """What one sweep did, in counts a caller can assert on."""

    cutoff: str
    dry_run: bool
    matched: int
    cancelled: int
    remaining: int
    sample_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "cutoff": self.cutoff,
            "dry_run": self.dry_run,
            "matched": self.matched,
            "cancelled": self.cancelled,
            "remaining": self.remaining,
            "sample_ids": list(self.sample_ids),
        }


def cutoff_from_days(days: float, *, now: float | None = None) -> str:
    """The ISO-8601 UTC cutoff `days` before `now` (default: the real clock).

    This is the ONE place a number of days becomes a timestamp, so the
    organization-wide default (scitex-dev's, not ours) converts to a cutoff the
    same way everywhere, and so a test can pin the clock instead of the date.
    """
    if days < 0:
        raise ValueError("days must be non-negative")
    stamp = time.time() if now is None else now
    return (datetime.fromtimestamp(stamp, tz=timezone.utc) - timedelta(days=days)).isoformat()


def _predicate() -> str:
    return (
        f"status IN ({_PLACEHOLDERS}) "
        f"AND {_ROW_CLOCK} IS NOT NULL "
        f"AND {_ROW_CLOCK} < ?::timestamptz"
    )


def freshness_gc(
    *,
    cutoff: str,
    dry_run: bool = False,
    sample: int = 5,
    conn: Any | None = None,
) -> FreshnessGCResult:
    """Forget every forgettable card whose clock is older than `cutoff`.

    `conn` is optional and, when supplied, MUST be a connection the caller owns
    and will commit — the sweep does not manage a transaction it did not open.
    When it is absent the sweep opens the guarded connection itself, runs all
    four statements, commits once, and closes.

    The transaction is REQUIRED, not decorative: the advisory lock is
    transaction-scoped, so a sweep that autocommitted between the SELECT and
    the UPDATE would serialise against nothing.
    """
    from ._store_canonical_read import _guarded_connection
    from ._store_target import resolve_store_target

    owned = conn is None
    if conn is None:
        conn = _guarded_connection(resolve_store_target(None))
    try:
        # ONE LOCK, taken before any read: two sweeps running concurrently with
        # the same cutoff must not both see the same rows and both flip them.
        conn.execute("SELECT pg_advisory_xact_lock(?)", (FRESHNESS_GC_LOCK_KEY,))

        rows = conn.execute(
            f"SELECT id FROM tasks WHERE {_predicate()}",
            (*FORGETTABLE_STATUSES, cutoff),
        ).fetchall()
        targets = [r["id"] for r in rows]

        cancelled = 0
        if targets and not dry_run:
            # ONE statement for the whole set. The row-level `status` and the
            # verbatim `card_json` payload are updated TOGETHER, because a read
            # reconstructs cards from the payload while every query filters on
            # the column: updating one and not the other produces a store that
            # disagrees with itself about the same card.
            conn.execute(
                "UPDATE tasks SET "
                "  status = 'cancelled', "
                "  blocker = NULL, "
                "  card_json = jsonb_set("
                "    jsonb_set(card_json::jsonb, '{status}', '\"cancelled\"'), "
                "    '{blocker}', 'null')::text "
                "WHERE id = ANY(?)",
                (targets,),
            )
            cancelled = len(targets)

        # THE READBACK, from the same transaction: 'remaining' is what a caller
        # asserts on, and it is the only statement here that can prove the
        # predicate now selects nothing rather than trusting `cancelled`.
        remaining = conn.execute(
            f"SELECT count(*) AS n FROM tasks WHERE {_predicate()}",
            (*FORGETTABLE_STATUSES, cutoff),
        ).fetchone()["n"]

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()

    return FreshnessGCResult(
        cutoff=cutoff,
        dry_run=dry_run,
        matched=len(targets),
        cancelled=cancelled,
        remaining=int(remaining),
        sample_ids=tuple(targets[:sample]),
    )


__all__ = [
    "FORGETTABLE_STATUSES",
    "FRESHNESS_GC_LOCK_KEY",
    "FreshnessGCResult",
    "cutoff_from_days",
    "freshness_gc",
]
