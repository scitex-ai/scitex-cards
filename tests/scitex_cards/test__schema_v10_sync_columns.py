#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v10 makes ``notifications`` syncable, and its payload impossible to omit.

TWO CHANGES, ONE REASON. A fact that must be on EVERY row cannot be left to the
writer, because the writers are never all the same version: measured 2026-07-30
the fleet ran 0.13.5 / 0.17.5 / 0.18.0 / 0.22.0 at once, and v7 already
concluded from that "application-side incrementing would require every one of
~90 agent containers to be current for the lock to mean anything".

THE SYNC COLUMNS ARE HERE FROM CREATION because retrofitting them onto a table
that is already being replicated is a rewrite — every existing row would need an
origin and a uuid nobody ever observed, and there is no honest value to invent.

THE PAYLOAD TRIGGER ends a loss class rather than another instance of it. Three
separate writers omitted ``record_json`` (``_inbox_postgres.enqueue``,
``_inbox_carry.carry_rows``, ``_inbox_migrate_postgres``), each found only after
it had taken every card write down fleet-wide. Four MORE payload-less rows
appeared on the live rail AFTER the enqueue fix merged, because merged is not
deployed — every one an undelivered operator DM.

THE TEST THAT CARRIES THE MOST WEIGHT is fresh-vs-migrated shape agreement.
This repo has been bitten by a fresh store and a migrated store disagreeing on
shape; a new column has to be added in two places and checking one of them is
how that divergence happens again.
"""

from __future__ import annotations

import pytest

from scitex_cards._db import SCHEMA_VERSION, connect
from scitex_cards._db_migrations import (
    NOTIFICATION_SYNC_COLUMNS,
    _migrate_v9_to_v10,
    table_columns,
)
from scitex_cards._db_schema_sql import SCHEMA_SQL
from scitex_cards._ddl import execute_ddl
from scitex_cards._schema_shape import SHAPE_LADDER

_TABLE = "notifications"
_NAMES = tuple(name for name, _ in NOTIFICATION_SYNC_COLUMNS)

#: The v9 shape — everything the fresh script declares EXCEPT the new columns.
_V9_NOTIFICATIONS = """
CREATE TABLE notifications (
    id           TEXT PRIMARY KEY,
    recipient_id TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    card_id      TEXT,
    body         TEXT,
    actor        TEXT,
    ts           TEXT NOT NULL,
    seen         INTEGER NOT NULL DEFAULT 0,
    record_json  TEXT,
    msg_id       TEXT,
    pushed_at    TEXT,
    confirmed_at TEXT,
    seq          BIGINT
)
"""


def _empty(new_store, prefix: str, script: str):
    """An empty throwaway store with ``script`` installed through the package.

    ``bootstrap=False``: the harness's per-test store is already at the current
    shape, so a v9 fixture built on it would carry the v10 columns before the
    migration ran and every assertion below would be true before the act.

    These fixtures used to be an in-memory scratch database. That cannot stand
    in for the store any more, and not merely on principle: every shape
    question here is asked through ``table_columns``, which reads
    ``information_schema`` and has no second dialect — so the old fixtures did
    not measure a v9-shaped store, they errored on the probe.
    """
    conn = connect(new_store(prefix, bootstrap=False))
    execute_ddl(conn, script)
    conn.commit()
    return conn


@pytest.fixture
def v9_store(new_store):
    """A store at the v9 shape — the thing the migration must upgrade."""
    conn = _empty(new_store, "cards_v10_v9shape", _V9_NOTIFICATIONS)
    yield conn
    conn.close()


@pytest.fixture
def fresh_store(new_store):
    """A store created by the CURRENT fresh-create script."""
    conn = _empty(new_store, "cards_v10_fresh", SCHEMA_SQL)
    yield conn
    conn.close()


class TestTheVersionWasBumped:
    def test_schema_version_is_at_least_ten(self):
        """A floor, not equality — v11 must not fail this test."""
        # Arrange
        minimum = 10

        # Act
        actual = SCHEMA_VERSION

        # Assert
        assert actual >= minimum


class TestTheFreshCreatePathHasTheColumns:
    @pytest.mark.parametrize("column", _NAMES)
    def test_a_fresh_store_declares_the_sync_column(self, fresh_store, column):
        # Arrange
        table = _TABLE

        # Act
        present = table_columns(fresh_store, table)

        # Assert
        assert column in present


class TestTheMigrationPathHasTheColumns:
    @pytest.mark.parametrize("column", _NAMES)
    def test_migrating_a_v9_store_adds_the_sync_column(self, v9_store, column):
        # Arrange: the v9 fixture is defined without any of these columns, so
        # the migration is what has to put them there.
        before = table_columns(v9_store, _TABLE)

        # Act
        _migrate_v9_to_v10(v9_store)

        # Assert
        assert column in table_columns(v9_store, _TABLE) - before

    def test_running_it_twice_is_a_no_op(self, v9_store):
        """Idempotent: every migration in this ladder runs on every open."""
        # Arrange
        _migrate_v9_to_v10(v9_store)
        after_first = table_columns(v9_store, _TABLE)

        # Act
        _migrate_v9_to_v10(v9_store)

        # Assert
        assert table_columns(v9_store, _TABLE) == after_first


class TestTheTwoPathsAgree:
    def test_a_migrated_store_has_the_same_columns_as_a_fresh_one(
        self, v9_store, fresh_store
    ):
        """The divergence this repo has already been bitten by, as a gate.

        Asserted through ``init_schema`` — the FULL chain — rather than through
        this one migration step. The single-step form is a proxy that goes red
        on every subsequent version with nothing actually wrong: v9's copy of
        this test did exactly that when v10 landed, having not inherited the
        lesson its v8 sibling had already written down. A real store reaches the
        current version because ``init_schema`` runs every rung, so that is what
        the invariant should be stated against.
        """
        # Arrange
        from scitex_cards._db import init_schema

        init_schema(v9_store)

        # Act
        migrated = table_columns(v9_store, _TABLE)

        # Assert
        assert migrated == table_columns(fresh_store, _TABLE)


class TestExistingRowsAreNotInvented:
    def test_a_pre_existing_row_gets_a_null_origin_rather_than_a_guess(
        self, v9_store
    ):
        """Nobody observed which node wrote it, so nothing may claim to know."""
        # Arrange
        v9_store.execute(
            "INSERT INTO notifications(id, recipient_id, event_type, ts) "
            "VALUES('n_old', 'agent-x', 'dm', '2026-08-01T00:00:00Z')"
        )

        # Act
        _migrate_v9_to_v10(v9_store)

        # Assert
        row = v9_store.execute(
            "SELECT origin_node FROM notifications WHERE id = 'n_old'"
        ).fetchone()
        assert row["origin_node"] is None

    def test_revision_starts_at_zero_rather_than_null(self, v9_store):
        """A counter that starts NULL cannot be incremented by anyone."""
        # Arrange
        v9_store.execute(
            "INSERT INTO notifications(id, recipient_id, event_type, ts) "
            "VALUES('n_old', 'agent-x', 'dm', '2026-08-01T00:00:00Z')"
        )

        # Act
        _migrate_v9_to_v10(v9_store)

        # Assert
        row = v9_store.execute(
            "SELECT revision FROM notifications WHERE id = 'n_old'"
        ).fetchone()
        assert row["revision"] == 0


class TestTheShapeLadderCanPlaceAV10Store:
    def test_the_ladder_carries_a_v10_rung(self):
        """Without it, every container re-runs the full DDL on every connect.

        ``schema_already_current`` compares the OBSERVED version against
        SCHEMA_VERSION; a missing rung makes observed < 10 forever, so the
        currency gate never closes and ~90 containers assert the schema on every
        single open — the measured ``pg_proc`` deadlock storm.
        """
        # Arrange
        ladder = SHAPE_LADDER

        # Act
        versions = {version for version, _, _, _ in ladder}

        # Assert
        assert SCHEMA_VERSION in versions

    def test_the_v10_rung_names_a_column_this_migration_adds(self):
        """A rung another change could satisfy would place a store falsely."""
        # Arrange
        rungs = [row for row in SHAPE_LADDER if row[0] == 10]

        # Act
        _, kind, table, column = rungs[0]

        # Assert
        assert (kind, table, column) == ("column", _TABLE, "row_uuid")


class TestThePayloadTriggerIsInstalled:
    """The guard the rung exists for, asserted where it actually runs.

    THIS REPLACES A TEST OF THE OTHER LEG. The class here was
    ``TestTheSqliteLegDoesNotInstallTheTrigger``: the rung has a
    ``if not _is_postgres(conn): return`` branch, and that branch was checked
    by building a scratch SQLite database and reading ``sqlite_master`` back.
    There is one storage engine now, so that branch has no caller and no
    constructible fixture — the only way to reach it is to open something that
    is not the store, which every door in this package refuses. The behaviour
    was not weakened, it was ABOLISHED, so the test of it is deleted rather
    than converted.

    What is kept is the half that always mattered and was never asserted
    anywhere in this repository: on the store, the trigger is THERE. A rung
    whose whole purpose is "no client of any version can enqueue a
    payload-less row" is worth nothing if the guard silently fails to install.
    """

    def test_the_payload_trigger_is_created_on_the_store(self, v9_store):
        # Arrange
        from scitex_cards._db_migrations import NOTIFICATION_PAYLOAD_TRIGGER
        from scitex_cards._schema_probe import trigger_names

        before = trigger_names(v9_store)

        # Act
        _migrate_v9_to_v10(v9_store)

        # Assert -- a TRANSITION: the empty v9 fixture proves the rung put it
        # there, where "it is present at the end" would also pass on a store
        # that already carried it.
        assert NOTIFICATION_PAYLOAD_TRIGGER in trigger_names(v9_store) - before


# EOF
