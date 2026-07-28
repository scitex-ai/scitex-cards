#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite adapter — the canonical store lives here (schema, connect, resolution).

THE STORE IS THIS DATABASE
--------------------------
Post-cutover, the SQLite database created here is THE canonical store — not a
shadow of a YAML file. There is no second document to reconcile to: the CRUD /
MCP / ``load_doc`` / ``_save_doc_unlocked`` path reads and writes this database
directly (see :mod:`scitex_cards._store_backend`). ``$SCITEX_CARDS_DB`` (the
database path) is the SOLE store identity, stamped into ``schema_meta`` and
enforced by the ownership guard (:mod:`scitex_cards._dual_write`). The YAML that
remains in the package is a BACKUP/EXPORT rail (``db export``) and a set of
non-card sidecars — never the task store.

Adapter scope
-------------
stdlib ``sqlite3`` ONLY (no scitex-db, no third-party) — keeps the
corruption-adjacent canonical store dependency-light. On every writable connect
we set ``journal_mode=WAL``, ``synchronous=NORMAL``, ``busy_timeout=300000``
(5 min), ``foreign_keys=ON``. The schema is created idempotently
(``CREATE TABLE/INDEX IF NOT EXISTS``) and stamped with ``PRAGMA user_version``
plus ``schema_meta`` rows.

Path resolution — DELEGATED, never re-rolled
--------------------------------------------
Precedence: explicit arg → ``$SCITEX_CARDS_DB`` env → ``$SCITEX_TODO_DB``
(deprecated, warned) → the ecosystem ``local_state.user_path("cards",
"cards.db")``. We DELEGATE the final tier to
``scitex_config._ecosystem.local_state.user_path`` rather than re-rolling a
project/user precedence chain. ``user_path()`` is user-canonical and CANNOT
express a project scope — the whole point: the 2026-07-06 stale-store incident
was caused by a rolled-own resolver that ranked a project copy above the user
store. Resolves to ``~/.scitex/cards/cards.db``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

#: Canonical DB filename. ``.db`` (not ``.sqlite``) so a future
#: ``stx.io.load("cards.db")`` round-trips (scitex-io registers only ``.db``).
#: ``cards.db`` under ``~/.scitex/cards/`` is the operator-declared SSOT
#: target (2026-07-16); the pre-rename shadow lived at ``~/.scitex/todo/todo.db``
#: and is REBUILT by import at cutover, never moved or trusted as current.
DEFAULT_DB_FILENAME = "cards.db"

#: package short name (``scitex-cards`` with the ``scitex-`` prefix stripped),
#: the ``pkg_short`` passed to ``local_state.user_path``.
PKG_SHORT = "cards"

#: env var that overrides the resolved DB path entirely (2nd tier).
ENV_DB = "SCITEX_CARDS_DB"

#: pre-rename name of :data:`ENV_DB` (package renamed 2026-07-16). Honoured
#: for one transition window with a loud deprecation warning; the NEW name
#: wins when both are set. (``scitex_cards._env_compat`` also mirrors
#: ``SCITEX_CARDS_DB`` onto this name at import, so the pair cannot diverge
#: for in-package readers — this fallback exists for direct callers of
#: :func:`resolve_db_path` in processes that never imported the package root.)
ENV_DB_DEPRECATED = "SCITEX_TODO_DB"

#: schema version — mirrored into both ``PRAGMA user_version`` and the
#: ``schema_meta`` table so a fast gate (pragma) and a human-readable row exist.
#:
#: v2 (S2 read path) adds ``tasks.card_json``: the VERBATIM card mapping as JSON.
#: The typed columns are the INDEX; ``card_json`` is the PAYLOAD. This is not
#: redundancy — it is the only way a SQLite read can be indistinguishable from the
#: YAML read. MEASURED on the live 1,452-card store: 22 distinct card keys are NOT
#: in the column mapping (``deferred_at`` x20, ``subagent`` x8, ``blocked_by`` x3,
#: ``note_*`` variants, ``completed_at``, ``tasks_path``, ...), and 711 distinct key
#: ORDERS exist. A column-based reconstruction would silently DROP those keys and
#: re-order the rest — serving confidently-wrong cards to the whole fleet, which is
#: far worse than being slow. Reconstructing from ``card_json`` is exact BY
#: CONSTRUCTION, and it cannot rot as new fields are added.
#: v4 adds ``inbox_recipients`` — the inboxes: MAP KEYS, so a recipient
#: whose inbox is currently EMPTY (drained) still round-trips through the
#: JSON export instead of silently vanishing with their zero rows.
#:
#: v3 (S4 export rail) extends the same verbatim-payload rule to the NON-CARD
#: sections: ``users.record_json``, ``notifications.record_json``,
#: ``messages.record_json`` hold each record EXACTLY as the source document
#: carried it. Same rationale as ``card_json``: typed columns are the INDEX,
#: the JSON is the PAYLOAD — a column-based export would silently drop
#: unknown keys, and the JSON-snapshot backup rail (ADR-0010) must be exact
#: by construction.
SCHEMA_VERSION = 4


def resolve_db_path(explicit: str | Path | None = None) -> Path:
    """Resolve the DB path, following the precedence chain.

    Precedence (highest first):

    1. ``explicit`` argument (CLI ``--db`` / function arg),
    2. ``$SCITEX_CARDS_DB`` environment override,
    3. ``$SCITEX_TODO_DB`` — deprecated pre-rename name, honoured with a
       loud warning for one transition window,
    4. ``local_state.user_path("cards", "cards.db")`` — DELEGATED to the
       ecosystem user-canonical resolver (never a re-rolled precedence).

    Returns a :class:`~pathlib.Path`; does NOT create the file.
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    env_val = os.environ.get(ENV_DB)
    if env_val:
        return Path(env_val).expanduser()
    legacy_val = os.environ.get(ENV_DB_DEPRECATED)
    if legacy_val:
        logger.warning(
            "%s is deprecated (package renamed 2026-07-16); rename the "
            "export to %s. The legacy value is honoured for one "
            "transition window only.",
            ENV_DB_DEPRECATED,
            ENV_DB,
        )
        return Path(legacy_val).expanduser()
    # Final tier — DELEGATE to the ecosystem user-canonical resolver.
    # Imported lazily so a caller passing an explicit / env path never
    # hard-requires scitex_config to be importable.
    from scitex_config._ecosystem import local_state

    return local_state.user_path(PKG_SHORT, DEFAULT_DB_FILENAME)


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
_SCHEMA_SQL = """
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
    card_json      TEXT
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
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq     INTEGER NOT NULL,
    author  TEXT,
    ts      TEXT,
    kind    TEXT,
    text    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_task ON task_comments(task_id, seq);

CREATE TABLE IF NOT EXISTS task_edges (
    src_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    dst_task_id TEXT NOT NULL,
    edge_type   TEXT NOT NULL,
    PRIMARY KEY (src_task_id, dst_task_id, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON task_edges(dst_task_id);

CREATE TABLE IF NOT EXISTS task_roles (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
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
);
CREATE INDEX IF NOT EXISTS idx_user_names_uid ON user_names(user_id);

CREATE TABLE IF NOT EXISTS inbox_recipients (
    recipient_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS notifications (
    id           TEXT PRIMARY KEY,
    recipient_id TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    card_id      TEXT,
    body         TEXT,
    actor        TEXT,
    ts           TEXT NOT NULL,
    seen         INTEGER NOT NULL DEFAULT 0,
    record_json  TEXT
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
#: :func:`verify` and the tests to assert completeness.
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
    "schema_meta",
)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply the S0 connection PRAGMAs on a writable connection."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=300000")
    conn.execute("PRAGMA foreign_keys=ON")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating parent dirs) a writable connection with S0 PRAGMAs.

    Does NOT create the schema — call :func:`init_schema` (or the combined
    :func:`open_db`) for that. ``row_factory`` is :class:`sqlite3.Row` so
    callers get name-addressable rows.

    THE MIN-CLIENT-VERSION GATE lives here, not in :func:`open_db`, because
    this is the one function BOTH the read path (``_store_read_sqlite``
    calls ``connect`` directly) and the write path (``open_db`` -> this
    function, then :func:`init_schema`) open every connection through. See
    :mod:`scitex_cards._min_client_version` for the full incident this
    answers: an outdated client must ERROR the moment it opens the store,
    not merely warn. A brand-new file (no ``schema_meta`` table yet) has no
    floor stamped, so the gate is a no-op and :func:`init_schema` still runs
    normally afterwards.
    """
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    from ._min_client_version import enforce_min_client_version

    try:
        enforce_min_client_version(conn)
    except Exception:
        conn.close()
        raise
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """The column names actually present on ``table`` in THIS database file.

    The honest question a guard must ask. ``PRAGMA user_version`` is a STAMP —
    a number some code wrote — and a stamp is metadata, so it can outlive the
    thing it describes. The columns are the artifact itself.
    """
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Add ``tasks.card_json`` to a v1 DB. Idempotent, additive, no rewrite.

    ``CREATE TABLE IF NOT EXISTS`` is a NO-OP on an existing table — it will not
    add a column — so a DB created before v2 keeps the old shape forever unless
    something ALTERs it. That silently-missing column is precisely the sort of
    thing a version stamp would have papered over.

    Existing rows get ``card_json = NULL``: the column is added, but NOT
    back-filled (a back-fill needs the YAML, which this layer does not have).
    Those NULLs are load-bearing — they are what makes the S2 read guard REFUSE a
    DB that has not been re-imported, instead of quietly serving cards with their
    unknown fields stripped. Nothing back-fills them: the importer was removed
    with the YAML tier, so a database carrying these NULLs must be replaced with
    one a current version wrote.
    """
    if "card_json" not in table_columns(conn, "tasks"):
        conn.execute("ALTER TABLE tasks ADD COLUMN card_json TEXT")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Add ``record_json`` to users/notifications/messages. Idempotent, additive.

    Same contract as :func:`_migrate_v1_to_v2`: existing rows get NULL and are
    NOT back-filled here — the exporter REFUSES NULL payloads loudly, which is
    what forces the database to be replaced by a current one instead of silently
    exporting stripped records.
    """
    for table in ("users", "notifications", "messages"):
        if "record_json" not in table_columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN record_json TEXT")


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the schema idempotently + stamp version. Commits on success.

    Runs the ``CREATE TABLE/INDEX IF NOT EXISTS`` script, applies the additive
    column migrations, sets ``PRAGMA user_version=SCHEMA_VERSION``, and seeds the
    ``schema_meta`` rows (``schema_version`` always; ``created_at`` / ``source``
    only if absent so a re-init never clobbers the original provenance).
    """
    conn.executescript(_SCHEMA_SQL)
    _migrate_v1_to_v2(conn)
    _migrate_v2_to_v3(conn)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_meta(key, value) VALUES('created_at', ?)",
        (_utc_now_iso(),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_meta(key, value) VALUES('source', 'fresh')",
    )
    conn.commit()


def open_db(explicit: str | Path | None = None) -> sqlite3.Connection:
    """Resolve → connect → ensure schema. The one-call adapter entry point.

    Combines :func:`resolve_db_path`, :func:`connect`, and
    :func:`init_schema`. Returns a ready-to-use connection whose schema is
    guaranteed present (created on first open; a no-op on an existing DB).
    """
    return _open_at(resolve_db_path(explicit))


def _open_at(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    init_schema(conn)
    return conn


def verify(explicit: str | Path | None = None) -> dict:
    """Open the DB read/verify its integrity + report table row counts.

    Returns a JSON-friendly dict::

        {"path", "exists", "ok", "user_version", "schema_version",
         "quick_check", "tables": {<name>: <row_count>, ...}, "source"}

    ``ok`` is True iff the file exists, ``user_version`` and the
    ``schema_meta.schema_version`` both equal :data:`SCHEMA_VERSION`, every
    expected table is present, and ``PRAGMA quick_check`` returns ``ok``.
    Never raises on a merely-absent DB (``exists=False``, ``ok=False``).
    """
    path = resolve_db_path(explicit)
    report: dict = {
        "path": str(path),
        "exists": path.exists(),
        "ok": False,
        "user_version": None,
        "schema_version": None,
        "quick_check": None,
        "source": None,
        "tables": {},
    }
    if not path.exists():
        return report

    conn = connect(path)
    try:
        report["user_version"] = int(conn.execute("PRAGMA user_version").fetchone()[0])
        report["quick_check"] = conn.execute("PRAGMA quick_check").fetchone()[0]
        present = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        tables: dict[str, int] = {}
        for name in SCHEMA_TABLES:
            if name in present:
                tables[name] = int(
                    conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                )
        report["tables"] = tables
        meta = {
            row[0]: row[1] for row in conn.execute("SELECT key, value FROM schema_meta")
        }
        report["schema_version"] = meta.get("schema_version")
        report["source"] = meta.get("source")
        all_tables_present = all(t in present for t in SCHEMA_TABLES)
        report["ok"] = bool(
            report["user_version"] == SCHEMA_VERSION
            and report["schema_version"] == str(SCHEMA_VERSION)
            and all_tables_present
            and report["quick_check"] == "ok"
        )
    finally:
        conn.close()
    return report


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with the canonical ``Z`` suffix.

    Same second-resolution shape as the task / user / inbox timestamps so
    the DB provenance stamps match the YAML store on disk.
    """
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


__all__ = [
    "DEFAULT_DB_FILENAME",
    "ENV_DB",
    "PKG_SHORT",
    "SCHEMA_TABLES",
    "SCHEMA_VERSION",
    "connect",
    "init_schema",
    "open_db",
    "resolve_db_path",
    "table_columns",
    "verify",
]

# EOF
