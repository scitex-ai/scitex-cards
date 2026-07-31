#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A FRESH PostgreSQL store can be built from the schema script, guards included.

Until now the live PostgreSQL carried its 9 guards because they were installed
BY HAND. That is enough to cut over onto that one server and not enough for
anything else: a second server, a rebuild, or a restore would come up with the
tables present and the guards silently absent -- append-only tables that quietly
accept DELETE, a schema_version that quietly regresses. Every one of those
failures looks like a healthy database.

SO THE ASSERTIONS HERE ARE NOT "the trigger exists". Counting triggers proves
only that CREATE ran. Each guard is PROBED by attempting the exact operation it
forbids, and the test passes only if the database refuses. A guard that is
present but vacuous fails these tests, which is the whole point -- that is the
shape a hand-retyped plpgsql conversion fails in.

The DDL under test is not re-derived: ``_pg_triggers`` was generated from
``pg_get_triggerdef`` / ``pg_get_functiondef`` against the running server, so it
is byte-identical to guards that are demonstrably enforcing today.

Runs against a REAL server or skips loudly, naming it.
"""

import os

import pytest

from scitex_cards._backend_connect import connect as backend_connect
from scitex_cards._db_dm_schema import SCHEMA_SQL_V5
from scitex_cards._db_schema_sql import SCHEMA_SQL, SCHEMA_TABLES
from scitex_cards._ddl import execute_ddl
from scitex_cards._pg_triggers import PG_TRIGGER_NAMES, PG_TRIGGER_STATEMENTS
from scitex_cards._schema_probe import table_names, trigger_names

PG_URL = os.environ.get(
    "SCITEX_CARDS_TEST_PG", "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
)
PROBE_SCHEMA = "freshstore"


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
def fresh_store():
    """A PostgreSQL store built from nothing but the shipped schema constants.

    An isolated SCHEMA rather than a database: the role has no CREATE DATABASE
    privilege, and ``search_path`` pointed at a private schema resolves every
    unqualified name there and nowhere else -- so this cannot touch the real
    store even by accident.
    """
    conn = backend_connect(PG_URL, read_only=False, rows_by_name=True)
    conn.execute(f"DROP SCHEMA IF EXISTS {PROBE_SCHEMA} CASCADE")
    conn.execute(f"CREATE SCHEMA {PROBE_SCHEMA}")
    conn.execute(f"SET search_path TO {PROBE_SCHEMA}")
    execute_ddl(conn, SCHEMA_SQL)
    execute_ddl(conn, SCHEMA_SQL_V5)
    for statement in PG_TRIGGER_STATEMENTS:
        conn.execute(statement)
    conn.commit()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.execute(f"DROP SCHEMA IF EXISTS {PROBE_SCHEMA} CASCADE")
        conn.commit()
        conn.close()


@requires_postgres
def test_every_shipped_table_is_created(fresh_store):
    # Arrange
    expected = set(SCHEMA_TABLES)

    # Act
    present = table_names(fresh_store)

    # Assert
    assert expected - present == set()


@requires_postgres
def test_every_guard_trigger_is_installed(fresh_store):
    # Arrange
    expected = set(PG_TRIGGER_NAMES)

    # Act
    present = trigger_names(fresh_store)

    # Assert
    assert expected - present == set()


@requires_postgres
def test_dm_messages_refuses_delete(fresh_store):
    """Append-only means the DATABASE refuses, not that callers behave."""
    # Arrange
    fresh_store.execute(
        "INSERT INTO dm_threads(id, kind, created_at, origin_host, record_json)"
        " VALUES('t1', 'dm', 'now', 'h', '{}')"
    )
    fresh_store.execute(
        "INSERT INTO dm_messages(id, thread_id, sender, body, ts, seq,"
        " origin_host, record_json)"
        " VALUES('m1', 't1', 'a', 'hello', 'now', 1, 'h', '{}')"
    )

    # Act
    try:
        fresh_store.execute("DELETE FROM dm_messages WHERE id = 'm1'")
        refused = False
    except Exception:
        refused = True
    fresh_store.rollback()

    # Assert
    assert refused


@requires_postgres
def test_dm_threads_refuses_delete(fresh_store):
    # Arrange
    fresh_store.execute(
        "INSERT INTO dm_threads(id, kind, created_at, origin_host, record_json)"
        " VALUES('t2', 'dm', 'now', 'h', '{}')"
    )

    # Act
    try:
        fresh_store.execute("DELETE FROM dm_threads WHERE id = 't2'")
        refused = False
    except Exception:
        refused = True
    fresh_store.rollback()

    # Assert
    assert refused


@requires_postgres
def test_a_schema_version_downgrade_leaves_the_value_untouched(fresh_store):
    """The floor guard REVERTS rather than raising -- so read the value back."""
    # Arrange
    fresh_store.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', '7')"
        " ON CONFLICT(key) DO UPDATE SET value = '7'"
    )

    # Act
    fresh_store.execute(
        "UPDATE schema_meta SET value = '5' WHERE key = 'schema_version'"
    )
    row = fresh_store.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()

    # Assert
    assert row["value"] == "7"


@requires_postgres
def test_a_schema_version_downgrade_is_counted(fresh_store):
    # Arrange
    fresh_store.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', '7')"
        " ON CONFLICT(key) DO UPDATE SET value = '7'"
    )

    # Act
    fresh_store.execute(
        "UPDATE schema_meta SET value = '5' WHERE key = 'schema_version'"
    )
    row = fresh_store.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version_downgrades_refused'"
    ).fetchone()

    # Assert
    assert row is not None and int(row["value"]) >= 1


@requires_postgres
def test_a_schema_version_upgrade_is_allowed(fresh_store):
    """The positive control: the guard must not refuse a LEGAL move."""
    # Arrange
    fresh_store.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', '7')"
        " ON CONFLICT(key) DO UPDATE SET value = '7'"
    )

    # Act
    fresh_store.execute(
        "UPDATE schema_meta SET value = '8' WHERE key = 'schema_version'"
    )
    row = fresh_store.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()

    # Assert
    assert row["value"] == "8"


@requires_postgres
def test_updating_a_task_bumps_its_revision(fresh_store):
    # Arrange
    fresh_store.execute(
        "INSERT INTO tasks(id, title, status, revision) VALUES('t', 'x', 'pending', 1)"
    )

    # Act
    fresh_store.execute("UPDATE tasks SET title = 'y' WHERE id = 't'")
    row = fresh_store.execute("SELECT revision FROM tasks WHERE id = 't'").fetchone()

    # Assert
    assert row["revision"] == 2


@requires_postgres
def test_reinstalling_the_guards_is_idempotent(fresh_store):
    """Re-running the schema must not fail -- CREATE OR REPLACE is the IF NOT
    EXISTS equivalent, and a schema script that cannot be re-run is not one."""
    # Arrange
    before = trigger_names(fresh_store)

    # Act
    for statement in PG_TRIGGER_STATEMENTS:
        fresh_store.execute(statement)
    after = trigger_names(fresh_store)

    # Assert
    assert after == before


# EOF
