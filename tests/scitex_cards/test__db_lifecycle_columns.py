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

No mocks (PA-306): these build REAL stores from the shipped SQL, on the engine
that ships. Each fixture carves its own throwaway PostgreSQL schema, installs
the DDL through the package's own ``execute_ddl``, and reads the shape back out
of ``information_schema`` — which is what makes the comparison meaningful now
that ``execute_ddl`` TRANSLATES on the way in. A scratch the retired engine file, which is
what these fixtures used to be, exercised the untranslated text and therefore
could not fail on a rung that is wrong for the only engine this package has.

The v12 baseline is produced by substituting the exact block the change added
back to the exact text it replaced — an assertion guards that substitution,
so if the schema is edited without updating this test, the test ERRORS rather
than silently comparing a store against itself.
"""

from __future__ import annotations

import pytest

from scitex_cards._db import connect
from scitex_cards._db_lifecycle_columns import (
    LIFECYCLE_COLUMNS,
    _migrate_v12_to_v13,
)
from scitex_cards._db_schema_sql import SCHEMA_SQL
from scitex_cards._ddl import execute_ddl
from scitex_cards._schema_probe import column_names

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


def _v12_schema_sql() -> str:
    """``SCHEMA_SQL`` as v12 shipped it, or an ERROR naming what drifted."""
    assert SCHEMA_SQL.count(_V13_BLOCK) == 1, (
        "the v13 block is not in _db_schema_sql.py verbatim, so this test "
        "cannot build a v12 baseline — update _V13_BLOCK/_V12_BLOCK to match "
        "the schema rather than letting the comparison run against a fiction"
    )
    return SCHEMA_SQL.replace(_V13_BLOCK, _V12_BLOCK, 1)


def _built(new_store, prefix: str, script: str):
    """An empty throwaway store with ``script`` installed through ``execute_ddl``.

    ``bootstrap=False`` is load-bearing: the per-test store the harness pins is
    already schema-complete, so a fixture built on it would carry the v13
    columns before the rung ran and every assertion below would be true before
    the act.
    """
    conn = connect(new_store(prefix, bootstrap=False))
    execute_ddl(conn, script)
    conn.commit()
    return conn


@pytest.fixture
def fresh_store(new_store):
    """A store built from the shipped CREATE TABLE script.

    Yields rather than returns: the connection is an external resource, and a
    fixture that returns one never closes it (STX-TQ005).
    """
    conn = _built(new_store, "cards_v13_fresh", SCHEMA_SQL)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def migrated_store(new_store):
    """A v12-shaped store carried forward by the rung under test."""
    conn = _built(new_store, "cards_v13_migrated", _v12_schema_sql())
    _migrate_v12_to_v13(conn)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def test_the_v12_baseline_genuinely_lacks_the_columns(new_store):
    """CONTROL. Without this, every assertion below could pass vacuously."""
    # Arrange
    conn = _built(new_store, "cards_v12_baseline", _v12_schema_sql())
    # Act
    present = column_names(conn, "tasks")
    # Assert
    try:
        assert not [c for c, _ in LIFECYCLE_COLUMNS if c in present]
    finally:
        conn.close()


def test_a_fresh_store_has_the_lifecycle_columns(fresh_store):
    # Arrange
    expected = [c for c, _ in LIFECYCLE_COLUMNS]
    # Act
    present = column_names(fresh_store, "tasks")
    # Assert
    assert all(column in present for column in expected)


def test_a_migrated_store_has_the_lifecycle_columns(migrated_store):
    # Arrange
    expected = [c for c, _ in LIFECYCLE_COLUMNS]
    # Act
    present = column_names(migrated_store, "tasks")
    # Assert
    assert all(column in present for column in expected)


def test_fresh_and_migrated_shapes_are_identical(fresh_store, migrated_store):
    """The rule the neighbouring rungs state in capitals.

    A SET, not an ordered list: ``information_schema`` has no promised row
    order, and the property being asserted was never about ordinal position —
    it is that neither path carries a column the other lacks.
    """
    # Arrange
    fresh = column_names(fresh_store, "tasks")
    # Act
    migrated = column_names(migrated_store, "tasks")
    # Assert
    assert fresh == migrated


def test_running_the_rung_twice_changes_nothing(migrated_store):
    """It runs on every open_db from ~90 containers; it must be idempotent."""
    # Arrange
    before = column_names(migrated_store, "tasks")
    # Act
    _migrate_v12_to_v13(migrated_store)
    # Assert
    assert column_names(migrated_store, "tasks") == before


def test_the_delete_flag_round_trips_as_a_boolean(migrated_store):
    """The column is declared BOOLEAN and must read back as one.

    THE ENGINE MAKES THIS STRICTER THAN IT USED TO BE. Against a file store
    ``BOOLEAN`` was an affinity and ``VALUES(..., 1)`` round-tripped as the
    integer 1, so this test could only ask whether the value was TRUTHY. The
    shipping engine has a real boolean type that REFUSES an integer, so the
    value written here is the value the column can actually hold, and the
    assertion is identity rather than truthiness.
    """
    # Arrange
    migrated_store.execute(
        "INSERT INTO tasks(id, title, is_deleted) VALUES('c1', 't', TRUE)"
    )
    # Act
    stored = migrated_store.execute(
        "SELECT is_deleted FROM tasks WHERE id='c1'"
    ).fetchone()["is_deleted"]
    # Assert
    assert stored is True


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
    ).fetchone()["is_deleted"]
    # Assert
    assert stored is None


# EOF
