#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE CORE SCHEMA DDL, as text — extracted from :mod:`scitex_cards._db`.

WHY THIS IS ITS OWN MODULE. ``_db`` owns CONNECTIONS: resolving a target,
opening it, applying PRAGMAs, gating the client version, running migrations.
The DDL is a different thing entirely — it is the shape of the store, a
~125-line SQL literal that changes for schema reasons, not connection reasons.
Keeping the two together pushed ``_db`` past the module line limit and made
every schema edit look like a change to the connection code.

The convention already existed: schema v5's DM tables live in
:mod:`scitex_cards._db_dm_schema`, the retirement trigger in
:mod:`scitex_cards._store_retirement`, and the version-floor trigger in
:mod:`scitex_cards._schema_shape`. This module is the missing sibling holding
the CORE tables, so all the DDL now lives in modules named for what they
create.

IT ALSO GIVES THE POSTGRESQL PORT A SEAM. The dialect step measured for the
port (AUTOINCREMENT has no portable spelling; PostgreSQL has no
``CREATE TRIGGER IF NOT EXISTS``) has to translate this text. A single
importable constant is where such a translation belongs — far better than a
literal buried in the middle of the connection module.

``_db`` re-exports both names, so ``_db._SCHEMA_SQL`` keeps working for
existing callers and tests.
"""

from __future__ import annotations

from ._db_dm_schema import DM_TABLES as _DM_TABLES

# --------------------------------------------------------------------------- #
# Schema                                                                      #
# --------------------------------------------------------------------------- #
#
# Rule (RFC #348 §2): a field any read path filters/sorts on → typed column +
# index; rare / nested / opaque payloads → JSON TEXT. ``group`` is remapped to
# the ``grp`` column (``group`` is a SQL reserved word); the adapter/bootstrap
# translate the name so the Python/YAML field is unchanged. ``deadlines`` and
# ``_log_meta`` ride JSON TEXT columns; comments / edges / roles are child
# tables. Enum validity stays in ``_model._validate_tasks`` — no SQL CHECKs.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    kind           TEXT,
    blocker        TEXT,
    task           TEXT,
    note           TEXT,
    goal           TEXT,
    project        TEXT,
    repo           TEXT,
    host           TEXT,
    agent          TEXT,
    assignee       TEXT,
    scope          TEXT,
    grp            TEXT,
    priority       INTEGER,
    parent         TEXT,
    pr_url         TEXT,
    issue_url      TEXT,
    deadline       TEXT,
    scheduled      TEXT,
    created_at     TEXT,
    last_activity  TEXT,
    started_at     TEXT,
    finished_at    TEXT,
    created_by     TEXT,
    job_id         TEXT,
    command        TEXT,
    deadlines_json TEXT,
    log_meta_json  TEXT,
    row_order      INTEGER,
    card_json      TEXT,
    revision       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_agent    ON tasks(agent);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
CREATE INDEX IF NOT EXISTS idx_tasks_scope    ON tasks(scope);
CREATE INDEX IF NOT EXISTS idx_tasks_kind     ON tasks(kind);
CREATE INDEX IF NOT EXISTS idx_tasks_blocker  ON tasks(blocker);
CREATE INDEX IF NOT EXISTS idx_tasks_project  ON tasks(project);
CREATE INDEX IF NOT EXISTS idx_tasks_deadline ON tasks(deadline);
CREATE INDEX IF NOT EXISTS idx_tasks_parent   ON tasks(parent);
CREATE INDEX IF NOT EXISTS idx_tasks_pr_url   ON tasks(pr_url);

CREATE TABLE IF NOT EXISTS task_comments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
    seq     INTEGER NOT NULL,
    author  TEXT,
    ts      TEXT,
    kind    TEXT,
    text    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_task ON task_comments(task_id, seq);

CREATE TABLE IF NOT EXISTS task_edges (
    src_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE
                DEFERRABLE INITIALLY DEFERRED,
    -- dst_task_id is DELIBERATELY unconstrained: a forward reference to a card
    -- that does not exist yet is a SUPPORTED pattern (_diagram/_mermaid.py
    -- skips an unknown dst with a WARN rather than failing). SRC constrained,
    -- DST not — stated here because "task_edges is FK-free" has been relayed
    -- once already, and that phrasing drops a constraint that really exists.
    dst_task_id TEXT NOT NULL,
    edge_type   TEXT NOT NULL,
    PRIMARY KEY (src_task_id, dst_task_id, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON task_edges(dst_task_id);

CREATE TABLE IF NOT EXISTS task_roles (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
    who     TEXT NOT NULL,
    role    TEXT NOT NULL,
    PRIMARY KEY (task_id, who, role)
);
CREATE INDEX IF NOT EXISTS idx_roles_who ON task_roles(who);

CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    host_at_name TEXT,
    notify_json  TEXT,
    turn_url     TEXT,
    a2a_port     INTEGER,
    created_at   TEXT,
    last_seen    TEXT,
    record_json  TEXT
);
CREATE TABLE IF NOT EXISTS user_names (
    name    TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS idx_user_names_uid ON user_names(user_id);

CREATE TABLE IF NOT EXISTS inbox_recipients (
    recipient_id TEXT PRIMARY KEY
);

-- v8 adds msg_id / pushed_at / confirmed_at so the notification rail can move
-- INTO the store instead of living in runtime/todo.db beside it. `msg_id` makes
-- DM dedup exact -- the (event_type, card_id, ts, actor) key is many-to-one by
-- construction at second resolution. `confirmed_at` is what lets delivery be
-- proven by the RECIPIENT rather than by the sender's transport returning.
--
-- They MUST match _migrate_v7_to_v8 exactly; a fresh store and a migrated store
-- disagreeing on shape is this repo's own recorded v4 failure.
--
-- COMMENTS STAY OUTSIDE THE STATEMENT. SQLite stores the original CREATE text
-- in sqlite_master verbatim, so a comment inside the column list becomes part
-- of the stored schema and test__ddl's round-trip against executescript fails.
-- Measured: that test caught exactly this on the first push of v8.
CREATE TABLE IF NOT EXISTS notifications (
    id           TEXT PRIMARY KEY,
    recipient_id TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    card_id      TEXT,
    body         TEXT,
    actor        TEXT,
    ts           TEXT NOT NULL,
    seen         INTEGER NOT NULL DEFAULT 0,
    record_json  TEXT,
    msg_id       TEXT,
    pushed_at    TEXT,
    confirmed_at TEXT,
    seq          BIGINT
);
CREATE INDEX IF NOT EXISTS idx_notif_recipient_seen
    ON notifications(recipient_id, seen);

CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    thread_key TEXT NOT NULL,
    sender     TEXT NOT NULL,
    recipient  TEXT NOT NULL,
    body       TEXT NOT NULL,
    ts         TEXT NOT NULL,
    read       INTEGER NOT NULL DEFAULT 0,
    record_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_key, ts);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

#: Ordered tuple of every table name the schema creates — used by
#: :func:`verify` and the tests to assert completeness. The ``dm_*`` block is
#: schema v5; its DDL lives in :mod:`scitex_cards._db_dm_schema`.
SCHEMA_TABLES: tuple[str, ...] = (
    "tasks",
    "task_comments",
    "task_edges",
    "task_roles",
    "users",
    "user_names",
    "inbox_recipients",
    "notifications",
    "messages",
    *_DM_TABLES,
    "schema_meta",
)

__all__ = ["SCHEMA_SQL", "SCHEMA_TABLES"]

# EOF
