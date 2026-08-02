#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The inbox rail's table/column/order names, resolved per backend.

THE LOAD-BEARING TEST HERE IS THE SQLITE ONE. The seam is introduced so
``_inbox_sqlite`` can be rewired without changing behaviour on the rail that is
actually in production; if the SQLite spelling is not byte-identical to what is
there today, the rewire is a behaviour change wearing a refactor's clothes.

THE ORDERING IS WHY THIS IS ONE OBJECT AND NOT THREE CONSTANTS. A table rename
plus a column rename produces SQL that is valid on both engines and silently
loses delivery order, because ``rowid`` has no PostgreSQL equivalent. Bundling
the order expression with the names makes "renamed the table but kept rowid"
unrepresentable rather than merely discouraged.
"""

from __future__ import annotations

import sqlite3

import pytest

from scitex_cards._inbox_shape import (
    POSTGRES_SHAPE,
    SQLITE_SHAPE,
    InboxShape,
    shape_for,
)

_PG_DSN = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"


@pytest.fixture
def sqlite_conn():
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def pg_conn():
    """Live Postgres: skip if UNDECLARED, fail if DECLARED-but-broken."""
    import os

    declared = os.environ.get("SCITEX_CARDS_TEST_PG_DSN")
    dsn = declared or _PG_DSN
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


class TestTheSqliteShapeMatchesWhatIsInProductionToday:
    """Byte-identical, or the rewire is a behaviour change in disguise."""

    def test_the_table_is_inbox(self):
        # Arrange
        shape = SQLITE_SHAPE

        # Act
        table = shape.table

        # Assert
        assert table == "inbox"

    def test_the_recipient_column_is_recipient(self):
        # Arrange
        shape = SQLITE_SHAPE

        # Act
        column = shape.recipient

        # Assert
        assert column == "recipient"

    def test_the_order_clause_is_order_by_rowid(self):
        """The exact string the five current call sites contain."""
        # Arrange
        shape = SQLITE_SHAPE

        # Act
        clause = shape.order()

        # Assert
        assert clause == "ORDER BY rowid"


class TestThePostgresShapeNamesTheCanonicalStore:
    def test_the_table_is_notifications(self):
        # Arrange
        shape = POSTGRES_SHAPE

        # Act
        table = shape.table

        # Assert
        assert table == "notifications"

    def test_the_recipient_column_is_renamed(self):
        # Arrange
        shape = POSTGRES_SHAPE

        # Act
        column = shape.recipient

        # Assert
        assert column == "recipient_id"

    def test_the_order_clause_uses_the_v9_column(self):
        """NOT rowid, which does not exist there, and NOT ts."""
        # Arrange
        shape = POSTGRES_SHAPE

        # Act
        clause = shape.order()

        # Assert
        assert clause == "ORDER BY seq"


class TestTheTwoShapesDifferInAllThree:
    """Guards a 'simplification' that shares a field between the two."""

    def test_every_field_differs(self):
        # Arrange
        pairs = [
            (SQLITE_SHAPE.table, POSTGRES_SHAPE.table),
            (SQLITE_SHAPE.recipient, POSTGRES_SHAPE.recipient),
            (SQLITE_SHAPE.order_by, POSTGRES_SHAPE.order_by),
        ]

        # Act
        shared = [a for a, b in pairs if a == b]

        # Assert
        assert shared == []


class TestTheShapeComesFromTheConnection:
    def test_a_sqlite_connection_selects_the_sqlite_shape(self, sqlite_conn):
        # Arrange
        expected = SQLITE_SHAPE

        # Act
        resolved = shape_for(sqlite_conn)

        # Assert
        assert resolved == expected

    def test_a_postgres_connection_selects_the_postgres_shape(self, pg_conn):
        """The half that cannot be inferred from the SQLite test."""
        # Arrange
        expected = POSTGRES_SHAPE

        # Act
        resolved = shape_for(pg_conn)

        # Assert
        assert resolved == expected


class TestTheShapeIsImmutable:
    """A shared module-level constant that a caller can mutate is a footgun."""

    def test_a_shape_cannot_be_reassigned(self):
        # Arrange
        shape = InboxShape(table="t", recipient="r", order_by="o")

        # Act
        try:
            shape.table = "other"
            raised = None
        except Exception as exc:
            raised = exc

        # Assert
        assert raised is not None


# EOF
