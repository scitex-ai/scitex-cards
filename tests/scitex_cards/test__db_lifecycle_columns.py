#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Schema rung v12 -> v13 — the lifecycle columns, and the shape invariant.

THE INVARIANT THAT MATTERS IS `test_fresh_and_migrated_shapes_are_identical`.
`_db_schema_sql.py` states it in capitals for the neighbouring rungs — "They
MUST match _migrate_v9_to_v10 exactly; a fresh store and a migrated store"
must not diverge — because `CREATE TABLE IF NOT EXISTS` is a no-op on an
existing table, so a column added ONLY to the fresh script never reaches a
migrated store and a column added ONLY to the rung never reaches a fresh one.
The chain already carries one scar from exactly that trap: there is no
`_migrate_v3_to_v4`, because v4's changes went into the fresh script alone.

A test that asserted only "the columns exist" would pass in both halves of
that failure. Comparing the two shapes is what makes it a real check.

No mocks (PA-306): these build real SQLite stores from the shipped SQL. The
v12 baseline is produced by substituting the exact block the change added
back to the exact text it replaced — an assertion guards that substitution,
so if the schema is edited without updating this test, the test ERRORS rather
than silently comparing a store against itself.
"""

from __future__ import annotations

import sqlite3

import pytest

from scitex_cards._db_lifecycle_columns import (
    LIFECYCLE_COLUMNS,
    _migrate_v12_to_v13,
)
from scitex_cards._db_schema_sql import SCHEMA_SQL

#: The exact block the v13 change introduced, and the exact text it replaced.
#: Reconstructing the v12 shape by substitution rather than by regex keeps the
#: baseline HONEST: a stray comma or a partial strip would build SQL that is
#: not what v12 shipped, and the comparison below would be against a fiction.
_V13_BLOCK = """    deleted_at     TEXT,
    is_deleted     BOOLEAN,
    completed_at   TEXT,
    reopened_at    TEXT
);"""

_V12_BLOCK = """    deleted_at     TEXT
);"""


def _columns(conn: sqlite3.Connection, table: str = "tasks") -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


@pytest.fixture
def fresh_store(tmp_path):
    """A store built from the shipped CREATE TABLE script.

    Yields rather than returns: the connection is an external resource, and a
    fixture that returns one never closes it (STX-TQ005).
    """
    conn = sqlite3.connect(tmp_path / "fresh.db")
    conn.executescript(SCHEMA_SQL)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def migrated_store(tmp_path):
    """A v12-shaped store carried forward by the rung under test."""
    assert SCHEMA_SQL.count(_V13_BLOCK) == 1, (
        "the v13 block is not in _db_schema_sql.py verbatim, so this test "
        "cannot build a v12 baseline — update _V13_BLOCK/_V12_BLOCK to match "
        "the schema rather than letting the comparison run against a fiction"
    )
    conn = sqlite3.connect(tmp_path / "migrated.db")
    conn.executescript(SCHEMA_SQL.replace(_V13_BLOCK, _V12_BLOCK, 1))
    _migrate_v12_to_v13(conn)
    try:
        yield conn
    finally:
        conn.close()


def test_the_v12_baseline_genuinely_lacks_the_columns(tmp_path):
    """CONTROL. Without this, every assertion below could pass vacuously."""
    # Arrange
    conn = sqlite3.connect(tmp_path / "v12.db")
    # Act
    conn.executescript(SCHEMA_SQL.replace(_V13_BLOCK, _V12_BLOCK, 1))
    # Assert
    try:
        assert not [c for c, _ in LIFECYCLE_COLUMNS if c in _columns(conn)]
    finally:
        conn.close()


def test_a_fresh_store_has_the_lifecycle_columns(fresh_store):
    # Arrange
    expected = [c for c, _ in LIFECYCLE_COLUMNS]
    # Act
    present = _columns(fresh_store)
    # Assert
    assert all(column in present for column in expected)


def test_a_migrated_store_has_the_lifecycle_columns(migrated_store):
    # Arrange
    expected = [c for c, _ in LIFECYCLE_COLUMNS]
    # Act
    present = _columns(migrated_store)
    # Assert
    assert all(column in present for column in expected)


def test_fresh_and_migrated_shapes_are_identical(fresh_store, migrated_store):
    """The rule the neighbouring rungs state in capitals."""
    # Arrange
    fresh = _columns(fresh_store)
    # Act
    migrated = _columns(migrated_store)
    # Assert
    assert fresh == migrated


def test_running_the_rung_twice_changes_nothing(migrated_store):
    """It runs on every open_db from ~90 containers; it must be idempotent."""
    # Arrange
    before = _columns(migrated_store)
    # Act
    _migrate_v12_to_v13(migrated_store)
    # Assert
    assert _columns(migrated_store) == before


def test_the_delete_flag_round_trips_as_a_boolean(migrated_store):
    """SQLite has no BOOL type; the value must still read back as truthy."""
    # Arrange
    migrated_store.execute(
        "INSERT INTO tasks(id, title, is_deleted) VALUES('c1', 't', 1)"
    )
    # Act
    stored = migrated_store.execute(
        "SELECT is_deleted FROM tasks WHERE id='c1'"
    ).fetchone()[0]
    # Assert
    assert bool(stored) is True


def test_an_existing_row_gets_null_not_an_invented_default(migrated_store):
    """No column takes a DEFAULT, so history is not given a value nobody measured.

    `is_deleted DEFAULT 0` would read as "every historical card was affirmatively
    not deleted". NULL says "not recorded", which is what is true.
    """
    # Arrange
    migrated_store.execute("INSERT INTO tasks(id, title) VALUES('c2', 't')")
    # Act
    stored = migrated_store.execute(
        "SELECT is_deleted FROM tasks WHERE id='c2'"
    ).fetchone()[0]
    # Assert
    assert stored is None


# EOF
