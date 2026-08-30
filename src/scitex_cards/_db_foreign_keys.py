#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reconcile a MIGRATED store's foreign keys to the shape the schema declares.

WHY THIS EXISTS
---------------
:mod:`scitex_cards._db_schema_sql` declares FOUR foreign keys inline. On
2026-08-10 the live store was measured and held ONE::

    ALL foreign keys in the entire database: 1
      user_names_user_id_fkey   deferrable=False
    CONTROL: 278 total pg_constraint rows

The 278-row control is what makes "1" a measurement rather than a mis-scoped
near-zero. Three of four declared constraints did not exist on the store the
whole fleet was using, and the one that did enforced nothing — ``users`` and
``user_names`` were both empty, while the three ABSENT ones would have covered
tasks 3731, task_comments 8421, task_edges 189, task_roles 125. So the live
store had ZERO enforced referential integrity on every table holding data.

The cause is the hole this chain already documents: ``CREATE TABLE IF NOT
EXISTS`` is a no-op on an existing table, so inline ``REFERENCES`` reaches
FRESH stores only. A store created before those clauses were written keeps its
original shape forever unless something ALTERs it. That is the same trap
:func:`~scitex_cards._db_migrations._migrate_v1_to_v2` warns about, and this is
its second instance.

WHY THE TARGET IS DEFERRABLE
----------------------------
``NOT DEFERRABLE`` in the declaration was never a decision — it is the default
you get from writing an inline ``REFERENCES`` without thinking about ordering,
and it was written before directed replay existed as a requirement. Under
replay a foreign key is an ORDERING constraint, and a child arriving before its
parent must not fail. #796 changed the declaration to
``DEFERRABLE INITIALLY DEFERRED`` so fresh stores are born correct; this rung
moves migrated stores to the SAME shape.

ONE TARGET, TWO PATHS. That is the whole point, and it is worth stating because
this card already paid for the alternative: two reconcilers aiming at DIFFERENT
shapes for one constraint oscillate forever, each step individually correct,
each log individually reporting success, the oscillation visible only to
someone reading both.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
``task_edges.dst_task_id`` stays UNCONSTRAINED. A forward reference to a card
that does not exist yet is a SUPPORTED pattern (``_diagram/_mermaid.py`` skips
an unknown dst with a WARN rather than failing). The rule is SRC constrained,
DST not — and measuring ORPHANS=0 on dst is NOT evidence the tolerance is
unused, because a forward reference is transient by construction, so a
between-batches snapshot shows zero necessarily.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "DECLARED_FOREIGN_KEYS",
    "ForeignKeyShape",
    "observe_foreign_key",
    "foreign_key_is_deferred",
    "_migrate_v10_to_v11",
]


#: The foreign keys ``_db_schema_sql`` declares, as
#: ``(table, column, referenced_table, referenced_column)``.
#:
#: ONE list, consulted by this rung, so a fresh store and a migrated store
#: cannot disagree about which constraints exist. Kept in the same order the
#: schema declares them so the two can be read side by side.
#:
#: ``task_edges.dst_task_id`` is ABSENT ON PURPOSE — see the module docstring.
DECLARED_FOREIGN_KEYS: tuple[tuple[str, str, str, str], ...] = (
    ("task_comments", "task_id", "tasks", "id"),
    ("task_edges", "src_task_id", "tasks", "id"),
    ("task_roles", "task_id", "tasks", "id"),
    ("user_names", "user_id", "users", "id"),
)

#: A 64-bit key for the advisory lock this rung serialises on. Arbitrary but
#: FIXED — every client must pick the same number or the lock protects nothing.
_ADVISORY_LOCK_KEY = 0x5C1_7EC_FADE


class ForeignKeyShape:
    """What a foreign key ACTUALLY looks like, as a three-member observation.

    Three states, not two, because "present" is not the question — a constraint
    can exist in the wrong shape, and a probe that only asks "does it exist"
    returns True on both sides of exactly the divergence this rung repairs.
    """

    #: No single-column FK on this (table, column).
    ABSENT = "absent"
    #: Present, but not ``DEFERRABLE INITIALLY DEFERRED``.
    PRESENT_NOT_DEFERRED = "present_not_deferred"
    #: Present and already in the declared shape. Nothing to do.
    PRESENT_DEFERRED = "present_deferred"


#: Observe by TABLE + COLUMN, never by constraint NAME.
#:
#: PostgreSQL's auto-generated ``<table>_<column>_fkey`` coincides with the name
#: a caller picks by convention, so a name-matching probe is correct for exactly
#: as long as they coincide, and SILENTLY ADDS A DUPLICATE when they do not.
#: The (table, column) pair is the fact; the name is one spelling of it.
#:
#: SCOPED TO THE CURRENT SCHEMA. ``relname`` alone is not a table identity —
#: PostgreSQL will happily hold ``public.task_comments`` and
#: ``other.task_comments`` at once, and an unscoped probe would report a
#: constraint from a schema this connection does not even read. It would then
#: skip the repair on the table that needed it, having "found" the constraint
#: somewhere else. ``current_schema()`` is the same resolution the migration's
#: own ``ALTER TABLE`` uses, so probe and repair cannot disagree about which
#: table they mean.
_OBSERVE_SQL = """
SELECT c.conname, c.condeferrable, c.condeferred
  FROM pg_constraint c
  JOIN pg_class t ON t.oid = c.conrelid
  JOIN pg_attribute a
    ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
 WHERE c.contype = 'f'
   AND t.relnamespace = current_schema()::regnamespace
   AND t.relname = ?
   AND a.attname = ?
   AND array_length(c.conkey, 1) = 1
"""


def _column(row: Any, name: str, position: int) -> Any:
    """One column of a row, whatever shape the row is. BY NAME FIRST.

    THIS FUNCTION EXISTS BECAUSE ITS ABSENCE SHIPPED A FLEET-WIDE OUTAGE.
    v11 originally read ``row[0], row[1], row[2]``. That is valid on a plain
    driver cursor and RAISES ``KeyError: 0`` on psycopg's ``dict_row`` — which
    is exactly what :func:`scitex_cards._db.connect` uses, because the rest of
    the store reads columns by name.

    The migration runs from ``init_schema``, i.e. on EVERY ``open_db``. So the
    positional read did not fail once: it failed the migration, which failed
    the open, which made the board permanently unreadable on any host that took
    0.37.0. Reported by scitex-agent-container, who halted distribution — 3 of 3
    runs, `scitex-cards summary` dead on 0.37.0 and fine on 0.36.0.

    THE PACKAGE ALREADY KNEW. ``_backend_connect`` documents at :163 that
    ``dict_row`` "supports only the [name]", and
    :func:`scitex_cards._schema_probe._sole_value` exists for precisely this
    hazard, with a docstring naming ``KeyError: 0``. New code reproduced a
    documented trap because the documentation lived next to the OTHER readers.

    Name first, position as the fallback, so the accessor is correct on all
    three row shapes rather than on whichever one the author last tested.
    """
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return row[position]


def observe_foreign_key(conn: Any, table: str, column: str) -> tuple[str, str | None]:
    """The ACTUAL shape of the FK on ``table.column``, and its name if any.

    Returns ``(ForeignKeyShape.*, constraint_name_or_None)``. Reads the
    catalogue; asserts nothing.

    Columns are read through :func:`_column` — BY NAME. See that function for
    why positional access here took the board down fleet-wide.
    """
    row = conn.execute(_OBSERVE_SQL, (table, column)).fetchone()
    if row is None:
        return ForeignKeyShape.ABSENT, None
    name = _column(row, "conname", 0)
    deferrable = bool(_column(row, "condeferrable", 1))
    deferred = bool(_column(row, "condeferred", 2))
    if deferrable and deferred:
        return ForeignKeyShape.PRESENT_DEFERRED, name
    return ForeignKeyShape.PRESENT_NOT_DEFERRED, name


def foreign_key_is_deferred(conn: Any, table: str, column: str) -> bool:
    """Is ``table.column``'s foreign key present AND initially deferred?

    THE LADDER RUNG FOR v11. It reads the CATALOGUE, which is exact: the
    constraint's own definition, not the text of a ``CREATE TABLE`` somebody
    stored. An earlier version fell back to string-matching stored DDL when the
    catalogue was unavailable, which confirmed that a table declared the column
    and carried SOME deferred foreign key without proving the two were the same
    clause — a looseness that a second FK on one of these tables would have
    silently turned into a wrong answer.
    """
    return observe_foreign_key(conn, table, column)[0] == (
        ForeignKeyShape.PRESENT_DEFERRED
    )


def _migrate_v10_to_v11(conn: Any) -> None:
    """Give a migrated store the foreign keys its schema declares. PostgreSQL.

    Additive and idempotent, like every rung here — but unlike the others this
    one takes real locks on tables the fleet is writing, so two properties are
    enforced rather than hoped for.

    **IT SERIALISES ON AN ADVISORY LOCK, AND IT WAITS RATHER THAN SKIPS.**
    ``init_schema`` runs on every ``open_db`` from ~90 containers, and this
    module's neighbours measured what that width does to concurrent DDL: 12
    simultaneous opens produced 11 ``DeadlockDetected`` failures on ``pg_proc``.
    ``ADD CONSTRAINT`` takes ShareRowExclusive on BOTH tables and validates
    every existing row, so ninety clients attempting it at once is that same
    failure with a bigger blast radius.

    ``pg_advisory_xact_lock`` is taken rather than ``pg_try_advisory_lock``, and
    the difference is load-bearing. A try-lock that gives up would return
    without doing the work — and the caller stamps the new schema version
    REGARDLESS, so ``schema_already_current`` would skip this rung on every
    subsequent open and the constraints would never be added on a store that now
    claims to be current. A silent skip here is not a retry, it is a PERMANENT
    MISS. Waiting costs the first client a few seconds and every other client a
    catalogue read that finds the work already done.

    The lock is transaction-scoped, so it is released by the caller's commit or
    by any rollback. Nothing can leak it.

    **THE ALTER IS ITS OWN ORPHAN CHECK.** An earlier design ran a
    ``SELECT ... WHERE NOT EXISTS`` orphan count and then the ``ALTER``, in one
    transaction on one snapshot. That is redundant: a validating
    ``ADD CONSTRAINT`` scans the table and fails on the first violating row, in
    the same statement, so there is no window between check and act to protect.
    Orphans measured 0 on 2026-08-10 (with an 8421-rows-DO-match control), but
    that number is deliberately NOT relied on here — it proves the migration is
    FEASIBLE, never that it is safe unguarded, and ~90 writers were live during
    the measurement with ``tasks`` moving 3608 -> 3731 mid-check. If a violating
    row exists at execution time the ALTER raises and the transaction rolls
    back, which is the correct outcome and needs no help from a precondition
    that was true minutes ago.

    A CONNECTION THAT IS NOT THE STORE GETS NOTHING HERE, and the early return
    below is a defensive no-op rather than a second code path: the repair is an
    ``ALTER TABLE ADD CONSTRAINT``, which only the store can execute. The
    multi-version fleet is where the divergence was measured and where the
    repair works.
    """
    from ._schema_probe import _is_postgres  # noqa: PLC0415 -- import cycle

    if not _is_postgres(conn):
        return

    # Serialise every client that reaches this rung. See the docstring for why
    # this WAITS instead of trying-and-skipping.
    conn.execute("SELECT pg_advisory_xact_lock(?)", (_ADVISORY_LOCK_KEY,))

    for table, column, ref_table, ref_column in DECLARED_FOREIGN_KEYS:
        shape, name = observe_foreign_key(conn, table, column)
        if shape == ForeignKeyShape.PRESENT_DEFERRED:
            continue
        if shape == ForeignKeyShape.PRESENT_NOT_DEFERRED:
            # Metadata-only: no table scan, no row rewrite. The constraint
            # already holds; only its timing changes.
            conn.execute(
                f'ALTER TABLE {table} ALTER CONSTRAINT "{name}" '
                "DEFERRABLE INITIALLY DEFERRED"
            )
            continue
        # ABSENT. The name is chosen to match what PostgreSQL would generate
        # inline, so a fresh store and a repaired one agree on the NAME as well
        # as the shape -- otherwise the two differ in a way a shape test that
        # compares names would report forever.
        conn.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_fkey "
            f"FOREIGN KEY ({column}) REFERENCES {ref_table}({ref_column}) "
            "ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED"
        )

# EOF
