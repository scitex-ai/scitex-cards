#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Is this store ALREADY in the shape we would assert?

The gate that lets :func:`scitex_cards._db.init_schema` assert the schema once
per STORE instead of once per OPEN.

WHY THIS EXISTS
---------------
On SQLite, re-running ``CREATE TABLE IF NOT EXISTS`` and the trigger scripts on
every connection was very nearly free. Against a shared PostgreSQL server it is
DDL against the system catalogues, and ``CREATE OR REPLACE FUNCTION`` rewrites
the ``pg_proc`` row every time — it is NOT a no-op when the definition already
matches.

Measured on the live store 2026-08-01 with the entire fleet STOPPED and only
four host daemons connected::

    pg_proc.xmin, sampled every 10s while idle:
      t+10  all 9 trigger functions  5069 -> 5075
      t+20  all 9 trigger functions  5075 -> 5082
      t+30  all 9 trigger functions  5082 -> 5087

and concurrency did not survive it::

     4 simultaneous open_db  ->  at least 1 deadlock
    12 simultaneous open_db  ->  11 of 12 failed, DeadlockDetected

Two clients replacing the same function at the same time contend on the
catalogue, and PostgreSQL resolves it by killing one. ``_db`` already notes that
"~90 containers call init_schema on every connection"; at that width this stops
being a slow path and becomes a broken one.

CONSERVATIVE BY CONSTRUCTION
----------------------------
Every uncertain answer is ``False``, which falls through to the full DDL. A
wrong ``False`` costs one redundant assertion — exactly today's behaviour. A
wrong ``True`` would leave a store unguarded while the client believed it had
guarded it, so nothing here INFERS presence; it verifies it against the
catalogue on every open. Only the WRITE is skipped, never the check.
"""

from __future__ import annotations

from typing import Any

__all__ = ["REQUIRED_GUARD_TRIGGERS", "schema_already_current"]

#: The guard triggers that must exist before the per-open DDL may be skipped.
#: Not decoration and not performance furniture: the retirement pair enforces
#: one-way retirement AND is what lets a client prove the store it opened is
#: current (see ``_store_retirement`` — a store that cannot prove it is current
#: is refused, so a missing guard is a correctness failure, not a lint); the
#: floor trigger is the engine-side copy of the never-regress rule, which binds
#: clients that predate the client-side floor; the rest are the append-only
#: guarantees the DM rail rests on.
REQUIRED_GUARD_TRIGGERS: frozenset[str] = frozenset(
    {
        "schema_meta_retirement_is_one_way",
        "schema_meta_retirement_is_undeletable",
        "schema_meta_version_never_regresses",
        "tasks_bump_revision",
        "dm_messages_immutable",
        "dm_messages_no_delete",
        "dm_receipts_no_delete",
        "dm_threads_no_delete",
        "dm_thread_member_events_no_delete",
    }
)


def schema_already_current(conn: Any, shape: Any, schema_version: int) -> bool:
    """True only when the store provably needs no DDL from this client.

    Parameters
    ----------
    conn
        An open store connection.
    shape
        The :class:`~scitex_cards._schema_shape.SchemaShape` already read by the
        caller — passed in rather than re-read, so this costs one catalogue
        query rather than two.
    schema_version
        The version this client would assert.

    Returns
    -------
    bool
        ``True`` to skip the DDL, ``False`` to run it.
    """
    from ._schema_probe import trigger_names  # noqa: PLC0415 -- import cycle
    from ._schema_shape import ShapeAgreement  # noqa: PLC0415 -- import cycle

    if shape.observed != schema_version:
        return False
    # The physical rungs and the stamp must tell the same story. A disagreement
    # is precisely the state the migration chain exists to repair, so it must
    # never take the fast path.
    if shape.agreement is not ShapeAgreement.AGREES:
        return False
    try:
        present = trigger_names(conn)
    except Exception:
        # An unreadable catalogue is not a current schema.
        return False
    return REQUIRED_GUARD_TRIGGERS <= present
