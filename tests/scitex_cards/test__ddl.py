#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The DDL splitter is tested against the REAL trigger constants.

Inventing SQL for this would test the splitter against my own idea of what the
package's DDL looks like. The constants below are the ones actually installed
on every store, so if one of them grows a shape the splitter cannot handle,
these fail rather than production.

THE FAILURE MODE THIS GUARDS is not a crash. A naive ``split(";")`` cuts a
trigger body at its internal semicolons, and the first fragment can still parse
as a complete ``CREATE TRIGGER`` on some engines -- installing a TRUNCATED
trigger that enforces less than its name claims. Every presence probe in this
package looks up triggers BY NAME, so a half-installed guard reports as
present. That is why the round-trip test below checks ENFORCEMENT and not
existence.
"""

from __future__ import annotations

import sqlite3

import pytest

from scitex_cards._db_migrations import REVISION_TRIGGER_SQL
from scitex_cards._ddl import execute_ddl, split_sql_script
from scitex_cards._schema_shape import SCHEMA_VERSION_FLOOR_TRIGGER_SQL
from scitex_cards._store_retirement import RETIREMENT_TRIGGER_SQL


class TestItSplitsTheRealTriggerConstants:
    def test_the_retirement_script_holds_two_triggers(self):
        # Arrange
        script = RETIREMENT_TRIGGER_SQL

        # Act
        statements = split_sql_script(script)

        # Assert
        assert len(statements) == 2

    def test_each_retirement_statement_is_a_whole_trigger(self):
        """A fragment that lost its END is the dangerous case, not an error case."""
        # Arrange
        script = RETIREMENT_TRIGGER_SQL

        # Act
        statements = split_sql_script(script)

        # Assert
        assert all(s.upper().rstrip().endswith("END") for s in statements)

    def test_the_version_floor_script_is_one_statement(self):
        # Arrange
        script = SCHEMA_VERSION_FLOOR_TRIGGER_SQL

        # Act
        statements = split_sql_script(script)

        # Assert
        assert len(statements) == 1

    def test_the_revision_trigger_script_is_one_statement(self):
        # Arrange
        script = REVISION_TRIGGER_SQL

        # Act
        statements = split_sql_script(script)

        # Assert
        assert len(statements) == 1


class TestTheNaiveSplitIsWrong:
    """POSITIVE CONTROL: proves the splitter is doing something, not nothing.

    Without this, a splitter that happened to return the whole script unchanged
    would pass every test above for the single-statement constants.
    """

    def test_splitting_on_semicolons_over_counts_the_floor_trigger(self):
        # Arrange
        naive = [s for s in SCHEMA_VERSION_FLOOR_TRIGGER_SQL.split(";") if s.strip()]

        # Act
        correct = split_sql_script(SCHEMA_VERSION_FLOOR_TRIGGER_SQL)

        # Assert
        assert len(naive) > len(correct)


class TestTransactionControlIsNotANestingOpener:
    """``BEGIN IMMEDIATE`` must not swallow the rest of the script."""

    def test_begin_immediate_does_not_open_a_body(self):
        # Arrange
        script = "BEGIN IMMEDIATE;\nCREATE TABLE t (a TEXT);\nCOMMIT;\n"

        # Act
        statements = split_sql_script(script)

        # Assert
        assert len(statements) == 3


class TestCommentsAreDropped:
    def test_a_comment_only_line_yields_no_statement(self):
        # Arrange
        script = "-- just a note\n"

        # Act
        statements = split_sql_script(script)

        # Assert
        assert statements == []

    def test_a_double_dash_inside_a_literal_is_kept(self):
        """Cutting at the first `--` would corrupt any statement containing one."""
        # Arrange
        script = "INSERT INTO t(v) VALUES ('a -- b');\n"

        # Act
        statements = split_sql_script(script)

        # Assert
        assert "a -- b" in statements[0]


class TestExecuteDdlRoundTripsOnARealDatabase:
    """Install via execute_ddl, then check the guard REFUSES -- not that it exists."""

    def test_it_reports_how_many_statements_ran(self, tmp_path):
        # Arrange
        conn = sqlite3.connect(tmp_path / "n.db")
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")

        # Act
        ran = execute_ddl(conn, RETIREMENT_TRIGGER_SQL)

        # Assert
        conn.close()
        assert ran == 2

    def test_the_installed_guard_actually_refuses(self, tmp_path):
        # Arrange
        conn = sqlite3.connect(tmp_path / "e.db")
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        execute_ddl(conn, RETIREMENT_TRIGGER_SQL)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('store_status','retired')"
        )

        # Act
        raised = pytest.raises(sqlite3.IntegrityError)

        # Assert
        with raised:
            conn.execute(
                "UPDATE schema_meta SET value='current' WHERE key='store_status'"
            )
        conn.close()

    def test_it_builds_the_same_schema_as_executescript(self, tmp_path):
        """THE TEST THAT ACTUALLY CAUGHT THE BUG.

        Eleven unit tests passed while the splitter was cutting four
        append-only triggers in half, because none of them executed
        ``SCHEMA_SQL_V5`` -- the constant that writes ``BEGIN`` trailing on its
        line rather than alone. Comparing the resulting sqlite_master against
        ``executescript``'s own output is the only check that could not be
        satisfied by a splitter that merely looked reasonable.
        """
        # Arrange
        from scitex_cards._db import _SCHEMA_SQL
        from scitex_cards._db_dm_schema import SCHEMA_SQL_V5

        scripts = [
            _SCHEMA_SQL,
            SCHEMA_SQL_V5,
            RETIREMENT_TRIGGER_SQL,
            SCHEMA_VERSION_FLOOR_TRIGGER_SQL,
            REVISION_TRIGGER_SQL,
        ]
        query = "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        native = sqlite3.connect(tmp_path / "native.db")
        for script in scripts:
            native.executescript(script)
        runner = sqlite3.connect(tmp_path / "runner.db")
        for script in scripts:
            execute_ddl(runner, script)
        runner.commit()

        # Act
        same = sorted(native.execute(query)) == sorted(runner.execute(query))

        # Assert
        native.close()
        runner.close()
        assert same

    def test_it_installs_every_trigger(self, tmp_path):
        """A count, because a missing guard is the failure that matters here."""
        # Arrange
        from scitex_cards._db import _SCHEMA_SQL
        from scitex_cards._db_dm_schema import SCHEMA_SQL_V5

        conn = sqlite3.connect(tmp_path / "trg.db")
        for script in (
            _SCHEMA_SQL,
            SCHEMA_SQL_V5,
            RETIREMENT_TRIGGER_SQL,
            SCHEMA_VERSION_FLOOR_TRIGGER_SQL,
            REVISION_TRIGGER_SQL,
        ):
            execute_ddl(conn, script)
        conn.commit()

        # Act
        triggers = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'"
        ).fetchone()[0]

        # Assert
        conn.close()
        assert triggers == 9

    def test_the_probe_is_not_vacuous(self, tmp_path):
        """The refusal above means nothing unless the UPDATE matched a row."""
        # Arrange
        conn = sqlite3.connect(tmp_path / "v.db")
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        execute_ddl(conn, RETIREMENT_TRIGGER_SQL)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('store_status','retired')"
        )

        # Act
        rows = conn.execute(
            "SELECT COUNT(*) FROM schema_meta WHERE key='store_status'"
        ).fetchone()[0]

        # Assert
        conn.close()
        assert rows == 1


# EOF
