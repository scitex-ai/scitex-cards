#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The schema assertion is a module of its own, and it still runs the ladder.

``init_schema`` moved out of ``_db`` because the two change for different
reasons: ``_db`` owns CONNECTIONS (resolve, dispatch, PRAGMAs, the client-version
gate) and this owns making an open connection carry the current SHAPE. Every
schema bump edits the ladder and nothing else, and while both lived in one file
each bump edited the connection module too — which is what pushed it past its
size budget at v10.

WHAT THESE TESTS ARE FOR. A move is exactly the kind of change that reports
green while doing nothing: the imports still resolve, the tests still collect,
and the ladder quietly stops running. So they assert the OBSERVABLE end state —
a store opened through the ordinary door comes back at the current version with
the current columns — rather than the mechanics of where the function lives.
"""

from __future__ import annotations

import sqlite3

import pytest

from scitex_cards import _db, _db_init_schema
from scitex_cards._db import SCHEMA_VERSION
from scitex_cards._db_migrations import NOTIFICATION_SYNC_COLUMNS, table_columns


@pytest.fixture
def opened(tmp_path):
    """A store opened through the ordinary door, schema and all."""
    conn = _db.open_db(tmp_path / "cards.db")
    yield conn
    conn.close()


class TestTheOldImportStillResolves:
    def test_db_exposes_the_same_function_object(self):
        """~30 call sites import it from ``_db``; a rename would break them."""
        # Arrange
        moved = _db_init_schema.init_schema

        # Act
        reexported = _db.init_schema

        # Assert
        assert reexported is moved


class TestTheLadderStillRuns:
    def test_a_fresh_store_reaches_the_current_version(self, opened):
        """The whole point of the module: the store ends up current."""
        # Arrange
        expected = SCHEMA_VERSION

        # Act
        stamped = opened.execute("PRAGMA user_version").fetchone()[0]

        # Assert
        assert stamped == expected

    @pytest.mark.parametrize("column", [name for name, _ in NOTIFICATION_SYNC_COLUMNS])
    def test_the_newest_rung_was_applied(self, opened, column):
        """v10 is the last rung; if the chain stopped early it stops here."""
        # Arrange
        table = "notifications"

        # Act
        present = table_columns(opened, table)

        # Assert
        assert column in present

    def test_reopening_an_existing_store_is_not_a_downgrade(self, tmp_path):
        """Every open runs the ladder; a second open must be a no-op."""
        # Arrange
        path = tmp_path / "cards.db"
        first = _db.open_db(path)
        first.close()

        # Act
        second = _db.open_db(path)

        # Assert
        try:
            assert second.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        finally:
            second.close()


class TestItDoesNotAssertTheSchemaOnAnAlreadyCurrentStore:
    def test_a_second_open_skips_the_ddl(self, tmp_path):
        """~90 containers open this store; re-running DDL deadlocked pg_proc.

        The currency gate is what makes the second open a READ. It can only
        close when the shape ladder can place the store at SCHEMA_VERSION, so
        this is also the test that a new rung was added for the new version.
        """
        # Arrange
        path = tmp_path / "cards.db"
        _db.open_db(path).close()
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row

        # Act
        from scitex_cards._schema_current import schema_already_current
        from scitex_cards._schema_shape import observed_version

        current = schema_already_current(conn, observed_version(conn), SCHEMA_VERSION)

        # Assert
        try:
            assert current is True
        finally:
            conn.close()


# EOF
