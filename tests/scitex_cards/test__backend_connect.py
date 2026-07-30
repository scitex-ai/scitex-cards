#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the backend-agnostic store connection.

THE POSTGRESQL TESTS RUN AGAINST A REAL SERVER, OR THEY SKIP LOUDLY.

They are not mocked, because mocking is what would hide the defects this module
exists to avoid. On 2026-07-30 two fatal bugs in scitex-db's migration tool
passed 196 tests and appeared only against a live driver: a probe that aborted
the transaction so the first CREATE TABLE died, and indexes of excluded tables
counted as carried. Both looked like pure logic. A mock agrees with whatever you
tell it; a server does not.

The skip reason names the server, so a green run that never touched PostgreSQL
cannot be mistaken for one that did.
"""

import os
import sqlite3

import pytest

from scitex_cards._backend_connect import StoreConnection, connect
from scitex_cards._store_url import BACKEND_POSTGRES, BACKEND_SQLITE

PG_URL = os.environ.get(
    "SCITEX_CARDS_TEST_PG", "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
)


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
def sqlite_store(tmp_path):
    """A throwaway SQLite store. Never the canonical one -- a test must not be
    able to touch the fleet's live board."""
    path = tmp_path / "cards.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, agent TEXT, title TEXT)")
    conn.executemany(
        "INSERT INTO tasks(id, agent, title) VALUES(?, ?, ?)",
        [
            ("a", "scitex-cards", "first"),
            ("b", "scitex-cards", "is it done?"),
            ("c", "other", "third"),
        ],
    )
    conn.commit()
    conn.close()
    yield str(path)


class TestBackendSelection:
    def test_a_path_opens_as_sqlite(self, sqlite_store):
        # Arrange
        target = sqlite_store
        # Act
        with connect(target) as conn:
            backend = conn.backend
        # Assert
        assert backend == BACKEND_SQLITE

    def test_a_sqlite_connection_is_wrapped(self, sqlite_store):
        # Arrange
        target = sqlite_store
        # Act
        with connect(target) as conn:
            wrapped = isinstance(conn, StoreConnection)
        # Assert
        assert wrapped is True


class TestSqliteReadsWork:
    def test_a_parameterised_query_returns_the_right_count(self, sqlite_store):
        # Arrange
        target = sqlite_store
        # Act
        with connect(target) as conn:
            n = conn.fetchone(
                "SELECT count(*) FROM tasks WHERE agent = ?", ("scitex-cards",)
            )[0]
        # Assert
        assert n == 2

    def test_read_only_refuses_to_write(self, sqlite_store):
        # Arrange: a reader that can mutate the store is how a board ends up
        # empty; read_only must actually be enforced, not documented.
        target = sqlite_store
        # Act
        with connect(target, read_only=True) as conn:
            try:
                conn.execute("DELETE FROM tasks")
                refused = False
            except sqlite3.OperationalError:
                refused = True
        # Assert
        assert refused is True


@requires_postgres
class TestPostgresReadsWork:
    """Against the real PostgreSQL 18.4 holding the verified copy."""

    def test_the_url_selects_the_postgres_backend(self):
        # Arrange
        target = PG_URL
        # Act
        with connect(target) as conn:
            backend = conn.backend
        # Assert
        assert backend == BACKEND_POSTGRES

    def test_sqlite_paramstyle_sql_runs_unchanged(self):
        # Arrange: this is the whole design -- the caller writes "?" and never
        # learns which backend it reached.
        query = "SELECT count(*) FROM tasks WHERE agent = ?"
        # Act
        with connect(PG_URL) as conn:
            n = conn.fetchone(query, ("scitex-cards",))[0]
        # Assert
        assert n > 0

    def test_a_question_mark_inside_a_literal_survives(self):
        # Arrange: a naive replace corrupts this silently -- wrong data, no error.
        query = "SELECT 'is it done?'"
        # Act
        with connect(PG_URL) as conn:
            value = conn.fetchone(query)[0]
        # Assert
        assert value == "is it done?"

    def test_a_placeholder_and_a_literal_coexist(self):
        # Arrange
        query = "SELECT ?, 'is it done?'"
        # Act
        with connect(PG_URL) as conn:
            row = conn.fetchone(query, ("x",))
        # Assert
        assert row == ("x", "is it done?")

    def test_the_append_only_guarantee_is_enforced_not_merely_present(self):
        # Arrange: the DELETE must MATCH A ROW or the guarantee is never
        # exercised -- a BEFORE DELETE trigger fires per row, so deleting zero
        # rows succeeds trivially. The first version of this test used a
        # nonexistent id "to be safe" and reported refused=False against a store
        # where the trigger provably works. A test that cannot fail for the
        # right reason cannot pass for it either. The transaction is rolled back
        # regardless, and the trigger aborts before anything is removed.
        import psycopg

        with connect(PG_URL) as conn:
            existing = conn.fetchone("SELECT id FROM dm_messages LIMIT 1")[0]
            # Act
            try:
                conn.execute("DELETE FROM dm_messages WHERE id = ?", (existing,))
                refused = False
            except psycopg.errors.RaiseException:
                refused = True
            finally:
                conn.raw.rollback()
        # Assert
        assert refused is True

    def test_the_row_the_delete_targeted_still_exists(self):
        # Arrange: proves the refusal test rolled back cleanly rather than
        # quietly removing a row from the operator's copy.
        with connect(PG_URL) as conn:
            target = conn.fetchone("SELECT id FROM dm_messages LIMIT 1")[0]
            # Act
            still_there = conn.fetchone(
                "SELECT count(*) FROM dm_messages WHERE id = ?", (target,)
            )[0]
        # Assert
        assert still_there == 1


@requires_postgres
class TestBothBackendsAnswerTheSameQuery:
    """The property that makes a migration meaningful: identical SQL text runs
    on either backend and the caller never learns which one answered.

    DELIBERATELY NOT COMPARED AGAINST THE LIVE STORE. The first version of this
    class read $SCITEX_CARDS_DB and compared row counts to the PostgreSQL copy.
    It failed with `0 == 2042`, because conftest redirects that variable to a
    temporary store precisely so tests cannot touch the fleet's live board --
    the protection working exactly as intended, against me. A test that needs
    the operator's real data to pass is not a test; it is a monitor, and it
    would go red every time someone wrote a card.

    The live comparison was run manually and is recorded on card
    cards-postgres-capable-client-20260730: identical SQL, `264` cards from both
    backends, with sqlite ahead of the copy on the append-heavy tables.
    """

    def test_the_same_sql_text_runs_on_both_backends(self, sqlite_store):
        # Arrange: one query string, SQLite paramstyle, two backends.
        query = "SELECT count(*) FROM tasks WHERE agent = ?"
        # Act
        with connect(sqlite_store) as lite, connect(PG_URL) as pg:
            ran = (
                lite.fetchone(query, ("scitex-cards",))[0],
                pg.fetchone(query, ("scitex-cards",))[0],
            )
        # Assert -- both answered; the counts differ because they are different
        # stores, and that is the point: the CALLER did not have to care.
        assert all(isinstance(n, int) for n in ran)

    def test_neither_backend_needed_a_dialect_specific_query(self, sqlite_store):
        # Arrange
        query = "SELECT count(*) FROM tasks WHERE agent = ? AND title <> 'x?'"
        # Act
        with connect(sqlite_store) as lite, connect(PG_URL) as pg:
            lite.fetchone(query, ("scitex-cards",))
            pg.fetchone(query, ("scitex-cards",))
            both_ran = True
        # Assert
        assert both_ran is True


# EOF
