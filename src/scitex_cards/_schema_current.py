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



def _behind_explains(shape: Any, schema_version: int) -> bool:
    """Is this disagreement fully explained by the client being out of date?

    True only for a STAMP_IS_HIGH store whose EVERY stamp sits above the version
    this client would assert. Then the store is unambiguously ahead, this
    client's DDL knows no rung that could change it, and running it can only
    take ShareRowExclusiveLock on ``pg_proc`` for nothing.

    IT DOES NOT WEAKEN THE GUARANTEE, because the guarantee is not carried here.
    What must never be skipped without proof is the GUARD TRIGGERS, and those are
    checked separately against the catalogue further down on every open. A client
    that is merely old, against a store that is fully guarded, is the one case
    where asserting the schema is pure contention.

    EVERY OTHER DISAGREEMENT STILL REFUSES, and the boundaries are deliberate:

    * STAMP_IS_LOW is a store whose rungs ran past its stamp. That IS the repair
      the migration chain exists for, and it is not this.
    * Stamps that disagree WITH EACH OTHER (``min`` below, not ``max``) leave one
      of them at or under this client's version, so the store is not provably
      ahead and the conservative branch is correct.
    * A current or ahead client seeing STAMP_IS_HIGH is a genuine anomaly -- it
      can read every rung it would assert, so a higher stamp is unexplained --
      and keeps running the DDL.
    """
    from ._schema_shape import ShapeAgreement  # noqa: PLC0415 -- import cycle

    if shape.agreement is not ShapeAgreement.STAMP_IS_HIGH:
        return False
    stamps = [
        stamp
        for stamp in (shape.stamped_meta, shape.stamped_pragma)
        if stamp is not None
    ]
    return bool(stamps) and min(stamps) > schema_version


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

    # BEHIND, not DIFFERENT. This was `!=` until 2026-08-02, and the difference
    # is the whole bug: `!=` fails in BOTH directions, so a client OLDER than the
    # store re-ran the full DDL on every connection.
    #
    # For a client that is AHEAD, running the DDL is the point -- it migrates the
    # store up. For one that is BEHIND it is worse than useless: a v7 client's
    # DDL only knows rungs 1..7, the store is already at v9, so the statements
    # change nothing AND take ShareRowExclusiveLock on pg_proc every single time
    # a connection opens. It cannot help and it can only serialise.
    #
    # MEASURED, and this is the failure it produces at fleet width:
    #     deadlock detected ... Process A waits for ShareLock on transaction N
    #     Process B waits for ShareRowExclusiveLock on relation pg_proc
    # Four samples on 2026-08-02, three of them hit by this agent's own
    # update_task and add_task calls while writing the card that describes it.
    # Two independent reporters had blamed row contention between concurrent
    # writers; the real antagonist is any stale client OPENING A CONNECTION
    # anywhere in the fleet, which is why the correlation with a co-writer
    # looked convincing and pointed at the wrong layer.
    #
    # The stale population is not hypothetical: agent containers measured at
    # SCHEMA_VERSION 7 against a store stamped 9, on more than one image vintage.
    #
    # `observed is None` stays a REFUSAL rather than becoming a comparison. It
    # means the store sits below the ladder floor and genuinely cannot be placed
    # (ShapeAgreement.UNKNOWN), and `None < int` would raise rather than decide.
    # Unknown is not "current"; it falls through to the DDL, which is the
    # conservative branch.
    if shape.observed is None or shape.observed < schema_version:
        return False
    # The physical rungs and the stamp must tell the same story. A disagreement
    # is precisely the state the migration chain exists to repair, so it must
    # never take the fast path -- EXCEPT for the one disagreement that is not a
    # repair situation at all, which is the same behind-client this function was
    # already fixed once to let through.
    #
    # THE 2026-08-02 FIX WAS ONE LINE SHORT, measured on the live board
    # 2026-08-31. That change made the version comparison `<` so a client BEHIND
    # the store would stop re-running DDL it cannot possibly need. It works: the
    # comparison above passes. Then this check rejected the very same client one
    # line later, for the very same underlying reason, and the deadlocks
    # continued:
    #
    #     deployed client SCHEMA_VERSION 12, observed 12, all 9 triggers present
    #     shape.agreement -> STAMP_IS_HIGH  ->  False  ->  full DDL, every open
    #
    # STAMP_IS_HIGH IS THE NORMAL STATE FOR A BEHIND-CLIENT, not a symptom. Its
    # physical-rung reader only knows the rungs its own version defines, so it
    # can never observe more than that, while the stamp was written by a newer
    # client. Every client the fleet has not yet upgraded therefore reports this
    # disagreement forever, and the fleet always contains such clients.
    if shape.agreement is not ShapeAgreement.AGREES and not _behind_explains(
        shape, schema_version
    ):
        return False
    try:
        present = trigger_names(conn)
    except Exception:
        # An unreadable catalogue is not a current schema.
        return False
    return REQUIRED_GUARD_TRIGGERS <= present
