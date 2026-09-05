#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v12 gives the mandated sync columns to the two tables the sync actually MOVES.

v10 gave ``origin_node, row_uuid, revision, updated_at, deleted_at`` to
``notifications``. Measured on the live store 2026-08-16, that left::

    tasks           4,824 rows    1/5   ``revision`` only (v6)
    task_comments  10,738 rows    0/5
    notifications   2,731 rows    5/5

and ``~/.local/bin/scitex-cards-sync.py`` reads ``tasks`` (:59, :65) and
``task_comments`` (:73, :80) — never ``notifications``. So the operator's
FROM-CREATION rule was satisfied on exactly the table that does not cross a host
boundary. This rung closes that inversion.

THE TEST THAT CARRIES THE MOST WEIGHT IS NOT ABOUT COLUMNS. It is
``TestTheLadderCannotFallBehindTheVersion``. ``_schema_ladder``'s v11 comment
already explains, at length, that bumping ``SCHEMA_VERSION`` without adding a
matching rung leaves ``observed`` permanently one behind both stamps — which
``schema_already_current`` reads as "not current", which re-runs the full DDL on
every open from ~90 containers, which this package measured as 11 of 12
concurrent opens failing with ``DeadlockDetected`` on ``pg_proc``.

That warning was written in PROSE and nothing enforced it. A rule that must be
remembered is forgotten at exactly the moment it matters, so this file turns it
into a mechanical barrier: the next person to bump the version without adding a
rung gets a red test naming the omission, instead of a fleet-wide deadlock
generator and a warning they were supposed to have read.
"""

from __future__ import annotations

import pytest

from scitex_cards._db import SCHEMA_VERSION, connect
from scitex_cards._db_init_schema import init_schema
from scitex_cards._db_migrations import table_columns
from scitex_cards._db_schema_sql import SCHEMA_SQL
from scitex_cards._db_sync_columns import (
    SYNC_COLUMNS,
    SYNCED_TABLES,
    _migrate_v11_to_v12,
)
from scitex_cards._ddl import execute_ddl
from scitex_cards._schema_shape import SHAPE_LADDER

_NAMES = tuple(name for name, _ in SYNC_COLUMNS)
_PAIRS = tuple((table, column) for table in SYNCED_TABLES for column in _NAMES)

#: The pairs the migration must genuinely ADD, i.e. every pair EXCEPT
#: ``tasks.revision`` — v6 already put that one there.
#:
#: Excluded deliberately rather than folded in with a union, because the
#: obvious spelling of that (``column in after | before``) passes when the
#: migration does nothing at all: a column already present satisfies it whether
#: or not anything ran. That is a test which cannot go red, and this session has
#: already shipped one of those and had to repair it. ``tasks.revision`` gets its
#: own explicit test below instead — asserting it is PRESERVED, which is the
#: property that actually matters for it.
_ADDED_PAIRS = tuple(p for p in _PAIRS if p != ("tasks", "revision"))

#: The v11 shape of the two tables — the FULL column list they carried BEFORE
#: this rung, which is the current fresh script minus the columns v12 adds.
#: ``tasks.revision`` is present because v6 added it; the other four are not.
#:
#: The full list matters and a trimmed one does NOT work: ``SCHEMA_SQL`` builds
#: ``idx_tasks_agent`` and friends, and ``CREATE TABLE IF NOT EXISTS`` will not
#: widen a table that already exists — so an abbreviated fixture makes
#: ``init_schema`` die on ``no such column: agent``, which says nothing about
#: the migration and everything about the fixture. A store that never had these
#: columns is not a store that existed.
_V11_TABLES = """
CREATE TABLE tasks (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    kind           TEXT,
    blocker        TEXT,
    task           TEXT,
    note           TEXT,
    goal           TEXT,
    project        TEXT,
    repo           TEXT,
    host           TEXT,
    agent          TEXT,
    assignee       TEXT,
    scope          TEXT,
    grp            TEXT,
    priority       INTEGER,
    parent         TEXT,
    pr_url         TEXT,
    issue_url      TEXT,
    deadline       TEXT,
    scheduled      TEXT,
    created_at     TEXT,
    last_activity  TEXT,
    started_at     TEXT,
    finished_at    TEXT,
    created_by     TEXT,
    job_id         TEXT,
    command        TEXT,
    deadlines_json TEXT,
    log_meta_json  TEXT,
    row_order      INTEGER,
    card_json      TEXT,
    revision       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE task_comments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    author  TEXT,
    ts      TEXT,
    kind    TEXT,
    text    TEXT NOT NULL
);
"""


def _empty(new_store, prefix: str, script: str):
    """An empty throwaway store with ``script`` installed through the package.

    ``bootstrap=False``: the harness's per-test store is already at the current
    shape, so a v11 fixture built on it would carry the v12 columns before the
    migration ran and every assertion below would be true before the act.

    ``execute_ddl`` rather than a driver-level script runner, and that is the
    part the old in-memory fixtures could not do: the shipped DDL is TRANSLATED
    on the way into the engine (``INTEGER PRIMARY KEY AUTOINCREMENT`` has no
    PostgreSQL spelling, and every inline-body trigger is substituted for its
    plpgsql pair), so a fixture that fed the raw text to another engine was
    exercising a string this package never installs anywhere.
    """
    conn = connect(new_store(prefix, bootstrap=False))
    execute_ddl(conn, script)
    conn.commit()
    return conn


@pytest.fixture
def v11_store(new_store):
    """A store at the v11 shape — the thing this migration must upgrade."""
    conn = _empty(new_store, "cards_v12_v11shape", _V11_TABLES)
    yield conn
    conn.close()


@pytest.fixture
def fresh_store(new_store):
    """A store created by the CURRENT fresh-create script, no migrations run."""
    conn = _empty(new_store, "cards_v12_fresh", SCHEMA_SQL)
    yield conn
    conn.close()


class TestTheLadderCannotFallBehindTheVersion:
    def test_the_top_rung_equals_the_schema_version(self):
        """The barrier that replaces a comment nobody is obliged to read.

        Equality, not ``>=``: a ladder that ran AHEAD of the version would be
        just as broken, reporting a store as newer than any client can build.
        """
        # Arrange
        declared = SCHEMA_VERSION

        # Act
        top_rung = max(version for version, *_ in SHAPE_LADDER)

        # Assert
        assert top_rung == declared

    def test_the_ladder_has_no_gap_below_the_top(self):
        """A ladder is walked upward and stops at the first miss, so a hole
        below the top silently caps every reading at the hole."""
        # Arrange
        floor = min(version for version, *_ in SHAPE_LADDER)

        # Act
        versions = {version for version, *_ in SHAPE_LADDER}

        # Assert
        assert versions == set(range(floor, SCHEMA_VERSION + 1))


class TestTheFreshCreatePathHasTheColumns:
    @pytest.mark.parametrize(("table", "column"), _PAIRS)
    def test_a_fresh_store_declares_the_sync_column(self, fresh_store, table, column):
        """FROM CREATION is the operator's word, so the fresh script carries
        them too — not only the migration that retrofits old stores."""
        # Arrange
        expected = column

        # Act
        present = table_columns(fresh_store, table)

        # Assert
        assert expected in present


class TestTheMigrationPathAddsTheColumns:
    @pytest.mark.parametrize(("table", "column"), _ADDED_PAIRS)
    def test_migrating_a_v11_store_adds_the_sync_column(self, v11_store, table, column):
        # Arrange: the v11 fixture carries `tasks.revision` and nothing else
        # from this set, so anything in the DIFFERENCE was added by the rung.
        before = table_columns(v11_store, table)

        # Act
        _migrate_v11_to_v12(v11_store)

        # Assert
        assert column in table_columns(v11_store, table) - before

    def test_running_it_twice_is_a_no_op(self, v11_store):
        """Idempotent: every rung in this chain runs on EVERY open_db, from
        ~90 containers."""
        # Arrange
        _migrate_v11_to_v12(v11_store)
        after_first = {t: table_columns(v11_store, t) for t in SYNCED_TABLES}

        # Act
        _migrate_v11_to_v12(v11_store)

        # Assert
        assert {t: table_columns(v11_store, t) for t in SYNCED_TABLES} == after_first


class TestItLeavesTheV6RevisionAlone:
    def test_tasks_revision_keeps_its_original_default(self, v11_store):
        """``tasks.revision`` arrived in v6 and is maintained by v7's
        ``tasks_bump_revision`` trigger. Re-adding or redefining it here would
        break the counter that trigger exists to keep meaningful, so the
        per-column guard must skip it rather than overwrite it."""
        # Arrange
        v11_store.execute("INSERT INTO tasks(id, title) VALUES('t1', 'x')")

        # Act
        _migrate_v11_to_v12(v11_store)

        # Assert
        # BY NAME, not by position: the store's rows are dict-shaped and raise
        # ``KeyError: 0`` on an index. The old in-memory fixture accepted both,
        # which is exactly how positional reads survived into the package and
        # cost it three separate crashes on the real server.
        row = v11_store.execute("SELECT revision FROM tasks").fetchone()
        assert row["revision"] == 0


class TestTheTwoPathsAgree:
    @pytest.mark.parametrize("table", SYNCED_TABLES)
    def test_a_migrated_store_ends_with_the_same_columns_as_a_fresh_one(
        self, new_store, table
    ):
        """The divergence this repo keeps getting bitten by, as a gate.

        Asserted through ``init_schema`` — the FULL chain — rather than through
        this one rung, following the lesson v10's copy of this test recorded:
        a single-step proxy goes red on every LATER version with nothing
        actually wrong.

        SCOPED TO THE FIVE SYNC COLUMNS, not to the whole column set. The v11
        fixture declares a deliberately REDUCED ``tasks`` (six columns, not
        thirty), and ``CREATE TABLE IF NOT EXISTS`` will not widen a table that
        already exists — so a migrated store legitimately lacks columns a fresh
        one has, and asserting full equality would be asserting something this
        chain has never promised. What it does promise is that both paths end
        up syncable, and that is what is checked.
        """
        # Arrange
        migrated = _empty(new_store, "cards_v12_agree_mig", _V11_TABLES)
        fresh = connect(new_store("cards_v12_agree_fresh", bootstrap=False))

        # Act
        init_schema(migrated)
        init_schema(fresh)

        # Assert
        try:
            assert set(_NAMES) <= (
                table_columns(migrated, table) & table_columns(fresh, table)
            )
        finally:
            migrated.close()
            fresh.close()


class TestTheSplitForwardsRatherThanShadows:
    @pytest.mark.parametrize(
        "name",
        [
            "SCHEMA_VERSION_FLOOR_TRIGGER",
            "SCHEMA_VERSION_FLOOR_TRIGGER_SQL",
            "SCHEMA_VERSION_DOWNGRADE_KEYS",
            "downgrade_report",
            "stamp_schema_version",
        ],
    )
    def test_the_floor_names_are_the_same_objects(self, name):
        """Identity, not presence. A re-export that SHADOWS rather than
        forwards is indistinguishable from a working one until two callers
        compare instances — which is why the earlier ``_db_mirror`` split was
        verified this way too."""
        # Arrange
        from scitex_cards import _schema_floor, _schema_shape

        # Act
        forwarded = getattr(_schema_shape, name)

        # Assert
        assert forwarded is getattr(_schema_floor, name)

    @pytest.mark.parametrize("name", ["SHAPE_LADDER", "LADDER_FLOOR"])
    def test_the_ladder_names_are_the_same_objects(self, name):
        # Arrange
        from scitex_cards import _schema_ladder, _schema_shape

        # Act
        forwarded = getattr(_schema_shape, name)

        # Assert
        assert forwarded is getattr(_schema_ladder, name)


# EOF
