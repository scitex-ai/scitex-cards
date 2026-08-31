#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Timestamp: "2026-08-16"
# File: ./src/scitex_cards/_db_sync_columns.py

"""Schema rung v11 -> v12: the mandated sync columns on the two SYNCED tables.

WHY THIS RUNG EXISTS. The operator's standing rule is that any table which
crosses a host boundary carries ``origin_node, row_uuid, revision, updated_at,
deleted_at`` FROM CREATION, and that a blind ``ON CONFLICT DO UPDATE`` is
prohibited. Measured on the live store 2026-08-16::

    tasks           4,824 rows    1/5   ``revision`` only (v6)
    task_comments  10,738 rows    0/5   none
    notifications   2,731 rows    5/5   installed by v9 -> v10

So the rule held on exactly the table the peer sync does NOT move.
``~/.local/bin/scitex-cards-sync.py`` reads ``tasks`` (:59, :65) and
``task_comments`` (:73, :80); it never touches ``notifications``. This rung
closes that inversion.

WHAT IT UNBLOCKS, STATED NARROWLY SO IT IS NOT OVERSOLD. Without these columns
a per-row merge has nothing to order by, so the merge is UNDEFINED rather than
merely wrong: ``scitex_dev.store``'s ``merge_field`` needs a stamp, and the only
per-row time column ``tasks`` has today is ``last_activity`` -- which COMMENTING
touches, so wiring last-writer-wins to it would make chatter outrank decisions.

WHAT IT DELIBERATELY DOES NOT DO, AND WHY THAT IS NOT A HALF-MEASURE. It adds
the columns; it does not populate them. That is the same state
``notifications`` is in today -- five columns present, ``origin_node`` NULL on
every row -- and that state IS a known defect, so it must be named rather than
inherited quietly: a synced row is currently indistinguishable from a local one.

The reason population is a separate change is that it is not mechanical.
``origin_node`` is SUBJECT -- which machine the row is ABOUT -- and not
PROVENANCE, which node relayed it here. The two coincide only until the first
relay, so filling it with "the node that wrote this row" is provenance wearing a
subject's name: it would look correct in every test and fail in exactly the
cross-host case the column exists for. A decision like that belongs in a change
that argues for it, not smuggled into an ALTER TABLE. (Credit: sac, 2026-08-12.)

Existing rows therefore keep NULL, which is the honest value and follows the
v9 -> v10 precedent verbatim: nobody observed which node wrote them, and
inventing one would make the missing data unrecoverable BY LOOKING CORRECT.

THE CONFLICT RULE FOR THIS CLASS, stated here because a blind
``ON CONFLICT DO UPDATE`` is prohibited and a wall clock is never the arbiter.
A card is NOT immutable-plus-latches the way a notification is -- it is edited
repeatedly, by many agents, on many hosts -- so the notification rule does not
transfer and must not be copied by analogy:

``tasks``
    The card DOCUMENT is the unit of merge, per ADR-0018 D1 ("the document is
    canonical, the columns are derived"), under last-writer-wins ordered by an
    HLC and NEVER by ``updated_at``. ``status`` is the one field that wants a
    lifecycle latch instead, and it is deliberately NOT promoted to a column
    with its own rule yet -- ``MergeRule.LATCH`` with a tiered order is still
    upstream. Until it lands, promoting ``status`` would mean declaring a rule
    measured to be wrong: ``MergeRule.MAX`` compares VALUES, so on TEXT it is
    lexicographic, and a locally ``cancelled`` card loses to a stale peer's
    ``in_progress``. Measured against one peer: 439 status forks, 303 of them a
    locally-terminal ``done``/``cancelled`` the peer still believes active.
``task_comments``
    APPEND, element-wise on the comment ``id``. Comments are only ever added,
    so two divergent copies of a thread merge to the UNION of their elements --
    order-independent, commutative, and unable to destroy either side's work.
    This is the one interior-merge path ``scitex_dev.store`` genuinely supports
    (list-valued JSON columns under APPEND/UNION), and the elements already
    carry the unique ``id`` its ``_element_id`` requires.

``updated_at`` is FOR AUDIT, NEVER FOR MERGE, on both tables -- same as v10.
"""

from __future__ import annotations

from typing import Any

from ._db_migrations import table_columns

__all__ = ["SYNC_COLUMNS", "SYNCED_TABLES", "_migrate_v11_to_v12"]

#: The operator's mandated set, with the same SQL types v9 -> v10 gave
#: ``notifications``. Identical on purpose: two spellings of one schema is how
#: a merge rule ends up depending on which table it is reading.
SYNC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("origin_node", "TEXT"),
    ("row_uuid", "TEXT"),
    ("revision", "INTEGER NOT NULL DEFAULT 0"),
    ("updated_at", "TEXT"),
    ("deleted_at", "TEXT"),
)

#: The tables the peer sync actually moves. NOT ``notifications`` -- it already
#: has these columns and the sync script never reads it.
#:
#: ``tasks`` already carries ``revision`` from v6, with a ``tasks_bump_revision``
#: trigger (v7) maintaining it. The per-column guard below leaves it alone; this
#: rung must not re-add or redefine it, because that trigger is what makes the
#: counter mean anything.
SYNCED_TABLES: tuple[str, ...] = ("tasks", "task_comments")


def _migrate_v11_to_v12(conn: Any) -> None:
    """Add the five sync columns to ``tasks`` and ``task_comments``.

    ADDITIVE AND IDEMPOTENT, checked PER COLUMN against the physical shape
    rather than against a stamp. ``table_columns`` is the honest question:
    ``PRAGMA user_version`` is a number some code wrote, and it can outlive the
    thing it describes. Measured on the live store 2026-07-31, the stamp swung
    5 -> 7 -> 5 while v7's artifacts were physically present the entire time.

    NO TABLE REWRITE, WHICH IS A CONSTRAINT AND NOT AN ASPIRATION. This runs on
    every ``open_db`` from ~90 containers, against tables holding 4,824 and
    10,738 rows. That is also why no column takes a volatile DEFAULT: PostgreSQL
    can add a column with a constant default in place, but a volatile one (a
    fresh uuid per row) forces a full rewrite of the table -- so ``row_uuid``
    arrives NULL and is filled by a later change that can argue for how.

    NO BACKEND BRANCH IS NEEDED HERE, and that is worth stating because the
    neighbouring rung has one. ``_migrate_v9_to_v10`` installs a plpgsql
    trigger, which only the store can accept. This rung installs no trigger and
    no function -- plain ``ALTER TABLE ADD COLUMN`` is standard -- so branching
    would add a difference between stores that nothing requires. If a fill
    trigger
    is added later it MUST take that branch; the column set must not.
    """
    for table in SYNCED_TABLES:
        present = table_columns(conn, table)
        for column, sql_type in SYNC_COLUMNS:
            if column not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")


# EOF
