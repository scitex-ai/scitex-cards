#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""":func:`scitex_cards._db.connect` must recognise a PostgreSQL target."""

import os

import pytest

from scitex_cards import _db
from scitex_cards._store_url import UnrecognisedStoreTarget

#: The two spellings from the 2026-08 incident, kept VERBATIM. These are used by
#: the no-file tests, which want a target that may well be unreachable -- the
#: assertion there is about the filesystem, not about the server.
PG_URL = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
PG_KV = "host=127.0.0.1 port=5432 dbname=scitex_cards user=scitex_cards"


def _as_keyword_value(url: str) -> str:
    """Rewrite a libpq URL into the keyword/value conninfo spelling.

    Uses psycopg's own parser rather than a regex: the point of the test that
    calls this is that BOTH spellings reach the same server, and a hand-rolled
    rewrite that dropped the ``options=-csearch_path`` the harness relies on
    would silently aim the connection at ``public`` -- the live board.
    """
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    return make_conninfo(**conninfo_to_dict(url))


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
    """The exact spelling that produced the observed stray the retired engine file."""
    # Arrange
    dsn = PG_KV

    # Act
    _try_connect(dsn)

    # Assert
    assert list(empty_cwd.iterdir()) == []


def test_a_postgres_url_opens_a_postgres_backed_connection(postgres_dsn):
    # Arrange
    conn = _db.connect(postgres_dsn)

    # Act
    backend = conn.backend

    # Assert
    assert backend.startswith("postgres")
    conn.close()


def test_a_libpq_keyword_conninfo_opens_a_postgres_backed_connection(postgres_dsn):
    """The keyword/value spelling, against the server the harness opened.

    ``postgres_dsn`` is a URL; this rewrites it into the conninfo form that
    produced the stray file, so the spelling under test is the incident's and
    the SERVER is one that actually answers.
    """
    # Arrange
    conn = _db.connect(_as_keyword_value(postgres_dsn))

    # Act
    backend = conn.backend

    # Assert
    assert backend.startswith("postgres")
    conn.close()


def test_a_postgres_connection_answers_a_query_by_column_name(postgres_dsn):
    # Arrange
    conn = _db.connect(postgres_dsn)

    # Act
    row = conn.execute("SELECT 1 AS one").fetchone()

    # Assert
    assert row["one"] == 1
    conn.close()


# THREE TESTS WERE DELETED HERE, and what they asserted is worth naming rather
# than quietly dropping: that a PATH target "still" returned a file-backed
# connection, "still" created its parent directory, and "still" carried a
# journal-mode PRAGMA. Every one of those is the behaviour this module's own
# header describes as the defect -- a target that is not the store being OPENED
# and CREATED. They were the compatibility half of a two-backend door, and the
# operator's ruling removed the second backend, so there is no world left in
# which they can pass. What replaces them is the same three facts inverted: the
# path is REFUSED, and the refusal happens before the filesystem is touched.


def _refusal(target):
    """Call the door and hand back what it raised, or None.

    ``pytest.raises`` would be the obvious spelling and it counts as the test's
    ONE assertion (STX-TQ007), which would leave no budget for the fact each
    test below is actually about -- that nothing was created on the way to the
    refusal. Capturing the exception separates the act from the assertion.
    """
    try:
        conn = _db.connect(target)
    except UnrecognisedStoreTarget as exc:
        return exc
    conn.close()
    return None


def test_a_path_target_is_refused(tmp_path):
    """The door's first statement, and the reason it is first."""
    # Arrange
    target = tmp_path / "nested" / "cards.db"

    # Act
    refused = _refusal(target)

    # Assert
    assert refused is not None


def test_a_refused_path_target_leaves_no_file(tmp_path):
    """The refusal must come BEFORE the open, not after it."""
    # Arrange
    target = tmp_path / "nested" / "cards.db"

    # Act
    _refusal(target)

    # Assert
    assert not target.exists()


def test_a_refused_path_target_creates_no_parent_directory(tmp_path):
    """``mkdir(parents=True)`` ran BEFORE the refusal existed, which is how the
    directory outlived the connection attempt that made it."""
    # Arrange
    target = tmp_path / "nested" / "cards.db"

    # Act
    _refusal(target)

    # Assert
    assert list(tmp_path.iterdir()) == []


def test_the_refusal_names_the_target_it_refused(tmp_path):
    """A refusal that does not say WHAT it refused sends the reader hunting.

    The measured cost of the opposite: a users-registry read fail-softing to an
    empty registry, which degraded every peer name in the fleet -- diagnosed
    only because the error carried the phantom path it had been handed.
    """
    # Arrange
    target = tmp_path / "nested" / "cards.db"

    # Act
    refused = _refusal(target)

    # Assert
    assert str(target) in str(refused)


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
