#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""":func:`scitex_cards._db.connect` must recognise a PostgreSQL target.

THE FAILURE THIS PREVENTS WAS OBSERVED, NOT IMAGINED. While testing #682 a
libpq keyword/value conninfo was handed to a resolver that assumed a path:

    host=127.0.0.1 port=5432 dbname=scitex_cards user=scitex_cards

``Path(...)`` on that string does not raise. It produces a plausible relative
path, and ``mkdir`` + ``sqlite3.connect`` then CREATED a SQLite database in the
working directory literally named after the DSN. It reported backend "sqlite",
accepted writes, and answered queries, while the real PostgreSQL server sat
untouched. Nothing raised. The file had to be deleted by hand.

So the load-bearing assertion here is not "PostgreSQL works" -- it is that NO
FILE APPEARS. A store that silently forks into a second, wrong database is the
failure mode this whole port exists to avoid, and it looks like success from
every angle except the one that counts.

The PostgreSQL connection tests run against a REAL server or skip loudly.
"""

import os
import sqlite3

import pytest

from scitex_cards import _db

PG_URL = os.environ.get(
    "SCITEX_CARDS_TEST_PG", "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
)
PG_KV = "host=127.0.0.1 port=5432 dbname=scitex_cards user=scitex_cards"


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
def empty_cwd(tmp_path):
    """Run the test in a real, empty directory and restore the old one.

    A real chdir rather than a patched one: the defect under test is that
    production code CREATES A FILE relative to the process working directory,
    so the working directory has to actually change for the test to observe it.
    """
    previous = os.getcwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(previous)


def _try_connect(dsn):
    """Open ``dsn``, tolerating an unreachable server.

    An unreachable PostgreSQL server is an acceptable outcome for the
    file-creation tests; a CREATED FILE is not. Swallowing the connection error
    keeps those tests meaningful without a live server.
    """
    try:
        conn = _db.connect(dsn)
    except Exception:
        return
    conn.close()


def test_a_postgres_url_never_becomes_a_file(empty_cwd):
    # Arrange
    dsn = PG_URL

    # Act
    _try_connect(dsn)

    # Assert
    assert list(empty_cwd.iterdir()) == []


def test_a_libpq_keyword_conninfo_never_becomes_a_file(empty_cwd):
    """The exact spelling that produced the observed stray SQLite file."""
    # Arrange
    dsn = PG_KV

    # Act
    _try_connect(dsn)

    # Assert
    assert list(empty_cwd.iterdir()) == []


@requires_postgres
def test_a_postgres_url_opens_a_postgres_backed_connection():
    # Arrange
    conn = _db.connect(PG_URL)

    # Act
    backend = conn.backend

    # Assert
    assert backend.startswith("postgres")
    conn.close()


@requires_postgres
def test_a_libpq_keyword_conninfo_opens_a_postgres_backed_connection():
    # Arrange
    conn = _db.connect(PG_KV)

    # Act
    backend = conn.backend

    # Assert
    assert backend.startswith("postgres")
    conn.close()


@requires_postgres
def test_a_postgres_connection_answers_a_query_by_column_name():
    # Arrange
    conn = _db.connect(PG_URL)

    # Act
    row = conn.execute("SELECT 1 AS one").fetchone()

    # Assert
    assert row["one"] == 1
    conn.close()


def test_a_path_target_still_returns_a_sqlite3_connection(tmp_path):
    # Arrange
    target = tmp_path / "nested" / "cards.db"

    # Act
    conn = _db.connect(target)

    # Assert
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_a_path_target_still_creates_its_parent_directory(tmp_path):
    # Arrange
    target = tmp_path / "nested" / "cards.db"

    # Act
    conn = _db.connect(target)
    conn.close()

    # Assert
    assert target.exists()


def test_a_path_target_still_gets_the_wal_pragma(tmp_path):
    # Arrange
    target = tmp_path / "nested" / "cards.db"

    # Act
    conn = _db.connect(target)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    # Assert
    assert mode == "wal"
    conn.close()


def test_the_extracted_ddl_is_the_same_object_db_exposes():
    # Arrange
    from scitex_cards import _db_schema_sql

    # Act
    via_db = _db._SCHEMA_SQL

    # Assert
    assert via_db is _db_schema_sql.SCHEMA_SQL


def test_the_table_roster_survived_extraction():
    # Arrange
    expected = "schema_meta"

    # Act
    roster = _db.SCHEMA_TABLES

    # Assert
    assert expected in roster


# EOF
