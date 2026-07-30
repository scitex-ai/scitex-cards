#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Retire a store, one-way, enforced by the engine rather than by good manners.

WHY THIS EXISTS -- identity is necessary and NOT sufficient
-----------------------------------------------------------
After a verified copy there are TWO stores with the SAME identity: the SQLite
source and the PostgreSQL destination. Both are complete. Both are legitimately
that workspace's store. ``store_uuid`` cannot separate them, and choosing the
wrong one is the failure that emptied this board before (2170 rows -> 18).

So a store must carry identity PLUS a statement of which store is CURRENT, and
**the cutover is precisely the act of moving that statement.** That statement is
written into the OLD store, so a straggler still holding the old path fails
loudly instead of quietly serving yesterday's board for a week.

WHY A TRIGGER AND NOT CLIENT CODE
---------------------------------
Today's repeated lesson: a rule only the current client honours is not a rule.
``schema_version`` was measured oscillating 7 -> 5 -> 6 on the live store within
one hour, because the venv every container agent uses ships 0.18.0, which stamps
its own version unconditionally and predates the monotonic floor. A retirement
written only by convention would be clobbered exactly the same way.

WHY ONE-WAY
-----------
A monotonic floor does not map onto a string, but one-way does, and it matches
reality: you do not un-retire a store you have already copied out of. It is also
the idiom this store already speaks -- five append-only triggers live here
(``dm_messages_no_delete`` and friends), so this is not a new concept, just one
more application of it.

THE UPSERT HAZARD, stated because it is not obvious
---------------------------------------------------
This package's idiom for ``schema_meta`` is ``INSERT OR REPLACE``, which SQLite
implements as DELETE-then-INSERT. A delete guard therefore fires on what looks
like a harmless upsert. Rather than weaken the guard to accommodate that, the
retirement keys are written with UPDATE via :func:`retire_store`, and DELETE of
a retirement key is refused outright once the store is retired. If a caller
reaches for ``INSERT OR REPLACE`` on a retired store's retirement keys, it is
supposed to fail -- that call is either a mistake or an attempt to undo the
retirement, and neither should succeed quietly.
"""

from __future__ import annotations

__all__ = [
    "RETIREMENT_KEYS",
    "RETIREMENT_TRIGGER_SQL",
    "STATUS_CURRENT",
    "STATUS_RETIRED",
    "TRIGGER_NAMES",
    "StoreRetired",
    "StoreCannotProveItsStatus",
    "read_status",
]

STATUS_CURRENT = "current"
STATUS_RETIRED = "retired"

#: Every key that carries part of a retirement. Deleting any of them once the
#: store is retired is refused.
RETIREMENT_KEYS = (
    "store_status",
    "retired_at",
    "retired_in_favour_of",
    "retired_by",
)

#: Names probed to decide whether this store can make the guarantee at all.
TRIGGER_NAMES = (
    "schema_meta_retirement_is_one_way",
    "schema_meta_retirement_is_undeletable",
)

#: Applied by ``init_schema`` alongside the other append-only guards.
#:
#: ``IS NOT`` rather than ``<>`` because SQLite's ``<>`` is NULL-propagating:
#: setting the value to NULL would compare as unknown, the WHEN clause would not
#: fire, and the retirement would be erasable by writing NULL over it. ``IS NOT``
#: treats NULL as a distinct value, which is the intent.
RETIREMENT_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS schema_meta_retirement_is_one_way
BEFORE UPDATE ON schema_meta
WHEN OLD.key = 'store_status'
 AND OLD.value = 'retired'
 AND NEW.value IS NOT 'retired'
BEGIN
    SELECT RAISE(ABORT,
        'store retirement is one-way: a retired store cannot become current');
END;

CREATE TRIGGER IF NOT EXISTS schema_meta_retirement_is_undeletable
BEFORE DELETE ON schema_meta
WHEN OLD.key IN ('store_status', 'retired_at', 'retired_in_favour_of', 'retired_by')
 AND (SELECT value FROM schema_meta WHERE key = 'store_status') = 'retired'
BEGIN
    SELECT RAISE(ABORT,
        'a retirement record cannot be deleted: this store is retired');
END;
"""


class StoreRetired(RuntimeError):
    """The store was opened, and it says it is no longer the current one."""

    def __init__(self, successor: str | None) -> None:
        self.successor = successor
        where = f" in favour of {successor}" if successor else ""
        super().__init__(
            f"this store has been retired{where}; it is no longer authoritative. "
            "Point at the successor store rather than reading this one."
        )

    @property
    def public_summary(self) -> str:
        return "This task store has been retired and is no longer authoritative."


class StoreCannotProveItsStatus(RuntimeError):
    """The store lacks the trigger, so absence of a retirement proves nothing.

    THE THIRD STATE, and the reason it is not pedantry. The installed 0.18.0
    client contains zero CREATE TRIGGER statements, so a store it initialised
    carries no guards at all. On such a store a stale writer can erase a
    retirement exactly as it erases ``schema_version``. "No retirement found"
    then has two indistinguishable causes -- never retired, or erased -- and
    "I could not tell" must be a refusal, never a cheerful default.
    """

    @property
    def public_summary(self) -> str:
        return "This task store cannot prove whether it is still current."


def read_status(rows: dict[str, str], trigger_names: set[str]) -> str:
    """Decide the store's status from its metadata and its installed guards.

    Pure: takes what was read rather than doing the reading, so every branch is
    testable without a database.

    Three outcomes, deliberately -- two would be a bug:
      * retirement present            -> raise :class:`StoreRetired`
      * guard absent                  -> raise :class:`StoreCannotProveItsStatus`
      * guard present, no retirement  -> ``STATUS_CURRENT``

    Order matters: an explicit retirement is believed even on a store that
    cannot enforce it. A retirement someone took the trouble to write is
    evidence; its absence on an unguarded store is not.
    """
    if rows.get("store_status") == STATUS_RETIRED:
        raise StoreRetired(rows.get("retired_in_favour_of"))
    if not set(TRIGGER_NAMES).issubset(trigger_names):
        raise StoreCannotProveItsStatus()
    return STATUS_CURRENT


# EOF
