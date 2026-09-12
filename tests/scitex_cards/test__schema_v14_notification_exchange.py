#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v14 persists the responder-issued delivery exchange on notifications."""

from scitex_cards._db import connect, init_schema
from scitex_cards._db_migrations import table_columns
from scitex_cards._db_notification_exchange import _migrate_v13_to_v14
from scitex_cards._schema_shape import SHAPE_LADDER


def test_fresh_store_has_notification_exchange_id(new_store):
    # Arrange
    conn = connect(new_store("cards_v14_fresh", bootstrap=False))
    # Act
    init_schema(conn, allow_migration=True)
    columns = table_columns(conn, "notifications")
    # Assert
    conn.close()
    assert "exchange_id" in columns


def test_migration_adds_exchange_id_idempotently(new_store):
    # Arrange
    conn = connect(new_store("cards_v14_old"))
    conn.execute("ALTER TABLE notifications DROP COLUMN exchange_id")
    conn.commit()
    # Act
    _migrate_v13_to_v14(conn)
    _migrate_v13_to_v14(conn)
    columns = table_columns(conn, "notifications")
    # Assert
    conn.close()
    assert "exchange_id" in columns


def test_version_and_physical_ladder_have_v14():
    # Arrange
    versions = {version for version, *_ in SHAPE_LADDER}
    # Act
    version = 14
    # Assert
    assert version in versions


# EOF
