#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The guard probe must read the REAL catalogue, and must not over-count.

TWO VANTAGES, DELIBERATELY. One half runs against a real store with the real
trigger constants — nothing is asserted about the catalogue that is not read
back out of a running server. The other half drives a hand-rolled recording
connection, because the SQL TEXT is a property of this module that a live run
cannot show you: a query that happened to return the right answer for the wrong
reason (unscoped, counting internal triggers) is indistinguishable from a
correct one when you only look at its result.

THE HALF THAT USED TO BE THE OTHER BACKEND IS GONE. This file was "the the retired engine
half runs against a real database; the PostgreSQL half uses a fake, because CI
has no PostgreSQL". CI now has one, the store IS one, and the module has a
single dialect — so the real-database half is the store and the recording half
is about the query text, not about a branch.
"""

from __future__ import annotations

from _banned import DRIVER, ENGINE  # noqa: F401

import pytest

from scitex_cards._db import connect
from scitex_cards._ddl import execute_ddl
from scitex_cards._schema_probe import (
    column_names,
    has_column,
    has_table,
    has_trigger,
    table_names,
    trigger_names,
)
from scitex_cards._store_retirement import RETIREMENT_TRIGGER_SQL

_SCHEMA_META = "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)"
_ONE_WAY = "schema_meta_retirement_is_one_way"


class _RecordingPostgresConnection:
    """A hand-rolled stand-in that reports a postgres backend and records SQL."""

    backend = "postgresql"

    def __init__(self, rows):
        self.rows = rows
        self.seen: list[str] = []

    def execute(self, sql):
        self.seen.append(sql)
        return list(self.rows)


@pytest.fixture
def empty(new_store):
    """An unprovisioned throwaway store — nothing in it but what a test puts."""
    conn = connect(new_store("cards_probe", bootstrap=False))
    yield conn
    conn.close()


class TestItIsReadFromTheRealCatalogue:
    def test_it_finds_an_installed_trigger(self, empty):
        # Arrange
        execute_ddl(empty, _SCHEMA_META)
        execute_ddl(empty, RETIREMENT_TRIGGER_SQL)

        # Act
        found = has_trigger(empty, _ONE_WAY)

        # Assert
        assert found

    def test_it_reports_absence_when_no_trigger_is_installed(self, empty):
        """The negative must be reachable, or the positive proves nothing."""
        # Arrange
        execute_ddl(empty, _SCHEMA_META)

        # Act
        found = has_trigger(empty, _ONE_WAY)

        # Assert
        assert not found

    def test_it_finds_a_table(self, empty):
        # Arrange
        execute_ddl(empty, _SCHEMA_META)

        # Act
        found = has_table(empty, "schema_meta")

        # Assert
        assert found


class TestItSeesThisStoreAndNotTheServer:
    """The 2026-08-30 defect, as tests rather than as a comment.

    The trigger query asked the whole DATABASE, and the store is now a SCHEMA on
    a shared server — so a guard belonging to somebody else's schema counted as
    this store's, and ``schema_already_current`` returned True for a store whose
    own guard had been dropped. THIS CLASS COULD NOT HAVE EXISTED ON THE PREVIOUS
    ENGINE: a file store has no schemas, so "every trigger in the database" and
    "every trigger in this store" were the same set, and the test that replaced
    this one asked whether ``the engine's``-prefixed internal tables were excluded —
    a question about a namespace this engine does not have.
    """

    def test_a_trigger_in_another_schema_is_not_counted(self, empty, new_store):
        # Arrange — a REAL guard, installed on a REAL store, that is not this one.
        neighbour = connect(new_store("cards_probe_neighbour", bootstrap=False))
        execute_ddl(neighbour, _SCHEMA_META)
        execute_ddl(neighbour, RETIREMENT_TRIGGER_SQL)
        neighbour.commit()

        # Act
        theirs = has_trigger(neighbour, _ONE_WAY)
        mine = has_trigger(empty, _ONE_WAY)

        # Assert -- the pair, because "mine is False" alone also passes when the
        # neighbour's install silently did nothing.
        try:
            assert (theirs, mine) == (True, False)
        finally:
            neighbour.close()

    def test_a_table_in_another_schema_is_not_counted(self, empty, new_store):
        # Arrange
        neighbour = connect(new_store("cards_probe_neighbour2", bootstrap=False))
        execute_ddl(neighbour, _SCHEMA_META)
        neighbour.commit()

        # Act
        theirs = has_table(neighbour, "schema_meta")
        mine = has_table(empty, "schema_meta")

        # Assert
        try:
            assert (theirs, mine) == (True, False)
        finally:
            neighbour.close()

    def test_an_empty_store_reports_no_tables_at_all(self, empty):
        """``information_schema`` holds hundreds of rows; none are the store's."""
        # Arrange
        store = empty

        # Act
        names = table_names(store)

        # Assert
        assert names == set()


class TestTheQueryTextIsWhatItClaims:
    def test_it_queries_pg_catalog(self):
        # Arrange
        conn = _RecordingPostgresConnection([("tasks_bump_revision",)])

        # Act
        trigger_names(conn)

        # Assert
        assert "pg_trigger" in conn.seen[0]

    def test_it_excludes_internal_triggers(self):
        """Every FK constraint installs internal triggers.

        Counting those would report a guard-free store as richly guarded --
        the exact direction of error this probe exists to prevent.
        """
        # Arrange
        conn = _RecordingPostgresConnection([("tasks_bump_revision",)])

        # Act
        trigger_names(conn)

        # Assert
        assert "tgisinternal" in conn.seen[0]

    def test_it_scopes_the_trigger_query_to_this_schema(self):
        """The text half of ``TestItSeesThisStoreAndNotTheServer`` above.

        The live test proves the answer; this proves it is the scoping that
        produced it, and not two schemas that happened not to collide.
        """
        # Arrange
        conn = _RecordingPostgresConnection([("tasks_bump_revision",)])

        # Act
        trigger_names(conn)

        # Assert
        assert "current_schema()" in conn.seen[0]

    def test_it_returns_the_names_it_was_given(self):
        # Arrange
        conn = _RecordingPostgresConnection([("a",), ("b",)])

        # Act
        names = trigger_names(conn)

        # Assert
        assert names == {"a", "b"}

    def test_tables_use_information_schema(self):
        # Arrange
        conn = _RecordingPostgresConnection([("tasks",)])

        # Act
        table_names(conn)

        # Assert
        assert "information_schema.tables" in conn.seen[0]

    def test_columns_use_information_schema(self):
        """The column rungs of the version ladder are read through this query,
        and an unreadable rung reads as ABSENT -- reporting the store OLDER than
        it physically is, which re-runs the whole DDL on every open."""
        # Arrange
        conn = _RecordingPostgresConnection([("revision",)])

        # Act
        column_names(conn, "tasks")

        # Assert
        assert "information_schema.columns" in conn.seen[0]

    def test_columns_are_scoped_to_the_table_asked_about(self):
        """Without the table filter every column in the database comes back,
        so any column lookup on any table would answer yes."""
        # Arrange
        conn = _RecordingPostgresConnection([("revision",)])

        # Act
        column_names(conn, "tasks")

        # Assert
        assert "table_name = 'tasks'" in conn.seen[0]


class TestColumnNames:
    def test_it_reads_the_names(self, empty):
        # Arrange
        execute_ddl(empty, "CREATE TABLE tasks (id TEXT PRIMARY KEY, revision INTEGER)")

        # Act
        names = column_names(empty, "tasks")

        # Assert
        assert names == {"id", "revision"}

    def test_it_reports_a_missing_column_absent(self, empty):
        """The negative must be reachable, or the positive proves nothing."""
        # Arrange
        execute_ddl(empty, "CREATE TABLE tasks (id TEXT PRIMARY KEY)")

        # Act
        found = has_column(empty, "tasks", "revision")

        # Assert
        assert not found

    def test_an_absent_table_yields_an_empty_set_rather_than_raising(self, empty):
        # Arrange
        absent = "nosuchtable"

        # Act
        names = column_names(empty, absent)

        # Assert
        assert names == set()

    def test_it_refuses_a_name_it_cannot_safely_interpolate(self, empty):
        """The table name is interpolated, not bound, because the placeholder
        this module would otherwise have to know about is a paramstyle layer it
        deliberately does not carry. Interpolation is only safe on a constrained
        identifier, so the name is validated rather than trusted."""
        # Arrange
        hostile = "tasks; DROP TABLE tasks"

        # Act
        try:
            column_names(empty, hostile)
            raised = None
        except ValueError as exc:
            raised = exc

        # Assert
        assert raised is not None


# EOF
