#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adjacent schema rungs, moved verbatim from the flat modules.

v13 -> v14 lived in ``scitex_cards/_db_notification_exchange.py`` and
v14 -> v15 in ``scitex_cards/_db_dm_idempotency.py``; both files are now
compatibility shims re-exporting these same objects. The bodies below are
unchanged — same SQL, same guards, same order of operations.

``__module__`` is deliberately kept at the OLD dotted paths. Moving a
definition changes introspection and pickle-by-reference unless
addressed: unpickling a rung helper recorded before the move imports
``scitex_cards._db_notification_exchange`` / ``scitex_cards._db_dm_idempotency``
(the shims), which resolve to these very objects. Recording the new path
here instead would break every such reference for no behavioural gain.
"""

from __future__ import annotations

from typing import Any

from .._db_migrations import table_columns

__all__ = ["_migrate_v13_to_v14", "_migrate_v14_to_v15"]


def _migrate_v13_to_v14(conn: Any) -> None:
    """Add the nullable responder-issued exchange id without rewriting rows."""
    if "exchange_id" not in table_columns(conn, "notifications"):
        conn.execute("ALTER TABLE notifications ADD COLUMN exchange_id TEXT")


def _migrate_v14_to_v15(conn: Any) -> None:
    """Add the nullable key and its per-sender uniqueness guard."""
    if "client_request_id" not in table_columns(conn, "dm_messages"):
        conn.execute("ALTER TABLE dm_messages ADD COLUMN client_request_id TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_dm_messages_client_request "
        "ON dm_messages(sender, client_request_id) "
        "WHERE client_request_id IS NOT NULL"
    )


_migrate_v13_to_v14.__module__ = "scitex_cards._db_notification_exchange"
_migrate_v14_to_v15.__module__ = "scitex_cards._db_dm_idempotency"

# EOF
