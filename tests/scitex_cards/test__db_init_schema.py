#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The schema assertion is a module of its own, and it still runs the ladder.

``init_schema`` moved out of ``_db`` because the two change for different
reasons: ``_db`` owns CONNECTIONS (resolve, dispatch, the client-version gate)
and this owns making an open connection carry the current SHAPE. Every schema
bump edits the ladder and nothing else, and while both lived in one file each
bump edited the connection module too — which is what pushed it past its size
budget at v10.

WHAT THESE TESTS ARE FOR. A move is exactly the kind of change that reports
green while doing nothing: the imports still resolve, the tests still collect,
and the ladder quietly stops running. So they assert the OBSERVABLE end state —
a store opened through the ordinary door comes back at the current version with
the current columns — rather than the mechanics of where the function lives.

THE STORE THEY OPEN IS AN EMPTY ONE, AND THAT IS THE WHOLE MEASUREMENT. The
harness's per-test store is already schema-complete, so "the ladder ran" is
true of it before ``open_db`` is called at all; asserting the end state against
that store passes with ``init_schema`` DELETED. Each test here therefore carves
an unprovisioned throwaway store (``bootstrap=False``) and asserts the
TRANSITION across the open.
"""

from __future__ import annotations

import pytest

from scitex_cards import _db, _db_init_schema
from scitex_cards._db import SCHEMA_VERSION
from scitex_cards._db_migrations import NOTIFICATION_SYNC_COLUMNS, table_columns
from scitex_cards._schema_shape import observed_version


def _stamped(conn) -> int | None:
    """The version the store SAYS it is, or None when it says nothing.

    ``PRAGMA user_version`` was the reading here; the shipping engine has no
    PRAGMA, and ``_read_stamps`` returns ``stamped_pragma=None`` on it by
    design. ``schema_meta.schema_version`` is the stamp that exists, and it is
    the one ``stamp_schema_version`` writes.
    """
    return observed_version(conn).stamped_meta


@pytest.fixture
def empty_store(new_store):
    """An unprovisioned throwaway store — no tables, no stamp, no rungs."""
    return new_store("cards_init_schema", bootstrap=False)


@pytest.fixture
def opened(empty_store):
    """That store, opened through the ordinary door: schema and all."""
    conn = _db.open_db(empty_store)
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
    def test_a_fresh_store_reaches_the_current_version(self, empty_store):
        """The whole point of the module: the store ends up current.

        Measured as a TRANSITION. The end state alone is satisfiable by a store
        that was already provisioned, which is every store this harness hands
        out except this one.
        """
        # Arrange
        before = _db.connect(empty_store)
        try:
            unstamped = _stamped(before)
        finally:
            before.close()

        # Act
        conn = _db.open_db(empty_store)

        # Assert
        try:
            assert (unstamped, _stamped(conn)) == (None, SCHEMA_VERSION)
        finally:
            conn.close()

    @pytest.mark.parametrize("column", [name for name, _ in NOTIFICATION_SYNC_COLUMNS])
    def test_the_newest_rung_was_applied(self, opened, column):
        """v10 is the last rung; if the chain stopped early it stops here."""
        # Arrange
        table = "notifications"

        # Act
        present = table_columns(opened, table)

        # Assert
        assert column in present

    def test_reopening_an_existing_store_is_not_a_downgrade(self, empty_store):
        """Every open runs the ladder; a second open must be a no-op."""
        # Arrange
        first = _db.open_db(empty_store)
        first.close()

        # Act
        second = _db.open_db(empty_store)

        # Assert
        try:
            assert _stamped(second) == SCHEMA_VERSION
        finally:
            second.close()


class TestItDoesNotAssertTheSchemaOnAnAlreadyCurrentStore:
    def test_a_second_open_skips_the_ddl(self, empty_store):
        """~90 containers open this store; re-running DDL deadlocked pg_proc.

        The currency gate is what makes the second open a READ. It can only
        close when the shape ladder can place the store at SCHEMA_VERSION, so
        this is also the test that a new rung was added for the new version.

        The probe runs on a PLAIN connection rather than through ``open_db``,
        because ``open_db`` is the thing whose second call must be a read:
        asking the question through it would assert the schema again as a side
        effect of asking.
        """
        # Arrange
        _db.open_db(empty_store).close()
        conn = _db.connect(empty_store)

        # Act
        from scitex_cards._schema_current import schema_already_current

        current = schema_already_current(conn, observed_version(conn), SCHEMA_VERSION)

        # Assert
        try:
            assert current is True
        finally:
            conn.close()

    def test_an_unprovisioned_store_is_not_reported_current(self, empty_store):
        """CONTROL for the test above: the gate must be able to answer False.

        Without it, ``schema_already_current`` returning a constant ``True``
        would pass — and a gate that always says "already current" is the one
        that leaves a store with no guards on it while reporting success.
        """
        # Arrange
        conn = _db.connect(empty_store)

        # Act
        from scitex_cards._schema_current import schema_already_current

        current = schema_already_current(conn, observed_version(conn), SCHEMA_VERSION)

        # Assert
        try:
            assert current is False
        finally:
            conn.close()


# EOF
