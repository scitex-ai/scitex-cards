#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The NON-CARD sections of the shadow DB: users, notifications, messages.

Extracted from :mod:`scitex_cards._db_bootstrap`, which had grown to hold two
different jobs: the CARD pipeline (tasks + their comments / edges / roles, the
thing every hot path touches) and these three whole-section tables, which are
rebuilt only when their section hash moves (see :func:`_db_mirror._sync_sections`).

The ownership rule that governs this file, and that S1 nearly broke:

    A TABLE IS OWNED BY EXACTLY THE FILE THAT PRODUCES IT.

``users`` and ``notifications`` come from the legacy task-store doc (its
``users:`` and ``inboxes:`` sections). ``messages`` does NOT — it is derived
from the ``threads.json`` SIDECAR. A doc-write path that rebuilt ``messages`` would delete
every DM thread on every card write, which is why
:data:`_db_bootstrap._DOC_CLEAR_ORDER` excludes it.
"""

from __future__ import annotations

import secrets

from ._db_payload import card_payload_json as _record_json
from ._db_payload import json_or_none as _json_or_none


def _gen_id(prefix: str) -> str:
    """Fallback id (``<prefix>`` + 12 hex) for a record missing its own id."""
    return prefix + secrets.token_hex(6)


def _insert_users(conn, users: list) -> dict[str, int]:
    counts = {"users": 0, "user_names": 0}
    if not isinstance(users, list):
        return counts
    for u in users:
        if not isinstance(u, dict):
            continue
        uid = u.get("id")
        if not (isinstance(uid, str) and uid):
            continue
        conn.execute(
            "INSERT OR REPLACE INTO users"
            "(id, kind, host_at_name, notify_json, turn_url, a2a_port, "
            " created_at, last_seen, record_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uid,
                u.get("kind"),
                u.get("host_at_name"),
                _json_or_none(u.get("notify")),
                u.get("turn_url"),
                u.get("a2a_port"),
                u.get("created_at"),
                u.get("last_seen"),
                # Verbatim payload (v3): the columns are the index, this is
                # the record — the JSON exporter reproduces it exactly.
                # STRICT encoder (key order kept, no coercion, NULL on
                # non-round-trippable) — same policy as tasks.card_json.
                _record_json(u),
            ),
        )
        counts["users"] += 1
        names = u.get("names")
        if isinstance(names, list):
            for name in names:
                if not (isinstance(name, str) and name):
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO user_names(name, user_id) VALUES (?, ?)",
                    (name, uid),
                )
                counts["user_names"] += 1
    return counts


def _insert_notifications(conn, inboxes) -> int:
    if not isinstance(inboxes, dict):
        return 0
    n = 0
    for recipient_id, records in inboxes.items():
        if not (isinstance(recipient_id, str) and recipient_id):
            continue
        if not isinstance(records, list):
            continue
        # The map KEY is data too: a drained (empty) inbox must survive
        # the round-trip, and zero notification rows cannot carry the
        # key (schema v4).
        conn.execute(
            "INSERT OR REPLACE INTO inbox_recipients(recipient_id) VALUES (?)",
            (recipient_id,),
        )
        for r in records:
            if not isinstance(r, dict):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO notifications"
                "(id, recipient_id, event_type, card_id, body, actor, ts, "
                " seen, record_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.get("id") or _gen_id("n_"),
                    recipient_id,
                    "" if r.get("event_type") is None else str(r.get("event_type")),
                    r.get("card_id"),
                    r.get("body"),
                    r.get("actor"),
                    "" if r.get("ts") is None else str(r.get("ts")),
                    1 if r.get("seen") else 0,
                    _record_json(r),
                ),
            )
            n += 1
    return n


def _insert_messages(conn, threads) -> int:
    if not isinstance(threads, dict):
        return 0
    n = 0
    for thread_key, records in threads.items():
        if not (isinstance(thread_key, str) and thread_key):
            continue
        if not isinstance(records, list):
            continue
        for r in records:
            if not isinstance(r, dict):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO messages"
                "(id, thread_key, sender, recipient, body, ts, read, "
                " record_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.get("id") or _gen_id("m_"),
                    thread_key,
                    "" if r.get("from") is None else str(r.get("from")),
                    "" if r.get("to") is None else str(r.get("to")),
                    "" if r.get("body") is None else str(r.get("body")),
                    "" if r.get("ts") is None else str(r.get("ts")),
                    1 if r.get("read") else 0,
                    _record_json(r),
                ),
            )
            n += 1
    return n


__all__ = [
    "_gen_id",
    "_insert_messages",
    "_insert_notifications",
    "_insert_users",
]

# EOF
