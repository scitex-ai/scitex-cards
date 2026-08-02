#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The guard probe must work on both backends, and must not over-count.

The SQLite half runs against a real database with the real trigger constants.
The PostgreSQL half uses a hand-rolled recording connection rather than a live
server, because CI has no PostgreSQL and a test that silently skips would be
exactly the green-bar theatre this suite forbids. The fake asserts the ONE
thing that cannot be checked without it: that the postgres branch is taken and
the catalogue queried is pg_catalog, not sqlite_master.

The live-server behaviour was verified separately against the real store
(9 triggers found, matching SQLite's 9) -- see the PR description.
"""

from __future__ import annotations

import sqlite3

from scitex_cards._schema_probe import (
    column_names,
    has_column,
    has_table,
    has_trigger,
    table_names,
    trigger_names,
)
from scitex_cards._store_retirement import RETIREMENT_TRIGGER_SQL


class _RecordingPostgresConnection:
    """A hand-rolled stand-in that reports a postgres backend and records SQL."""

    backend = "postgresql"

    def __init__(self, rows):
        self.rows = rows
        self.seen: list[str] = []

    def execute(self, sql):
        self.seen.append(sql)
        return list(self.rows)


class TestSqliteIsReadFromTheRealCatalogue:
    def test_it_finds_an_installed_trigger(self, tmp_path):
        # Arrange
        conn = sqlite3.connect(tmp_path / "a.db")
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.executescript(RETIREMENT_TRIGGER_SQL)

        # Act
        found = has_trigger(conn, "schema_meta_retirement_is_one_way")

        # Assert
        conn.close()
        assert found

    def test_it_reports_absence_when_no_trigger_is_installed(self, tmp_path):
        """The negative must be reachable, or the positive proves nothing."""
        # Arrange
        conn = sqlite3.connect(tmp_path / "b.db")
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")

        # Act
        found = has_trigger(conn, "schema_meta_retirement_is_one_way")

        # Assert
        conn.close()
        assert not found

    def test_it_finds_a_table(self, tmp_path):
        # Arrange
        conn = sqlite3.connect(tmp_path / "c.db")
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")

        # Act
        found = has_table(conn, "schema_meta")

        # Assert
        conn.close()
        assert found

    def test_it_excludes_sqlite_internal_tables(self, tmp_path):
        """sqlite_sequence would otherwise appear as a store table."""
        # Arrange
        conn = sqlite3.connect(tmp_path / "d.db")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        conn.execute("INSERT INTO t DEFAULT VALUES")

        # Act
        names = table_names(conn)

        # Assert
        conn.close()
        assert not any(n.startswith("sqlite_") for n in names)


class TestPostgresTakesTheOtherBranch:
    def test_it_queries_pg_catalog_not_sqlite_master(self):
        # Arrange
        conn = _RecordingPostgresConnection([("tasks_bump_revision",)])

        # Act
        trigger_names(conn)

        # Assert
        assert "pg_trigger" in conn.seen[0]

    def test_it_never_touches_sqlite_master_on_postgres(self):
        """The whole defect was querying a catalogue that does not exist there."""
        # Arrange
        conn = _RecordingPostgresConnection([("tasks_bump_revision",)])

        # Act
        trigger_names(conn)

        # Assert
        assert "sqlite_master" not in conn.seen[0]

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

    def test_columns_use_information_schema_not_pragma(self):
        """PRAGMA table_info is SQLite-only, so the column rungs of the version
        ladder were unreadable on PostgreSQL -- and an unreadable rung reads as
        ABSENT, reporting the store OLDER than it physically is."""
        # Arrange
        conn = _RecordingPostgresConnection([("revision",)])

        # Act
        column_names(conn, "tasks")

        # Assert
        assert "information_schema.columns" in conn.seen[0]

    def test_columns_never_issue_a_pragma_on_postgres(self):
        """PostgreSQL rejects PRAGMA outright: syntax error at or near PRAGMA."""
        # Arrange
        conn = _RecordingPostgresConnection([("revision",)])

        # Act
        column_names(conn, "tasks")

        # Assert
        assert "PRAGMA" not in conn.seen[0]

    def test_columns_are_scoped_to_the_table_asked_about(self):
        """Without the table filter every column in the database comes back,
        so any column lookup on any table would answer yes."""
        # Arrange
        conn = _RecordingPostgresConnection([("revision",)])

        # Act
        column_names(conn, "tasks")

        # Assert
        assert "table_name = 'tasks'" in conn.seen[0]


class TestColumnNamesOnSqlite:
    def test_it_reads_the_name_not_the_column_index(self, tmp_path):
        """PRAGMA table_info returns (cid, name, type, ...). Taking the FIRST
        column -- which is what the shared _sole_value helper does -- would
        yield the integer cid, and every lookup would silently miss."""
        # Arrange
        conn = sqlite3.connect(tmp_path / "cols.db")
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, revision INTEGER)")

        # Act
        names = column_names(conn, "tasks")

        # Assert
        conn.close()
        assert names == {"id", "revision"}

    def test_it_reports_a_missing_column_absent(self, tmp_path):
        """The negative must be reachable, or the positive proves nothing."""
        # Arrange
        conn = sqlite3.connect(tmp_path / "cols2.db")
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")

        # Act
        found = has_column(conn, "tasks", "revision")

        # Assert
        conn.close()
        assert not found

    def test_an_absent_table_yields_an_empty_set_rather_than_raising(self, tmp_path):
        # Arrange
        conn = sqlite3.connect(tmp_path / "cols3.db")

        # Act
        names = column_names(conn, "nosuchtable")

        # Assert
        conn.close()
        assert names == set()

    def test_it_refuses_a_name_it_cannot_safely_interpolate(self, tmp_path):
        """The table name is interpolated, not bound, because the two engines
        disagree on the placeholder. Interpolation is only safe on a
        constrained identifier, so the name is validated rather than trusted."""
        # Arrange
        conn = sqlite3.connect(tmp_path / "cols4.db")

        # Act
        try:
            column_names(conn, "tasks; DROP TABLE tasks")
            raised = None
        except ValueError as exc:
            raised = exc

        # Assert
        conn.close()
        assert raised is not None


# EOF
