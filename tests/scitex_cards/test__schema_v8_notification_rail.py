#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v8 gives ``notifications`` the columns the inbox rail needs — on BOTH paths."""

from __future__ import annotations

from scitex_cards._db import SCHEMA_VERSION, connect, init_schema
from scitex_cards._db_migrations import (
    NOTIFICATION_RAIL_COLUMNS,
    _migrate_v7_to_v8,
    table_columns,
)

# THE ``env`` FIXTURE IS GONE, AND WITH IT A PRIVATE ``$HOME``. It existed so a
# store built from a filename could not resolve to the real one; the isolation
# now comes from the target itself — ``new_store`` hands out a uniquely named
# throwaway schema, which nothing outside the test can address and which is
# dropped CASCADE when the test ends. A private HOME protected a resolution
# step these tests no longer take.


def _fresh_store(new_store, prefix: str):
    """A store built by the fresh-create path."""
    conn = connect(new_store(prefix, bootstrap=False))
    init_schema(conn)
    return conn


def _pre_v8_store(new_store, prefix: str):
    """A store whose ``notifications`` predates v8 — the migration's input."""
    conn = _fresh_store(new_store, prefix)
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
    def test_fresh_create_installs_them(self, new_store):
        # Arrange
        conn = _fresh_store(new_store, "cards_v8_fresh")

        # Act
        present = table_columns(conn, "notifications")

        # Assert
        conn.close()
        assert {c for c, _ in NOTIFICATION_RAIL_COLUMNS} <= present


class TestTheMigrationInstallsThem:
    def test_a_pre_v8_store_gains_them(self, new_store):
        # Arrange
        conn = _pre_v8_store(new_store, "cards_v8_old")

        # Act
        _migrate_v7_to_v8(conn)

        # Assert
        present = table_columns(conn, "notifications")
        conn.close()
        assert {c for c, _ in NOTIFICATION_RAIL_COLUMNS} <= present

    def test_the_input_really_lacked_them(self, new_store):
        """POSITIVE CONTROL — otherwise the test above proves nothing."""
        # Arrange
        conn = _pre_v8_store(new_store, "cards_v8_control")

        # Act
        present = table_columns(conn, "notifications")

        # Assert
        conn.close()
        assert not {c for c, _ in NOTIFICATION_RAIL_COLUMNS} & present

    def test_running_it_twice_is_a_no_op(self, new_store):
        """Every open re-runs the chain, so idempotence is the normal path."""
        # Arrange
        conn = _pre_v8_store(new_store, "cards_v8_twice")
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

    def test_the_two_paths_produce_the_same_columns(self, new_store):
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
        fresh = _fresh_store(new_store, "cards_v8_a")
        migrated = _pre_v8_store(new_store, "cards_v8_b")

        # Act
        init_schema(migrated)
        fresh_cols = table_columns(fresh, "notifications")
        migrated_cols = table_columns(migrated, "notifications")

        # Assert
        fresh.close()
        migrated.close()
        assert fresh_cols == migrated_cols


# EOF
