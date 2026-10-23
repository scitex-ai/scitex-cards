#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v16 hides a DM conversation per reader without touching its content."""

from scitex_cards._db import SCHEMA_VERSION, connect
from scitex_cards._db_dm_schema import migrate_v15_to_v16
from scitex_cards._schema_ladder import SHAPE_LADDER
from scitex_cards._schema_probe import has_table, has_trigger


def test_fresh_store_has_visibility_events(new_store):
    # Arrange
    conn = connect(new_store("cards_v16_fresh"))
    # Act
    tables = has_table(conn, "dm_visibility_events")
    trigger = has_trigger(conn, "dm_visibility_events_no_delete")
    # Assert
    conn.close()
    assert tables and trigger


def test_migration_adds_visibility_events_idempotently(new_store):
    # Arrange
    conn = connect(new_store("cards_v16_old"))
    conn.execute("DROP TABLE IF EXISTS dm_visibility_events")
    conn.commit()
    # Act
    migrate_v15_to_v16(conn)
    migrate_v15_to_v16(conn)
    tables = has_table(conn, "dm_visibility_events")
    trigger = has_trigger(conn, "dm_visibility_events_no_delete")
    # Assert
    conn.close()
    assert tables and trigger


def test_version_and_physical_ladder_have_v16():
    # Arrange
    versions = {version for version, *_ in SHAPE_LADDER}
    # Act — the live constant lives HERE, in the newest version file, so the
    # bump and its rung arrive (or fail) together. Older files pin literals.
    version = SCHEMA_VERSION
    # Assert
    assert version == 16 and version in versions


# EOF
