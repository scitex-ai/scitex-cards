#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The null-safe operator must be chosen per backend, and these tests EXECUTE it.

A test that only compares strings would pass on a helper that emits confident
nonsense. The defect this module exists to prevent has bitten twice already, and
both times the code READ correctly:

  * 0.31.3 - the inbox used ``IS NOT DISTINCT FROM``, which is standard SQL and
    correct everywhere except the SQLite the HOST runs (3.37.2; the syntax needs
    3.39). CI and every container ran a newer SQLite, so nothing went red. Every
    enqueue raised for 36 hours into a fail-soft ``except``.
  * 2026-08-02 - the fix for that (``IS ?``) is SQLite-only, so it becomes a hard
    blocker for moving the inbox rail onto Postgres, where ``IS $1`` is a syntax
    error.

So the load-bearing tests run real SQL against a real connection, and the CROSS
tests deliberately run each spelling on the WRONG backend to prove the helper is
necessary rather than decorative. If someone later "simplifies" this to a single
literal, a cross test goes red and names the reason.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from scitex_cards._sql_null_safe import (
    POSTGRES_NULL_SAFE,
    SQLITE_NULL_SAFE,
    null_safe_eq,
)

_PG_DSN = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"

#: The incompatibility only exists below 3.39; above it the standard spelling
#: works on SQLite too and there is nothing to demonstrate.
_sqlite_understands_standard = sqlite3.sqlite_version_info >= (3, 39)

_needs_old_sqlite = pytest.mark.skipif(
    _sqlite_understands_standard,
    reason=(
        f"SQLite {sqlite3.sqlite_version} understands the standard spelling; "
        "the incompatibility exists only below 3.39 (the HOST runs 3.37.2)"
    ),
)


#: When SET, Postgres is DECLARED available and an unreachable server is a
#: FAILURE, not a skip. CI exports this alongside its postgres service, so a
#: broken service goes red instead of quietly reducing the suite to the SQLite
#: half. Unset (a dev box with no Postgres) still skips.
_ENV_PG_DSN = "SCITEX_CARDS_TEST_PG_DSN"


@pytest.fixture
def pg_conn():
    """A live Postgres connection: skip if UNDECLARED, fail if DECLARED-but-broken.

    The asymmetry is the point. A Postgres-only test does not fail without a
    server, it SKIPS -- and a skipped test is indistinguishable from a passing
    one in a green summary. So the moment someone declares a server by exporting
    the DSN, absence stops being an excuse and becomes a failure.

    Same rule as the channel log sink: unconfigured is fine, configured-but-
    broken is loud. Without it, adding a CI service buys nothing -- the legs
    would go green whether the service came up or not.
    """
    declared = os.environ.get(_ENV_PG_DSN)
    dsn = declared or _PG_DSN

    try:
        import psycopg
    except ImportError:
        if declared:
            pytest.fail(
                f"{_ENV_PG_DSN} is set but psycopg is not installed -- the "
                "Postgres tests would silently not run. Install the [postgres] "
                "extra or unset the variable."
            )
        pytest.skip("psycopg not installed")

    try:
        conn = psycopg.connect(dsn, connect_timeout=5)
    except Exception as exc:
        if declared:
            pytest.fail(
                f"{_ENV_PG_DSN} declares a Postgres at {dsn!r} but connecting "
                f"raised {type(exc).__name__}: {exc}. A declared backend that "
                "cannot be reached must be loud, not skipped."
            )
        pytest.skip(f"no live Postgres: {type(exc).__name__}")

    yield conn
    conn.close()


def _executes(conn, sql, params):
    """Run ``sql``; return the exception it raised, or None on success."""
    try:
        with conn:
            conn.execute(sql, params).fetchall()
    except Exception as exc:
        return exc
    return None


class TestTheEmittedFragment:
    def test_sqlite_uses_the_bare_is_operator(self):
        # Arrange
        column = "card_id"

        # Act
        fragment = null_safe_eq(column, postgres=False)

        # Assert
        assert fragment == f"card_id {SQLITE_NULL_SAFE} ?"

    def test_postgres_uses_the_standard_operator(self):
        # Arrange
        column = "card_id"

        # Act
        fragment = null_safe_eq(column, postgres=True)

        # Assert
        assert fragment == f"card_id {POSTGRES_NULL_SAFE} ?"

    def test_the_two_spellings_differ(self):
        """Guards a 'simplification' collapsing both arms to one literal."""
        # Arrange
        column = "c"

        # Act
        both = {
            null_safe_eq(column, postgres=True),
            null_safe_eq(column, postgres=False),
        }

        # Assert
        assert len(both) == 2


class TestSqliteExecutesItsOwnSpelling:
    """Not a string comparison - the statement must parse and run."""

    def test_null_matches_null(self):
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (c TEXT)")
        conn.execute("INSERT INTO t (c) VALUES (NULL)")
        sql = f"SELECT 1 FROM t WHERE {null_safe_eq('c', postgres=False)}"

        # Act
        rows = conn.execute(sql, (None,)).fetchall()

        # Assert -- null-safe means NULL equals NULL, which plain = never does
        assert len(rows) == 1

    def test_null_does_not_match_a_value(self):
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (c TEXT)")
        conn.execute("INSERT INTO t (c) VALUES (NULL)")
        sql = f"SELECT 1 FROM t WHERE {null_safe_eq('c', postgres=False)}"

        # Act
        rows = conn.execute(sql, ("x",)).fetchall()

        # Assert
        assert rows == []


class TestTheCrossCasesThatJustifyThisModule:
    """Each spelling on the WRONG backend. These are the reason it exists."""

    @_needs_old_sqlite
    def test_the_postgres_spelling_is_rejected_by_old_sqlite(self):
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (c TEXT)")
        sql = f"SELECT 1 FROM t WHERE {null_safe_eq('c', postgres=True)}"

        # Act
        raised = _executes(conn, sql, (None,))

        # Assert
        assert raised is not None

    def test_the_sqlite_spelling_is_rejected_by_postgres(self, pg_conn):
        # Arrange -- psycopg paramstyle is %s, which is what _db translates ? into
        sql = f"SELECT 1 WHERE 'a' {SQLITE_NULL_SAFE} %s"

        # Act
        raised = _executes(pg_conn, sql, ("a",))

        # Assert
        assert raised is not None

    def test_the_postgres_spelling_is_accepted_by_postgres(self, pg_conn):
        """Positive control for the test above.

        Without it, 'the SQLite spelling fails on Postgres' could equally mean a
        broken connection, a wrong DSN, or a missing table -- a failure proving
        nothing about the operator.
        """
        # Arrange
        sql = f"SELECT 1 WHERE 'a' {POSTGRES_NULL_SAFE} %s"

        # Act
        raised = _executes(pg_conn, sql, ("a",))

        # Assert
        assert raised is None


# EOF
