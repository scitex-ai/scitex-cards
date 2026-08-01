#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``AUTOINCREMENT`` is the one construct here with no portable spelling.

MEASURED ON BOTH ENGINES, 2026-07-31 -- all three candidates, because the
obvious middle one is a trap:

    INTEGER PRIMARY KEY AUTOINCREMENT   sqlite OK    postgres SYNTAX ERROR
    INTEGER PRIMARY KEY                 sqlite OK    postgres NotNullViolation
    GENERATED ALWAYS AS IDENTITY        sqlite ERROR postgres OK

The middle row is what a careless port reaches for. It PARSES on both engines
and fails only at INSERT time, because PostgreSQL does not auto-assign a plain
``INTEGER PRIMARY KEY`` the way SQLite's rowid alias does. DDL-time success,
runtime failure -- the shape this whole port keeps meeting.

So the PostgreSQL test here INSERTS a row and reads the generated id back.
Asserting only that ``CREATE TABLE`` succeeded would pass for the trap spelling
too, which would make this suite worse than no suite: it would certify the one
choice that breaks in production.

The PostgreSQL tests run against a REAL server or skip loudly.
"""

import os
import sqlite3

import pytest

from scitex_cards._backend_connect import connect as backend_connect
from scitex_cards._ddl import execute_ddl, to_dialect

PG_URL = os.environ.get(
    "SCITEX_CARDS_TEST_PG", "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
)

AUTOINC_DDL = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT)"


def _postgres_reachable() -> tuple[bool, str]:
    try:
        import psycopg
    except ImportError:
        return False, "psycopg is not installed (pip install 'psycopg[binary]')"
    try:
        psycopg.connect(PG_URL, connect_timeout=4).close()
    except Exception as exc:
        return False, f"{PG_URL} unreachable: {type(exc).__name__}"
    return True, ""


_PG_OK, _PG_WHY = _postgres_reachable()
requires_postgres = pytest.mark.skipif(
    not _PG_OK, reason=_PG_WHY or "postgres available"
)


@pytest.fixture
def pg_schema():
    """An isolated PostgreSQL SCHEMA, dropped afterwards.

    A schema rather than a database: the test role has no CREATE DATABASE
    privilege, and a schema with ``search_path`` pointed at it is equally
    isolated -- an unqualified table name resolves there and nowhere else.
    """
    conn = backend_connect(PG_URL, read_only=False, rows_by_name=True)
    conn.execute("DROP SCHEMA IF EXISTS dialecttest CASCADE")
    conn.execute("CREATE SCHEMA dialecttest")
    conn.execute("SET search_path TO dialecttest")
    try:
        yield conn
    finally:
        conn.execute("DROP SCHEMA IF EXISTS dialecttest CASCADE")
        conn.commit()
        conn.close()


def test_sqlite_statements_are_returned_unchanged():
    # Arrange
    statement = AUTOINC_DDL

    # Act
    translated = to_dialect(statement, postgres=False)

    # Assert
    assert translated == statement


def test_postgres_translation_removes_autoincrement():
    # Arrange
    statement = AUTOINC_DDL

    # Act
    translated = to_dialect(statement, postgres=True)

    # Assert
    assert "AUTOINCREMENT" not in translated.upper()


def test_postgres_translation_uses_identity():
    # Arrange
    statement = AUTOINC_DDL

    # Act
    translated = to_dialect(statement, postgres=True)

    # Assert
    assert "GENERATED ALWAYS AS IDENTITY" in translated


def test_a_statement_without_autoincrement_is_untouched_on_postgres():
    # Arrange
    statement = "CREATE TABLE u (id TEXT PRIMARY KEY, body TEXT)"

    # Act
    translated = to_dialect(statement, postgres=True)

    # Assert
    assert translated == statement


def test_the_original_ddl_still_works_on_sqlite():
    # Arrange
    conn = sqlite3.connect(":memory:")

    # Act
    execute_ddl(conn, AUTOINC_DDL)
    conn.execute("INSERT INTO t(body) VALUES('x')")
    generated = conn.execute("SELECT id FROM t").fetchone()[0]

    # Assert
    assert generated == 1
    conn.close()


@requires_postgres
def test_execute_ddl_creates_the_autoincrement_table_on_postgres(pg_schema):
    # Arrange
    execute_ddl(pg_schema, AUTOINC_DDL)

    # Act
    from scitex_cards._schema_probe import has_table

    present = has_table(pg_schema, "t")

    # Assert
    assert present


@requires_postgres
def test_postgres_auto_assigns_the_id_on_insert(pg_schema):
    """The assertion the trap spelling would fail -- CREATE alone is not proof."""
    # Arrange
    execute_ddl(pg_schema, AUTOINC_DDL)

    # Act
    pg_schema.execute("INSERT INTO t(body) VALUES('x')")
    row = pg_schema.execute("SELECT id FROM t").fetchone()

    # Assert
    assert row["id"] == 1


# EOF
