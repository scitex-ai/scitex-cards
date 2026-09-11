#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ordinary opens must never migrate an existing configured store."""

from scitex_cards._db import connect, open_db
from scitex_cards._db_init_schema import SchemaMigrationRequired, init_schema


def _make_v13(store: str) -> None:
    conn = open_db(store)
    try:
        conn.execute(
            "ALTER TABLE notifications DROP COLUMN IF EXISTS exchange_id"
        )
        conn.execute(
            "UPDATE schema_meta SET value = '13' WHERE key = 'schema_version'"
        )
        conn.commit()
    finally:
        conn.close()


def _has_exchange_column(store: str) -> bool:
    conn = connect(store)
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'notifications' AND column_name = 'exchange_id'"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _ordinary_open_error(store: str):
    try:
        open_db(store)
    except Exception as exc:  # noqa: BLE001 - the returned type is asserted
        return exc
    return None


def test_open_db_refuses_to_auto_migrate_an_existing_store(new_store):
    # Arrange
    store = new_store()
    _make_v13(store)
    # Act
    error = _ordinary_open_error(store)
    # Assert
    assert (
        isinstance(error, SchemaMigrationRequired),
        "No schema changes" in str(error),
        _has_exchange_column(store),
    ) == (True, True, False)


def test_explicit_admin_schema_initialisation_may_migrate(new_store):
    # Arrange
    store = new_store()
    _make_v13(store)
    conn = connect(store)
    # Act
    try:
        init_schema(conn, allow_migration=True)
    finally:
        conn.close()
    # Assert
    assert _has_exchange_column(store)


# EOF
