#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The physical LADDER: what version the store's ARTIFACTS say it is.

Extracted from ``_schema_shape`` unchanged. This is the half that does not
depend on anybody's honesty -- a client cannot claim a column it did not add --
and it is deliberately independent of :mod:`scitex_cards._schema_floor`, which
handles the STAMP. Neither imports the other; both are read together by
:func:`~scitex_cards._schema_shape.observed_version`, which is what reconciles
them into a verdict.

ADDING A RUNG IS PART OF BUMPING ``SCHEMA_VERSION``, NOT A FOLLOW-UP. See the
v11 and v12 entries below: a bumped version with no matching rung leaves
``observed`` permanently one behind both stamps, which ``schema_already_current``
reads as "not current", which re-runs the full DDL on every open from ~90
containers -- measured elsewhere in this package as 11 of 12 concurrent opens
failing with ``DeadlockDetected`` on ``pg_proc``.
"""

from __future__ import annotations

from ._schema_probe import has_column, has_table, has_trigger

__all__ = ["SHAPE_LADDER", "LADDER_FLOOR"]

#: version -> the physical artifact that migration installed.
#:
#: This is a LADDER, not a lookup: version N is observed only when every rung
#: up to N is present. A store carrying v7's trigger but missing v6's column
#: is not "v7 with a gap", it is a store whose migration chain broke, and
#: reporting 7 for it would hide exactly the corruption worth finding.
#:
#: It starts at 5 deliberately. v1-v4 left no artifact this can distinguish
#: (v4's changes went into the fresh-database-only script -- see the NOTE in
#: ``init_schema``), so a store below 5 reads as UNKNOWN rather than being
#: assigned a number this module cannot actually justify.
SHAPE_LADDER: tuple[tuple[int, str, str, str], ...] = (
    (5, "table", "dm_messages", ""),
    (5, "table", "dm_threads", ""),
    (5, "table", "dm_receipts", ""),
    (5, "table", "dm_thread_member_events", ""),
    (6, "column", "tasks", "revision"),
    (7, "trigger", "tasks_bump_revision", ""),
    # v8 — the notification rail's columns. `msg_id` is the rung rather than
    # `confirmed_at` only because it is the first of the three; all three land in
    # one migration, so any of them would place the store equally well.
    (8, "column", "notifications", "msg_id"),
    (9, "column", "notifications", "seq"),
    # v10 — the sync columns. `row_uuid` is the rung rather than any of the
    # other four because it is the one that cannot plausibly be added by
    # something else: `revision` and `updated_at` are names a future table might
    # acquire for local reasons, and a rung that another change could satisfy
    # would place a store at v10 that never ran this migration.
    (10, "column", "notifications", "row_uuid"),
    # v11 — the foreign keys a MIGRATED store never received. The rung is the
    # constraint itself rather than a column or a trigger because the constraint
    # IS what that migration installs, and this ladder is deliberately built on
    # physical artifacts rather than stamps.
    #
    # THIS RUNG IS NOT OPTIONAL AND ITS ABSENCE IS NOT COSMETIC. `SCHEMA_VERSION`
    # became 11 in the same change; without a rung to match, `observed` would
    # stop at 10 while both stamps read 11, giving STAMP_IS_HIGH forever. That is
    # not a stale number — `schema_already_current` treats any disagreement as
    # "not current", so EVERY open from ~90 containers would re-run the full DDL,
    # which is precisely the pg_proc contention this module's own measurements
    # record as 11 of 12 concurrent opens failing with DeadlockDetected. A
    # missing ladder rung would have converted a version bump into a fleet-wide
    # deadlock generator.
    #
    # `task_comments.task_id` is the rung rather than any of the other three
    # because it is the constraint over the largest child table (8421 rows on
    # 2026-08-10), so it is the one whose absence matters most and the one no
    # unrelated change would install by coincidence.
    (11, "foreign_key", "task_comments", "task_id"),
    # v12 — the sync columns on the two tables the peer sync actually MOVES.
    # v10 gave those five columns to `notifications`, which that sync never
    # reads; v12 gives them to `tasks` and `task_comments`, which it does.
    #
    # THE RUNG IS ADDED IN THE SAME CHANGE AS THE VERSION BUMP, for the reason
    # the v11 comment above spells out at length. That warning was written to
    # be acted on rather than admired, so it is.
    #
    # `tasks.row_uuid` is the rung, by the same test v10 applied to
    # `notifications`: of the five columns it is the one no unrelated change
    # would install by coincidence. `revision` is disqualified outright --
    # `tasks` has carried it since v6, so it would place every v6 store at v12
    # -- and `updated_at` / `deleted_at` are names any table might acquire for
    # purely local reasons. A rung something else can satisfy does not measure
    # the migration, it measures a coincidence.
    (12, "column", "tasks", "row_uuid"),
    # v13 is measured by `reopened_at`, NOT by `is_deleted` or `completed_at`,
    # and the choice follows the rule the v12 note above states: a rung that
    # something else can satisfy measures a coincidence, not the migration.
    # `is_deleted` is exactly the family that note warns about -- a name any
    # table might acquire for purely local reasons -- and `completed_at` is
    # nearly as generic. `reopened_at` is specific to this rung's reason for
    # existing (a lifecycle that must be able to go backwards without lowering
    # a monotone stamp), so nothing else plausibly grows it by accident.
    (13, "column", "tasks", "reopened_at"),
)

#: The lowest version this module can justify from physical evidence.
LADDER_FLOOR = 5


def _has_table(conn, name: str) -> bool:
    """Delegated so the ladder reads the right catalogue on either backend."""
    return has_table(conn, name)


def _has_trigger(conn, name: str) -> bool:
    """Delegated: sqlite_master does not exist on PostgreSQL, and a rung that
    cannot be seen is reported ABSENT -- which downgrades the observed version
    rather than erroring, the quiet direction."""
    return has_trigger(conn, name)


def _has_column(conn, table: str, column: str) -> bool:
    """Delegated for the same reason as the two above: ``PRAGMA table_info``
    does not exist on PostgreSQL, so every COLUMN rung of the ladder was
    unreadable there -- and an unreadable rung reads as ABSENT, which reports
    the store OLDER than it physically is."""
    if not _has_table(conn, table):
        return False
    return has_column(conn, table, column)


def _rung_present(conn, kind: str, name: str, extra: str) -> bool:
    if kind == "table":
        return _has_table(conn, name)
    if kind == "trigger":
        return _has_trigger(conn, name)
    if kind == "column":
        return _has_column(conn, name, extra)
    if kind == "foreign_key":
        from ._db_foreign_keys import foreign_key_is_deferred  # noqa: PLC0415

        return foreign_key_is_deferred(conn, name, extra)
    raise ValueError(f"unknown ladder rung kind: {kind!r}")

# EOF
