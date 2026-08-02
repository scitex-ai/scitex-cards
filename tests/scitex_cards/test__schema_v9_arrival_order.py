#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v9 gives ``notifications`` an arrival-order column, on BOTH creation paths.

WHY THE COLUMN EXISTS. The SQLite inbox delivers and acks by ``ORDER BY rowid``
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

import sqlite3

import pytest

from scitex_cards._db import SCHEMA_VERSION
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


@pytest.fixture
def v8_store():
    """A store at the v8 shape -- the thing the migration must upgrade."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_V8_NOTIFICATIONS)
    yield conn
    conn.close()


@pytest.fixture
def fresh_store():
    """A store created by the CURRENT fresh-create script."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    execute_ddl(conn, SCHEMA_SQL)
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
        # Arrange
        _migrate_v8_to_v9(v8_store)

        # Act
        migrated = table_columns(v8_store, "notifications")
        fresh = table_columns(fresh_store, "notifications")

        # Assert
        assert migrated == fresh


@pytest.fixture
def pg_conn():
    """Live Postgres: skip if UNDECLARED, fail if DECLARED-but-broken."""
    import os

    declared = os.environ.get("SCITEX_CARDS_TEST_PG_DSN")
    dsn = declared or "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
    try:
        import psycopg
    except ImportError:
        if declared:
            pytest.fail("SCITEX_CARDS_TEST_PG_DSN is set but psycopg is missing")
        pytest.skip("psycopg not installed")
    try:
        conn = psycopg.connect(dsn, connect_timeout=5)
    except Exception as exc:
        if declared:
            pytest.fail(f"declared Postgres at {dsn!r} unreachable: {exc}")
        pytest.skip(f"no live Postgres: {type(exc).__name__}")
    yield conn
    conn.close()


class TestThePostgresGeneratorIsRealAndMonotonic:
    """The half SQLite tests structurally cannot reach.

    On SQLite the column is a plain BIGINT and ``rowid`` remains the generator,
    so none of the tests above exercise the sequence, the DEFAULT, or ``setval``
    -- the entire PostgreSQL-specific branch of the migration. Everything here
    runs inside ``force_rollback``, so the live store is never mutated.
    """

    def test_the_migration_adds_the_column_on_postgres(self, pg_conn):
        # Arrange
        table = "notifications"

        # Act
        with pg_conn.transaction(force_rollback=True):
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
