#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_cards/_db_lifecycle_columns.py
"""Schema rung v12 -> v13: the lifecycle facts leave the merged document.

WHY THESE THREE COLUMNS EXIST, and why a JSON key could not do the job.

``_log_meta.deleted_at`` and ``_log_meta.completed_at`` live INSIDE
``card_json``. ADR-0018 D1 made that document canonical under
``MergeRule.LAST_WRITER_WINS``, and last-writer-wins replaces a document
WHOLE. So a later edit on another host carries its own document, the
tombstone is simply not in it, and the card is live again on every host --
a resurrection with no error and no conflict, because from the merge's
point of view the rule worked. The same mechanism loses a completion.

A fact that must survive a whole-document overwrite has to be merged
INDEPENDENTLY of that document, which means it has to be its own column.

OPERATOR RULING, 2026-08-26, which is what settled the delete half
(Telegram, verbatim): 「削除ですけど、基本的に一度データベースに入れたものは
削除しないで削除と言うフラグで対応してください」 -- once a row is in the
database it is never deleted; a delete FLAG handles it. Upstream already
agrees in as many words: ``FieldRole.HIDE_FLAG`` is documented as "The
soft-delete marker. Nothing is ever deleted; this is how a row leaves the
default view."

THE FLAG IS BOOL BECAUSE THE PRIMITIVE REFUSES ANYTHING ELSE.
``scitex_dev.store._policy`` validates a HIDE_FLAG field at construction:
``kind`` must be ``FieldKind.BOOL`` and ``merge`` must be
``LAST_WRITER_WINS``. That is why this rung adds ``is_deleted`` rather than
declaring the existing ``deleted_at TEXT`` -- a timestamp cannot be the
hide flag. ``deleted_at`` stays exactly as it is, as ordinary audit data
beside the flag.

AND THE FLAG MUST BE WRITTEN THROUGH THE ORDINARY WRITE PATH -- never
through ``Store.hide()`` / ``unhide()``. Measured against installed
scitex-dev 0.56.6: ``_apply.py:79-83`` sets ``hidden`` UNCONDITIONALLY for
``OpKind.HIDE`` / ``OpKind.UNHIDE``, with no HLC comparison, unlike the
``OpKind.UPSERT`` branch immediately above it. Two stores given the same
two concurrent ops in opposite arrival orders end up disagreeing
permanently (``is_hidden=False`` vs ``True``), while the same intent
expressed as upserts on this column converges in both orders. Tracked as
``dev-hide-unhide-ops-are-arrival-order-dependent-and-diverge-20260826``.

WHY ``completed_at`` AND ``reopened_at`` ARE A PAIR. Completion needs to
survive a stale peer's document, so it wants a monotone rule
(``MergeRule.MAX``). But a monotone rule cannot be lowered, and reopening
a card is legitimate -- upstream makes exactly this objection when it
forbids ``MAX`` on a hide flag, "MergeRule.MAX in particular would make a
hide permanent". Two monotone stamps avoid the deadlock instead of trading
it: each only ever moves forward, and the presented state derives from
whichever is later, so reopening is an ordinary write rather than an
attempt to undo one.

``MAX`` ON TEXT IS LEXICOGRAPHIC, WHICH IS THE TRAP THIS PACKAGE HAS
ALREADY MEASURED. ``_db_sync_columns`` records it for ``status``: "a
locally ``cancelled`` card loses to a stale peer's ``in_progress`` ...
439 status forks, 303 of them a locally-terminal ``done``/``cancelled``
the peer still believes active". These two columns are safe from that
ONLY because they hold timestamps, where lexicographic order over
fixed-width ISO-8601 UTC coincides with chronological order. That is a
CONSTRAINT ON WRITERS, not a property of the column: a mixed corpus
(``+09:00`` offsets, variable fractional precision, a bare date) breaks
``MAX`` silently and in the same way. The status LABEL itself is still not
promoted, and must not be until ``MergeRule.LATCH`` lands upstream.

NO BACKFILL, DELIBERATELY, and this follows the v11 -> v12 precedent
rather than inventing a policy. That rung states the constraint plainly:
"NO TABLE REWRITE, WHICH IS A CONSTRAINT AND NOT AN ASPIRATION. This runs
on every ``open_db`` from ~90 containers, against tables holding 4,824 and
10,738 rows." Existing rows therefore get NULL, which is the honest value
-- nobody has yet decided how a historical ``_log_meta.completed_at``
should map onto the column, and inventing one would make the gap
unrecoverable BY LOOKING CORRECT. The fill is a later change that can
argue for how, exactly as ``row_uuid`` was left.

NO BACKEND BRANCH. Plain ``ALTER TABLE ADD COLUMN`` is understood by both
SQLite and PostgreSQL, and no trigger or function is installed here, so
branching would add a difference between the two stores that nothing
requires. If a fill trigger is added later it MUST take that branch (see
``_migrate_v9_to_v10``, which skips its trigger on SQLite because
``json_object()`` needs 3.38 and the live host runs 3.37.2); the column
set must not.
"""

from __future__ import annotations

from typing import Any

from ._db_migrations import table_columns

__all__ = ["LIFECYCLE_COLUMNS", "LIFECYCLE_TABLE", "_migrate_v12_to_v13"]


#: The table that owns a card's lifecycle. ``task_comments`` is deliberately
#: NOT included: a comment is append-only and never reopened, and its own
#: ``deleted_at`` is not contested by a whole-document overwrite the way a
#: card's is. Adding columns there would be shape nobody asked for.
LIFECYCLE_TABLE = "tasks"

#: ``(column, sql_type)``, applied one at a time against the physical shape.
#:
#: ``BOOLEAN`` is written rather than ``INTEGER`` because PostgreSQL has a
#: real boolean type and the declared field is ``FieldKind.BOOL``. SQLite has
#: no boolean type but accepts the token and gives it NUMERIC affinity, so the
#: same DDL is valid on both -- the difference surfaces only in what the
#: driver hands back (``0``/``1`` from sqlite3, ``False``/``True`` from
#: psycopg), and both are correct inputs to ``bool()``.
#:
#: No column takes a DEFAULT. A default would silently assert a value for
#: every pre-existing row -- ``is_deleted DEFAULT 0`` reads as "every historical
#: card was affirmatively not deleted", which is a claim nobody measured. NULL
#: says "not recorded", which is what is true.
LIFECYCLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("is_deleted", "BOOLEAN"),
    ("completed_at", "TEXT"),
    ("reopened_at", "TEXT"),
)


def _migrate_v12_to_v13(conn: Any) -> None:
    """Add the lifecycle columns to ``tasks``. Additive, idempotent, no rewrite.

    Checked PER COLUMN against the physical shape rather than against a
    stamp, for the reason the neighbouring rungs give: ``PRAGMA
    user_version`` is a number some code wrote and it can outlive the thing
    it describes -- measured on the live store 2026-07-31, the stamp swung
    5 -> 7 -> 5 while v7's artifacts were physically present throughout.
    ``table_columns`` asks the store what it actually has.
    """
    present = table_columns(conn, LIFECYCLE_TABLE)
    for column, sql_type in LIFECYCLE_COLUMNS:
        if column not in present:
            conn.execute(
                f"ALTER TABLE {LIFECYCLE_TABLE} ADD COLUMN {column} {sql_type}"
            )


# EOF
