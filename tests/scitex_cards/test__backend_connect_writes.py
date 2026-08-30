#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The seam must carry a WRITE, not just a read.

Until these methods existed the seam had zero importers, and that was not an
oversight: `_db.init_schema` alone needs `executescript` and `commit`, and every
mutation module needs `commit`/`rollback`. A connection wrapper that cannot
close a transaction cannot carry the write path, so nothing could adopt it.

THE HALVES HAVE COLLAPSED INTO ONE. This file used to say "the the retired engine half runs
here; the PostgreSQL half was exercised against the live server -- see the PR",
because pure logic could not catch the two defects that mattered and a mocked
driver would reproduce exactly the assumptions under test. Both statements are
still true and there is now only one engine to run them on, so the write tests
open a REAL throwaway store rather than a scratch file. What is still asserted
WITHOUT a connection is the thing a server cannot tell you: that the DSN
classification is right BEFORE a connection is attempted.
"""

from __future__ import annotations

import pytest

from scitex_cards._backend_connect import connect
from scitex_cards._store_url import (
    BACKEND_POSTGRES,
    BACKEND_UNSUPPORTED,
    backend_of,
    is_postgres_conninfo,
)


@pytest.fixture
def empty(new_store):
    """An unprovisioned throwaway store, opened writable through the seam."""
    conn = connect(new_store("cards_seam", bootstrap=False), read_only=False)
    yield conn
    conn.close()


class TestAKeywordValueDsnIsNotAFilename:
    """The defect this file's sibling fix exists for, pinned as a regression.

    libpq accepts `host=... port=... dbname=...` and psycopg connects with it
    happily. Only the URL form was recognised, so such a DSN classified as a
    FILENAME and was opened as one -- creating a database in the working
    directory literally named `host=127.0.0.1 port=5432 dbname=...`, which then
    accepted writes and answered queries. A wrong store that works.
    """

    def test_a_keyword_value_dsn_is_postgres(self):
        # Arrange
        dsn = "host=127.0.0.1 port=5432 dbname=scitex_cards user=scitex_cards"

        # Act
        backend = backend_of(dsn)

        # Assert
        assert backend == BACKEND_POSTGRES

    def test_a_bare_dbname_is_postgres(self):
        # Arrange
        dsn = "dbname=cards"

        # Act
        backend = backend_of(dsn)

        # Assert
        assert backend == BACKEND_POSTGRES

    def test_a_path_containing_equals_is_not_taken_for_a_dsn(self):
        """Detection is by KEYWORD, not by 'contains ='.

        THE ANSWER CHANGED NAME, NOT MEANING. This asserted the path classified
        as "the retired engine"; there is no second engine to classify as, and
        `BACKEND_UNSUPPORTED` is not the name of one -- it is the symbol for
        "this names no store I can open". What the test is for is unchanged and
        is if anything sharper now: a filesystem path that happens to contain
        '=' must not be mistaken for a conninfo and CONNECTED TO.
        """
        # Arrange
        path = "/srv/data/a=b/cards.db"

        # Act
        backend = backend_of(path)

        # Assert
        assert backend == BACKEND_UNSUPPORTED

    def test_an_ordinary_path_is_not_taken_for_a_dsn(self):
        # Arrange
        path = "/home/x/.scitex/cards/cards.db"

        # Act
        backend = backend_of(path)

        # Assert
        assert backend == BACKEND_UNSUPPORTED

    def test_the_predicate_rejects_a_non_string(self):
        # Arrange
        target = 42

        # Act
        result = is_postgres_conninfo(target)

        # Assert
        assert result is False


class TestTheSeamCarriesAWrite:
    def test_executescript_reports_how_many_statements_ran(self, empty):
        """A count, because 'installed nothing' must not look like 'installed all'."""
        # Arrange
        script = "CREATE TABLE t (k TEXT PRIMARY KEY, v TEXT);\nCREATE INDEX ix ON t(v);"

        # Act
        ran = empty.executescript(script)

        # Assert
        assert ran == 2

    def test_executemany_inserts_every_row(self, empty):
        # Arrange
        empty.executescript("CREATE TABLE t (k TEXT PRIMARY KEY, v TEXT);")

        # Act
        empty.executemany("INSERT INTO t(k,v) VALUES (?,?)", [("a", "1"), ("b", "2")])

        # Assert -- AN EXPLICIT ALIAS: a COUNT has no column name of its own and
        # this driver's rows do not answer to a position.
        count = empty.fetchone("SELECT COUNT(*) AS n FROM t")[0]
        assert count == 2

    def test_commit_persists_across_connections(self, new_store):
        """The point of commit: a SECOND connection sees it."""
        # Arrange
        target = new_store("cards_seam_commit", bootstrap=False)
        writer = connect(target, read_only=False)
        writer.executescript("CREATE TABLE t (k TEXT PRIMARY KEY);")
        writer.execute("INSERT INTO t(k) VALUES (?)", ("a",))
        writer.commit()
        writer.close()

        # Act
        reader = connect(target, read_only=True)
        count = reader.fetchone("SELECT COUNT(*) AS n FROM t")[0]

        # Assert
        reader.close()
        assert count == 1

    def test_rollback_discards_the_uncommitted_row(self, empty):
        # Arrange
        empty.executescript("CREATE TABLE t (k TEXT PRIMARY KEY);")
        empty.execute("INSERT INTO t(k) VALUES (?)", ("a",))
        empty.commit()
        empty.execute("INSERT INTO t(k) VALUES (?)", ("b",))

        # Act
        empty.rollback()

        # Assert
        count = empty.fetchone("SELECT COUNT(*) AS n FROM t")[0]
        assert count == 1


class TestRowsByName:
    """`_db.connect` asks for named rows, and callers read columns BY NAME."""

    def test_rows_are_indexable_by_column_name(self, new_store):
        # Arrange
        conn = connect(
            new_store("cards_seam_named", bootstrap=False),
            read_only=False,
            rows_by_name=True,
        )
        conn.executescript("CREATE TABLE t (k TEXT PRIMARY KEY, v TEXT);")
        conn.execute("INSERT INTO t(k,v) VALUES (?,?)", ("a", "1"))

        # Act
        row = conn.fetchone("SELECT k, v FROM t")

        # Assert
        conn.close()
        assert row["v"] == "1"

    def test_it_is_off_by_default(self, empty):
        """Opt-in, so existing positional readers are not changed underneath.

        Asserted as "the row is a plain tuple" rather than "the row is not the
        old driver's Row type". Same property, stated about the type this
        driver actually returns instead of about the absence of one that no
        longer exists.
        """
        # Arrange
        empty.executescript("CREATE TABLE t (k TEXT PRIMARY KEY);")
        empty.execute("INSERT INTO t(k) VALUES (?)", ("a",))

        # Act
        row = empty.fetchone("SELECT k FROM t")

        # Assert
        assert isinstance(row, tuple)


# `TestReadOnlyStillRefusesWrites` WAS DELETED, and the decision that abolished
# it is stated in the function it tested. `_backend_connect.connect`'s docstring:
#
#     ``read_only`` is ADVISORY and is deliberately not faked. Read-only-ness is
#     a property of the ROLE, not of the connection, so this function will not
#     claim to enforce something it does not: grant SELECT-only to the role if
#     that is the guarantee you need.
#
# The test asserted that a `read_only=True` connection RAISES on an INSERT.
# That held on a file store, where the driver could open the file read-only, and
# it cannot hold here -- the connection is the same connection and the server
# decides by role. Keeping the test would require the wrapper to fake a refusal,
# which is precisely what the docstring refuses to do; keeping it green would
# require granting the harness a SELECT-only role, which would be testing the
# fixture's grant rather than this seam. The guarantee moved to the database,
# and a test of it belongs where the grant is.
