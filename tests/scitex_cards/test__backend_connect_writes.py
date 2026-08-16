#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The seam must carry a WRITE, not just a read.

Until these methods existed the seam had zero importers, and that was not an
oversight: `_db.init_schema` alone needs `executescript` and `commit`, and every
mutation module needs `commit`/`rollback`. A connection wrapper that cannot
close a transaction cannot carry the write path, so nothing could adopt it.

The SQLite half runs here. The PostgreSQL half was exercised against the live
PostgreSQL 18.4 holding the verified copy of the store -- see the PR -- because
this module's own header says pure logic could not catch the two defects that
mattered, and a mocked driver would reproduce exactly the assumptions under
test. What IS asserted here without a server is the thing a server cannot tell
you: that the paramstyle and the DSN classification are right BEFORE a
connection is attempted.
"""

from __future__ import annotations

import sqlite3

import pytest

from scitex_cards._backend_connect import connect
from scitex_cards._store_url import backend_of, is_postgres_conninfo


class TestAKeywordValueDsnIsNotAFilename:
    """The defect this file's sibling fix exists for, pinned as a regression.

    libpq accepts `host=... port=... dbname=...` and psycopg connects with it
    happily. Only the URL form was recognised, so such a DSN classified as
    SQLITE and was opened AS A FILENAME -- creating a database in the working
    directory literally named `host=127.0.0.1 port=5432 dbname=...`, which then
    accepted writes and answered queries. A wrong store that works.
    """

    def test_a_keyword_value_dsn_is_postgres(self):
        # Arrange
        dsn = "host=127.0.0.1 port=5432 dbname=scitex_cards user=scitex_cards"

        # Act
        backend = backend_of(dsn)

        # Assert
        assert backend == "postgresql"

    def test_a_bare_dbname_is_postgres(self):
        # Arrange
        dsn = "dbname=cards"

        # Act
        backend = backend_of(dsn)

        # Assert
        assert backend == "postgresql"

    def test_a_path_containing_equals_is_still_sqlite(self):
        """Detection is by KEYWORD, not by 'contains ='.

        A filesystem path may legitimately contain '=' and must keep resolving
        to SQLite, or this fix would break existing stores to fix a new one.
        """
        # Arrange
        path = "/srv/data/a=b/cards.db"

        # Act
        backend = backend_of(path)

        # Assert
        assert backend == "sqlite"

    def test_an_ordinary_path_is_still_sqlite(self):
        # Arrange
        path = "/home/x/.scitex/cards/cards.db"

        # Act
        backend = backend_of(path)

        # Assert
        assert backend == "sqlite"

    def test_the_predicate_rejects_a_non_string(self):
        # Arrange
        target = 42

        # Act
        result = is_postgres_conninfo(target)

        # Assert
        assert result is False


class TestTheSeamCarriesAWrite:
    def test_executescript_reports_how_many_statements_ran(self, tmp_path):
        """A count, because 'installed nothing' must not look like 'installed all'."""
        # Arrange
        conn = connect(str(tmp_path / "a.db"), read_only=False)

        # Act
        ran = conn.executescript(
            "CREATE TABLE t (k TEXT PRIMARY KEY, v TEXT);\nCREATE INDEX ix ON t(v);"
        )

        # Assert
        conn.close()
        assert ran == 2

    def test_executemany_inserts_every_row(self, tmp_path):
        # Arrange
        conn = connect(str(tmp_path / "b.db"), read_only=False)
        conn.executescript("CREATE TABLE t (k TEXT PRIMARY KEY, v TEXT);")

        # Act
        conn.executemany("INSERT INTO t(k,v) VALUES (?,?)", [("a", "1"), ("b", "2")])

        # Assert
        count = conn.fetchone("SELECT COUNT(*) FROM t")[0]
        conn.close()
        assert count == 2

    def test_commit_persists_across_connections(self, tmp_path):
        """The point of commit: a SECOND connection sees it."""
        # Arrange
        db = tmp_path / "c.db"
        writer = connect(str(db), read_only=False)
        writer.executescript("CREATE TABLE t (k TEXT PRIMARY KEY);")
        writer.execute("INSERT INTO t(k) VALUES (?)", ("a",))
        writer.commit()
        writer.close()

        # Act
        reader = connect(str(db), read_only=True)
        count = reader.fetchone("SELECT COUNT(*) FROM t")[0]

        # Assert
        reader.close()
        assert count == 1

    def test_rollback_discards_the_uncommitted_row(self, tmp_path):
        # Arrange
        conn = connect(str(tmp_path / "d.db"), read_only=False)
        conn.executescript("CREATE TABLE t (k TEXT PRIMARY KEY);")
        conn.execute("INSERT INTO t(k) VALUES (?)", ("a",))
        conn.commit()
        conn.execute("INSERT INTO t(k) VALUES (?)", ("b",))

        # Act
        conn.rollback()

        # Assert
        count = conn.fetchone("SELECT COUNT(*) FROM t")[0]
        conn.close()
        assert count == 1


class TestRowsByName:
    """`_db.connect` sets sqlite3.Row and callers read columns BY NAME."""

    def test_rows_are_indexable_by_column_name(self, tmp_path):
        # Arrange
        conn = connect(str(tmp_path / "e.db"), read_only=False, rows_by_name=True)
        conn.executescript("CREATE TABLE t (k TEXT PRIMARY KEY, v TEXT);")
        conn.execute("INSERT INTO t(k,v) VALUES (?,?)", ("a", "1"))

        # Act
        row = conn.fetchone("SELECT k, v FROM t")

        # Assert
        conn.close()
        assert row["v"] == "1"

    def test_it_is_off_by_default(self, tmp_path):
        """Opt-in, so existing positional readers are not changed underneath."""
        # Arrange
        conn = connect(str(tmp_path / "f.db"), read_only=False)
        conn.executescript("CREATE TABLE t (k TEXT PRIMARY KEY);")
        conn.execute("INSERT INTO t(k) VALUES (?)", ("a",))

        # Act
        row = conn.fetchone("SELECT k FROM t")

        # Assert
        conn.close()
        assert not isinstance(row, sqlite3.Row)


class TestReadOnlyStillRefusesWrites:
    """The regression guard: adding a write path must not open the read door."""

    def test_a_read_only_connection_refuses_to_write(self, tmp_path):
        # Arrange
        db = tmp_path / "g.db"
        w = connect(str(db), read_only=False)
        w.executescript("CREATE TABLE t (k TEXT PRIMARY KEY);")
        w.commit()
        w.close()
        ro = connect(str(db), read_only=True)

        # Act
        raised = pytest.raises(sqlite3.OperationalError)

        # Assert
        with raised:
            ro.execute("INSERT INTO t(k) VALUES (?)", ("a",))
        ro.close()


# EOF
