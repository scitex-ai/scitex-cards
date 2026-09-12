#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v15 persists caller retry identity under a database uniqueness guard."""

from scitex_cards._db import SCHEMA_VERSION, connect
from scitex_cards._db_dm_idempotency import _migrate_v14_to_v15
from scitex_cards._db_migrations import table_columns
from scitex_cards._schema_ladder import SHAPE_LADDER


def test_migration_adds_client_request_id(new_store):
    # Arrange
    conn = connect(new_store("cards_v15_old"))
    conn.execute("DROP INDEX IF EXISTS idx_dm_messages_client_request")
    conn.execute("ALTER TABLE dm_messages DROP COLUMN client_request_id")
    conn.commit()
    # Act
    _migrate_v14_to_v15(conn)
    columns = table_columns(conn, "dm_messages")
    conn.close()
    # Assert
    assert "client_request_id" in columns


def test_version_and_physical_ladder_have_v15():
    # Arrange
    versions = {version for version, *_ in SHAPE_LADDER}
    # Act
    version = SCHEMA_VERSION
    # Assert
    assert version == 15 and version in versions


# EOF
