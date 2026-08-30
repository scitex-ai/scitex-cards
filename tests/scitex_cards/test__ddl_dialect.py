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

THE SERVER TESTS TAKE THE HARNESS'S STORE AND CANNOT SKIP. They gated on
``$SCITEX_CARDS_TEST_PG`` -- this package's own private marker -- and skipped
when it was unset, falling back to a hardcoded ``127.0.0.1:5432`` nobody
serves. Nothing sets that name any more, so "unset" is now always: the two
tests that carry this file's entire point reported green in CI without opening
a connection, which is the silent-green this suite exists to refuse.
"""

import pytest

from scitex_cards._db import connect
from scitex_cards._ddl import execute_ddl, to_dialect

AUTOINC_DDL = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT)"


@pytest.fixture
def pg_schema(new_store):
    """An isolated throwaway store, dropped afterwards.

    A schema rather than a database: the test role has no CREATE DATABASE
    privilege, and a schema with ``search_path`` pointed at it is equally
    isolated -- an unqualified table name resolves there and nowhere else. That
    is what ``new_store`` hands out, so the fixture no longer carves one by
    hand against a hardcoded server.
    """
    conn = connect(new_store("cards_dialect", bootstrap=False))
    try:
        yield conn
    finally:
        conn.close()


def test_the_untranslated_branch_returns_the_statement_unchanged():
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


# `test_the_original_ddl_still_works_on_sqlite` WAS DELETED HERE. It fed the
# UNTRANSLATED text to the other engine and asserted the id came back as 1 --
# a check that the `postgres=False` branch produces something that engine
# accepts. There is no such engine to accept it. The branch itself survives and
# is still tested: `test_sqlite_statements_are_returned_unchanged` above asserts
# it returns the statement verbatim, which is the whole of what it does and the
# reason `to_dialect` keeps it (so the schema constants can be compared against
# their untranslated form).


def test_execute_ddl_creates_the_autoincrement_table_on_postgres(pg_schema):
    # Arrange
    execute_ddl(pg_schema, AUTOINC_DDL)

    # Act
    from scitex_cards._schema_probe import has_table

    present = has_table(pg_schema, "t")

    # Assert
    assert present


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
