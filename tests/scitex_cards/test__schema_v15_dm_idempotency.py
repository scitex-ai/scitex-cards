#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v15 persists caller retry identity under a database uniqueness guard."""

from scitex_cards._database.schema_rungs import _migrate_v14_to_v15
from scitex_cards._db import connect
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
    # Act — literal 15, NOT the live constant: the newest version file owns
    # the "current" assertion (v16 holds it now), and every older file keeps
    # pinning its own rung so a dropped rung fails HERE, naming its version.
    version = 15
    # Assert
    assert version in versions


# EOF
