#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Schema rung v13 -> v14: one delivery exchange id per notification."""

from __future__ import annotations

from typing import Any

from ._db_migrations import table_columns

__all__ = ["_migrate_v13_to_v14"]


def _migrate_v13_to_v14(conn: Any) -> None:
    """Add the nullable responder-issued exchange id without rewriting rows."""
    if "exchange_id" not in table_columns(conn, "notifications"):
        conn.execute("ALTER TABLE notifications ADD COLUMN exchange_id TEXT")


# EOF
