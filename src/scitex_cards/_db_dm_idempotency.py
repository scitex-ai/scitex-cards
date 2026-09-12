#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Schema rung v14 -> v15: caller idempotency keys for DM submission."""

from __future__ import annotations

from typing import Any

from ._db_migrations import table_columns

__all__ = ["_migrate_v14_to_v15"]


def _migrate_v14_to_v15(conn: Any) -> None:
    """Add the nullable key and its per-sender uniqueness guard."""
    if "client_request_id" not in table_columns(conn, "dm_messages"):
        conn.execute("ALTER TABLE dm_messages ADD COLUMN client_request_id TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_dm_messages_client_request "
        "ON dm_messages(sender, client_request_id) "
        "WHERE client_request_id IS NOT NULL"
    )


# EOF
