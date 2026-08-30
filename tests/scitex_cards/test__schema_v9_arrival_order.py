#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v9 gives ``notifications`` an arrival-order column, on BOTH creation paths.

WHY THE COLUMN EXISTS. The the retired engine inbox delivers and acks by ``ORDER BY rowid``
at five call sites, and ``rowid`` has no PostgreSQL equivalent. Moving the rail
without replacing it loses delivery order SILENTLY -- the SQL stays valid on
both engines and every test stays green, which is the worst shape a correctness
regression can take.

WHY NOT ``ORDER BY ts, id``, which the export path already uses for this table
and justifies as "on append-only tables it is the same order rowid produced".
Measured on the live rail 2026-08-02 and it is not:

    3496 rows; 1256 positions differ from rowid order
    1051 same-second ties, and 8 genuine TIMESTAMP INVERSIONS
    e.g. a row stamped 2026-08-02T00:00:00Z followed by one stamped
         2026-08-01T18:07:41Z -- six hours earlier

``enqueue(ts=...)`` takes a CALLER-SUPPLIED timestamp, so ``ts`` is not an
insert-time clock. The export only needs a REPRODUCIBLE order and is fine;
delivery needs the ARRIVAL one and is not.

THE TEST THAT CARRIES THE MOST WEIGHT is the fresh-vs-migrated shape agreement.
This repo has already been bitten by a fresh store and a migrated store
disagreeing on shape, which is why NOTIFICATION_RAIL_COLUMNS exists as one list
consulted by both paths. A new column has to be added in two places, and
checking only one of them is how the divergence happens again.
"""

from __future__ import annotations

import pytest

from scitex_cards._db import SCHEMA_VERSION, connect
from scitex_cards._db_migrations import (
    NOTIFICATION_ORDER_COLUMN,
    _migrate_v8_to_v9,
    table_columns,
)
from scitex_cards._db_schema_sql import SCHEMA_SQL
from scitex_cards._ddl import execute_ddl

_COLUMN = NOTIFICATION_ORDER_COLUMN[0]

#: The v8 shape, i.e. everything the fresh script declares EXCEPT the new
#: column. Built by removing it rather than by re-typing the table, so this
#: fixture cannot drift away from the real schema.
_V8_NOTIFICATIONS = """
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
    confirmed_at TEXT
)
"""


def _empty(new_store, prefix: str, script: str):
    """An empty throwaway store with ``script`` installed through the package.

    ``bootstrap=False``: the harness's per-test store is already at the current
    shape, so a v8 fixture built on it would carry the v9 column before the
    migration ran and every assertion below would be true before the act.

    An in-memory scratch database was what these fixtures used to be. It cannot
    stand in for the store any more, and not only on principle: every shape
    question in this file is asked through ``table_columns``, which reads
    ``information_schema`` and has no second dialect to fall back to — so the
    old fixtures did not measure an old-shaped store, they errored on the probe.
    """
    conn = connect(new_store(prefix, bootstrap=False))
    execute_ddl(conn, script)
    conn.commit()
    return conn


@pytest.fixture
def v8_store(new_store):
    """A store at the v8 shape -- the thing the migration must upgrade."""
    conn = _empty(new_store, "cards_v9_v8shape", _V8_NOTIFICATIONS)
    yield conn
    conn.close()


@pytest.fixture
def fresh_store(new_store):
    """A store created by the CURRENT fresh-create script."""
    conn = _empty(new_store, "cards_v9_fresh", SCHEMA_SQL)
    yield conn
    conn.close()


class TestTheVersionWasBumped:
    def test_schema_version_is_at_least_nine(self):
        """A floor, not equality -- v10 must not fail this test."""
        # Arrange
        minimum = 9

        # Act
        actual = SCHEMA_VERSION

        # Assert
        assert actual >= minimum


class TestTheFreshCreatePathHasTheColumn:
    def test_a_fresh_store_declares_the_order_column(self, fresh_store):
        # Arrange
        table = "notifications"

        # Act
        present = table_columns(fresh_store, table)

        # Assert
        assert _COLUMN in present


class TestTheMigrationAddsTheColumn:
    def test_a_v8_store_lacks_it_before_migrating(self, v8_store):
        """Positive control: the fixture must actually be at the OLD shape.

        Without this, the migration test passes on a fixture that already had
        the column and proves nothing.
        """
        # Arrange
        table = "notifications"

        # Act
        present = table_columns(v8_store, table)

        # Assert
        assert _COLUMN not in present

    def test_migrating_adds_it(self, v8_store):
        # Arrange
        table = "notifications"

        # Act
        _migrate_v8_to_v9(v8_store)

        # Assert
        assert _COLUMN in table_columns(v8_store, table)

    def test_migrating_twice_does_not_raise(self, v8_store):
        """Idempotent -- every store open runs the whole chain."""
        # Arrange
        _migrate_v8_to_v9(v8_store)

        # Act
        try:
            _migrate_v8_to_v9(v8_store)
            raised = None
        except Exception as exc:
            raised = exc

        # Assert
        assert raised is None


class TestFreshAndMigratedAgreeOnShape:
    """This repo's own recorded failure: two creation paths, one shape."""

    def test_the_two_paths_produce_the_same_notifications_columns(
        self, fresh_store, v8_store
    ):
        """Runs the FULL chain, not one step.

        This asserted ``_migrate_v8_to_v9`` alone against the CURRENT fresh
        schema, which is a proxy that breaks on every new version rather than a
        statement about the invariant — and it went red the moment v10 added
        the sync columns, with nothing actually wrong: a real v8 store reaches
        v10 because ``init_schema`` runs the WHOLE chain.

        Its v8 sibling already learned this and says so in
        ``test__schema_v8_notification_rail.py``; the lesson had not travelled
        here. Asserting through ``init_schema`` — what production calls, and
        what is idempotent by design because ~90 agents invoke it on every open
        — makes this version-independent while still catching the failure it
        was written for: a migration that forgets a column the fresh path
        declares.
        """
        # Arrange
        from scitex_cards._db import init_schema

        init_schema(v8_store)

        # Act
        migrated = table_columns(v8_store, "notifications")
        fresh = table_columns(fresh_store, "notifications")

        # Assert
        assert migrated == fresh


@pytest.fixture
def pg_conn(postgres_dsn):
    """A connection to the harness's throwaway PostgreSQL. NEVER SKIPS.

    ONE NAME ANSWERS "WHERE IS THE STORE", and this fixture used to add a
    second. It read ``$SCITEX_CARDS_TEST_PG_DSN`` -- this package's own private
    marker -- and SKIPPED when it was unset. Nothing sets that name any more,
    so "unset" is now always, and these tests reported green in CI without ever
    opening a connection: the exact failure
    ``.github/workflows/postgres-backend-on-ubuntu-latest.yml`` exists to
    remove ("a Postgres-only test does not FAIL without a server, it SKIPS, and
    a skipped test is indistinguishable from a passing one").

    ``postgres_dsn`` (tests/conftest.py) is the one source of truth: a real
    throwaway schema on the cluster the harness opened, which FAILS rather than
    skipping when there is none.

    The driver is a hard requirement rather than a skip for the same reason:
    this package has one storage engine and psycopg is how it is reached, so
    an interpreter without it cannot run these tests at all and must say so.
    """
    try:
        import psycopg
    except ImportError:  # pragma: no cover - the package requires the driver
        pytest.fail(
            "psycopg is not installed, so the only storage engine this "
            "package has cannot be reached. Install the postgres extra: "
            "pip install -e '.[postgres]'",
            pytrace=False,
        )
    conn = psycopg.connect(postgres_dsn, connect_timeout=5)
    yield conn
    conn.close()


#: The v8 shape, spelled for PostgreSQL. The tests below CREATE IT rather than
#: assume it: CI runs a fresh postgres:16 with nothing in it, and assuming the
#: table is how the carry test passed locally and failed in CI an hour before
#: this file was written. Safe inside the rolled-back transactions because
#: PostgreSQL DDL is transactional -- created and gone with the rollback.
_PG_V8_NOTIFICATIONS = """
CREATE TABLE IF NOT EXISTS notifications (
    id           TEXT PRIMARY KEY,
    recipient_id TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    card_id      TEXT,
    body         TEXT,
    actor        TEXT,
    ts           TEXT NOT NULL,
    seen         BIGINT NOT NULL DEFAULT 0,
    record_json  TEXT,
    msg_id       TEXT,
    pushed_at    TEXT,
    confirmed_at TEXT
)
"""


class TestTheGeneratorIsRealAndMonotonic:
    """The SEQUENCE, read through a RAW driver connection.

    This class was written as "the half the retired engine tests structurally cannot
    reach", back when the fixtures above built an in-memory scratch database in
    which the column was a plain BIGINT and no sequence existed. They now build
    a real store, so that division is gone -- but the class is kept, and for a
    reason the assertions below already state: it holds a RAW psycopg
    connection, which yields TUPLES, where every other test in this file holds
    the wrapped ``StoreConnection``, which yields dict-shaped rows. Same table,
    two row types depending on how it was opened, and this package has already
    paid three separate ``KeyError: 0`` crashes for assuming one of them.

    Everything here runs inside ``force_rollback``, so nothing it creates
    survives the test.
    """

    def test_the_migration_adds_the_column_on_postgres(self, pg_conn):
        # Arrange
        table = "notifications"

        # Act
        with pg_conn.transaction(force_rollback=True):
            pg_conn.execute(_PG_V8_NOTIFICATIONS)
            _migrate_v8_to_v9(pg_conn)
            present = table_columns(pg_conn, table)

        # Assert
        assert _COLUMN in present

    def test_the_default_generates_a_value_without_the_writer_supplying_one(
        self, pg_conn
    ):
        """A column with no generator would leave these NULL and lose order."""
        # Arrange
        ident = "n_v9a"

        # Act
        with pg_conn.transaction(force_rollback=True):
            pg_conn.execute(_PG_V8_NOTIFICATIONS)
            _migrate_v8_to_v9(pg_conn)
            pg_conn.execute(
                "INSERT INTO notifications (id, recipient_id, event_type, ts) "
                f"VALUES ('{ident}', 'a', 'dm', '2026-01-01T00:00:00Z')"
            )
            row = pg_conn.execute(
                f"SELECT {_COLUMN} FROM notifications WHERE id = '{ident}'"
            ).fetchone()

        # Assert -- POSITIONAL on purpose. These fixtures use a RAW psycopg
        # connection, which yields TUPLES; the dict-like rows that broke
        # _live_task_fingerprint come from _db.connect()'s wrapper. Same table,
        # two row types, depending on how you opened it -- which is exactly why
        # production code must read BY NAME and this test must not.
        assert row[0] is not None

    def test_two_inserts_receive_increasing_values(self, pg_conn):
        """The property the column exists for: a TOTAL ORDER across writers."""
        # Arrange
        idents = ("n_v9b", "n_v9c")

        # Act
        with pg_conn.transaction(force_rollback=True):
            pg_conn.execute(_PG_V8_NOTIFICATIONS)
            _migrate_v8_to_v9(pg_conn)
            for ident in idents:
                pg_conn.execute(
                    "INSERT INTO notifications (id, recipient_id, event_type, ts) "
                    f"VALUES ('{ident}', 'a', 'dm', '2026-01-01T00:00:00Z')"
                )
            rows = pg_conn.execute(
                f"SELECT {_COLUMN} FROM notifications "
                "WHERE id IN ('n_v9b','n_v9c') ORDER BY id"
            ).fetchall()

        # Assert -- positional; see the note above on raw-vs-wrapped rows
        assert rows[0][0] < rows[1][0]

    def test_running_it_twice_on_postgres_does_not_raise(self, pg_conn):
        """CREATE SEQUENCE / ALTER COLUMN must both tolerate a re-run."""
        # Arrange
        runs = 2

        # Act
        try:
            with pg_conn.transaction(force_rollback=True):
                pg_conn.execute(_PG_V8_NOTIFICATIONS)
                for _ in range(runs):
                    _migrate_v8_to_v9(pg_conn)
            raised = None
        except Exception as exc:
            raised = exc

        # Assert
        assert raised is None


class TestExistingRowsAreNotBackfilled:
    """NULL is the honest value; a manufactured order is worse than none."""

    def test_a_pre_existing_row_keeps_a_null_order(self, v8_store):
        # Arrange
        v8_store.execute(
            "INSERT INTO notifications (id, recipient_id, event_type, ts) "
            "VALUES ('n_old', 'agent-a', 'dm', '2026-08-01T00:00:00Z')"
        )

        # Act
        _migrate_v8_to_v9(v8_store)
        row = v8_store.execute(
            f"SELECT {_COLUMN} AS s FROM notifications WHERE id = 'n_old'"
        ).fetchone()

        # Assert -- arrival order for this row lives in the OTHER database's
        # rowid and is only knowable during the carry
        assert row["s"] is None


# EOF
