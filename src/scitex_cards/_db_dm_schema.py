#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Schema v5 — the DM tables, and the triggers that make them append-only.

DESIGN: ``docs/design/dm-into-cards-db.md`` §3 (schema) and §4 (append-only
made unreachable rather than guarded).

WHY THESE TABLES EXIST. Until v5, DMs were the ONE piece of fleet data the
canonical store's protections did not cover. Cards got WAL, store-identity
stamping, tombstones, a no-shrink guard, export and snapshot; the operator's
actual conversation with the fleet got a ``threads.json`` file next to the
database and none of it. The v3 ``messages`` table looks like a counterexample
and is not: it is a one-directional DERIVED MIRROR with no live writer, so it
carried DM bytes without ever being the store of them.

THE ONE STRUCTURAL DEPARTURE from ``messages`` is the absence of a
``recipient`` column. ``dm_messages`` records who SENT; the membership event
log records who can SEE. A scalar ``recipient`` is the schema-level reason a
DM can never have three participants, so copying it forward would have carried
that limit into the new store for free.

APPEND-ONLY IS ENFORCED BY TRIGGERS, NOT BY PYTHON. A Python guard binds only
the callers that go through it; a trigger binds the ``sqlite3`` CLI, a stray
script and every future caller. Stated honestly: ``DROP TRIGGER``, a raw file
copy and ``sqlite3 .recover`` still bypass all of it. The triggers close the
ACCIDENT class, not the adversary class.

This DDL lives in its own module (rather than inline in :mod:`scitex_cards._db`)
so the fresh-database path and the additive v4→v5 migration run BYTE-IDENTICAL
statements. Two spellings of one schema is how a migrated database and a fresh
database quietly stop being the same shape.
"""

from __future__ import annotations

import sqlite3

#: Every table this script creates, in dependency order (parents first).
DM_TABLES: tuple[str, ...] = (
    "dm_threads",
    "dm_thread_member_events",
    "dm_messages",
    "dm_receipts",
)

#: Every trigger this script creates. Named so a guard can assert the ENGINE
#: carries the refusal, rather than trusting that the DDL was run.
DM_TRIGGERS: tuple[str, ...] = (
    "dm_threads_no_delete",
    "dm_thread_member_events_no_delete",
    "dm_receipts_no_delete",
    "dm_messages_no_delete",
    "dm_messages_immutable",
)

SCHEMA_SQL_V5 = """
CREATE TABLE IF NOT EXISTS dm_threads (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    title        TEXT,
    created_at   TEXT NOT NULL,
    created_by   TEXT,
    origin_host  TEXT NOT NULL,
    record_json  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dm_thread_member_events (
    id           TEXT PRIMARY KEY,
    thread_id    TEXT NOT NULL REFERENCES dm_threads(id),
    member       TEXT NOT NULL,
    action       TEXT NOT NULL,
    ts           TEXT NOT NULL,
    actor        TEXT,
    origin_host  TEXT NOT NULL,
    record_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dm_member_thread
    ON dm_thread_member_events(thread_id, ts, id);
CREATE INDEX IF NOT EXISTS idx_dm_member_member
    ON dm_thread_member_events(member);

CREATE TABLE IF NOT EXISTS dm_messages (
    id           TEXT PRIMARY KEY,
    thread_id    TEXT NOT NULL REFERENCES dm_threads(id),
    sender       TEXT NOT NULL,
    body         TEXT NOT NULL,
    ts           TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    origin_host  TEXT NOT NULL,
    deleted_at   TEXT,
    deleted_by   TEXT,
    record_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dm_messages_thread
    ON dm_messages(thread_id, seq, id);
CREATE INDEX IF NOT EXISTS idx_dm_messages_sender
    ON dm_messages(sender, ts);

CREATE TABLE IF NOT EXISTS dm_receipts (
    message_id   TEXT NOT NULL REFERENCES dm_messages(id),
    reader       TEXT NOT NULL,
    read_at      TEXT NOT NULL,
    origin_host  TEXT NOT NULL,
    source       TEXT NOT NULL,
    PRIMARY KEY (message_id, reader)
);
CREATE INDEX IF NOT EXISTS idx_dm_receipts_reader ON dm_receipts(reader);

CREATE TRIGGER IF NOT EXISTS dm_threads_no_delete
BEFORE DELETE ON dm_threads BEGIN
    SELECT RAISE(ABORT, 'dm_threads is append-only: rows are never removed');
END;

CREATE TRIGGER IF NOT EXISTS dm_thread_member_events_no_delete
BEFORE DELETE ON dm_thread_member_events BEGIN
    SELECT RAISE(ABORT,
        'dm_thread_member_events is append-only: leaving is a leave event');
END;

CREATE TRIGGER IF NOT EXISTS dm_receipts_no_delete
BEFORE DELETE ON dm_receipts BEGIN
    SELECT RAISE(ABORT,
        'dm_receipts is append-only: a read receipt is never withdrawn');
END;

CREATE TRIGGER IF NOT EXISTS dm_messages_no_delete
BEFORE DELETE ON dm_messages BEGIN
    SELECT RAISE(ABORT,
        'dm_messages is append-only: tombstone via deleted_at, never DELETE');
END;

CREATE TRIGGER IF NOT EXISTS dm_messages_immutable
BEFORE UPDATE ON dm_messages
WHEN OLD.thread_id   IS NOT NEW.thread_id
  OR OLD.sender      IS NOT NEW.sender
  OR OLD.body        IS NOT NEW.body
  OR OLD.ts          IS NOT NEW.ts
  OR OLD.seq         IS NOT NEW.seq
  OR OLD.origin_host IS NOT NEW.origin_host
  OR OLD.record_json IS NOT NEW.record_json
BEGIN
    SELECT RAISE(ABORT,
        'dm_messages rows are immutable except deleted_at/deleted_by');
END;
"""


def migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """Create the v5 DM tables + triggers on ANY database. Idempotent, additive.

    Every statement is ``IF NOT EXISTS``, so this is a no-op on a database that
    already carries them and a plain create on one that does not. It touches no
    existing table and no existing row: v4 data is untouched by construction,
    which is what makes M0 need no reversal — inert tables have nothing to undo.

    Called from :func:`scitex_cards._db.init_schema`, i.e. on EVERY open. That
    is deliberate: ``CREATE TABLE IF NOT EXISTS`` in the main script would also
    cover a fresh file, but a v4 database that is merely opened (never
    re-created) would otherwise keep the old shape forever while its
    ``user_version`` stamp claimed v5. The stamp is metadata; the tables are
    the artifact.
    """
    conn.executescript(SCHEMA_SQL_V5)


__all__ = ["DM_TABLES", "DM_TRIGGERS", "SCHEMA_SQL_V5", "migrate_v4_to_v5"]

# EOF
