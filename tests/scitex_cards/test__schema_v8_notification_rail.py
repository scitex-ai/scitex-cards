#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v8 gives ``notifications`` the columns the inbox rail needs — on BOTH paths.

The notification inbox currently lives in a SQLite sidecar at
``runtime/todo.db``, located from the store PATH. So pointing the store at a
PostgreSQL server does not move it: cards go to the server and notifications
stay on a local file. That split is what let a DM commit to the store on
2026-08-01 while no notification was ever created, with every card-side check
green.

v8 is the first step of closing it. ``notifications`` already exists on the
fresh-create path with the right shape and the right ``(recipient_id, seen)``
index, and is VESTIGIAL — 0 rows on the live store, its only writers being the
derived-mirror rebuild. It is missing exactly three columns the sidecar gained
later: ``msg_id``, ``pushed_at``, ``confirmed_at``.

THE FAILURE THIS FILE EXISTS TO PREVENT is recorded in this repo's own
``_db_migrations`` header: *"Whatever v4 added went into ``_SCHEMA_SQL`` only …
so a v3 file upgraded straight to v5 never received it, while its stamp said
otherwise."* A store that reports the right schema version and does not have
the column. So the load-bearing test here is not "does the migration add the
columns" — it is **does a FRESH store end up identical to a MIGRATED one**.

Scope, stated so nobody reads more into v8 than it does: installing the columns
does NOT move the rail. ``_inbox_sqlite`` still writes ``todo.db``, and
``_db_mirror`` still issues ``DELETE FROM notifications`` — harmless against a
derived empty table, and data loss the moment this table becomes the store of
record. That DELETE must be neutralised in the same change that flips the
writers.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from scitex_cards._db import SCHEMA_VERSION, init_schema
from scitex_cards._db_migrations import (
    NOTIFICATION_RAIL_COLUMNS,
    _migrate_v7_to_v8,
    table_columns,
)

_MANAGED = ("SCITEX_CARDS_DB", "HOME", "SCITEX_DIR")


@pytest.fixture
def env(tmp_path):
    """Private HOME so nothing resolves to the real store."""
    saved = {name: os.environ.get(name) for name in _MANAGED}
    saved_cwd = os.getcwd()

    os.environ.pop("SCITEX_DIR", None)
    os.environ["HOME"] = str(tmp_path)
    os.chdir(tmp_path)

    yield tmp_path

    os.chdir(saved_cwd)
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _fresh_store(path):
    """A store built by the fresh-create path."""
    conn = sqlite3.connect(str(path))
    init_schema(conn)
    return conn


def _pre_v8_store(path):
    """A store whose ``notifications`` predates v8 — the migration's input."""
    conn = sqlite3.connect(str(path))
    init_schema(conn)
    # Rebuild the pre-v8 shape honestly, by recreating the table without the v8
    # columns rather than faking the state by editing a version stamp. A
    # stamp-edit would test a corruption we invented; this tests the upgrade
    # path real stores actually take.
    conn.execute("DROP TABLE notifications")
    conn.execute(
        "CREATE TABLE notifications ("
        " id TEXT PRIMARY KEY, recipient_id TEXT NOT NULL, event_type TEXT NOT NULL,"
        " card_id TEXT, body TEXT, actor TEXT, ts TEXT NOT NULL,"
        " seen INTEGER NOT NULL DEFAULT 0, record_json TEXT)"
    )
    conn.commit()
    return conn


class TestTheVersionMovedWithTheShape:
    def test_schema_version_is_at_least_8(self):
        """A floor, not a snapshot — later versions must not fail this."""
        # Arrange
        floor = 8

        # Act
        actual = SCHEMA_VERSION

        # Assert
        assert actual >= floor

    def test_the_ladder_can_place_a_v8_store(self):
        """Physical evidence, not the stamp — the ladder must have a v8 rung."""
        # Arrange
        from scitex_cards._schema_shape import SHAPE_LADDER

        # Act
        rungs = [v for v, *_ in SHAPE_LADDER]

        # Assert
        assert 8 in rungs


class TestAFreshStoreHasTheColumns:
    def test_fresh_create_installs_them(self, env):
        # Arrange
        conn = _fresh_store(env / "fresh.db")

        # Act
        present = table_columns(conn, "notifications")

        # Assert
        conn.close()
        assert {c for c, _ in NOTIFICATION_RAIL_COLUMNS} <= present


class TestTheMigrationInstallsThem:
    def test_a_pre_v8_store_gains_them(self, env):
        # Arrange
        conn = _pre_v8_store(env / "old.db")

        # Act
        _migrate_v7_to_v8(conn)

        # Assert
        present = table_columns(conn, "notifications")
        conn.close()
        assert {c for c, _ in NOTIFICATION_RAIL_COLUMNS} <= present

    def test_the_input_really_lacked_them(self, env):
        """POSITIVE CONTROL — otherwise the test above proves nothing."""
        # Arrange
        conn = _pre_v8_store(env / "control.db")

        # Act
        present = table_columns(conn, "notifications")

        # Assert
        conn.close()
        assert not {c for c, _ in NOTIFICATION_RAIL_COLUMNS} & present

    def test_running_it_twice_is_a_no_op(self, env):
        """Every open re-runs the chain, so idempotence is the normal path."""
        # Arrange
        conn = _pre_v8_store(env / "twice.db")
        _migrate_v7_to_v8(conn)

        # Act
        _migrate_v7_to_v8(conn)

        # Assert
        present = table_columns(conn, "notifications")
        conn.close()
        assert {c for c, _ in NOTIFICATION_RAIL_COLUMNS} <= present


class TestFreshAndMigratedAgree:
    """THE LOAD-BEARING TEST. The v4 gap in this repo's own chain is exactly
    this disagreement, and it stayed invisible because the stamp was right."""

    def test_the_two_paths_produce_the_same_columns(self, env):
        """Runs the FULL chain, not one step.

        This asserted `_migrate_v7_to_v8` alone against the CURRENT fresh
        schema, which is a proxy that breaks on every new version rather than a
        statement about the invariant. It went red the moment v9 added
        `notifications.seq`: the fresh path had the column, the single migration
        step did not, and nothing was actually wrong -- a real v7 store reaches
        v9 because `init_schema` runs the WHOLE chain.

        `init_schema` is what production calls, is idempotent by design (~90
        agents invoke it on every open), and applies every migration. Asserting
        through it makes this version-independent: v10 will not need to touch
        this test, and it still catches the failure it was written for -- a
        migration that forgets a column the fresh path declares.
        """
        # Arrange
        fresh = _fresh_store(env / "a.db")
        migrated = _pre_v8_store(env / "b.db")

        # Act
        init_schema(migrated)
        fresh_cols = table_columns(fresh, "notifications")
        migrated_cols = table_columns(migrated, "notifications")

        # Assert
        fresh.close()
        migrated.close()
        assert fresh_cols == migrated_cols


# EOF
