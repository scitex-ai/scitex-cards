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

from ._db_dm_schema import DM_TABLES as _DM_TABLES
from ._db_migrations import table_columns

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
#:
#: v5 (DM into the store) adds ``dm_threads`` / ``dm_thread_member_events`` /
#: ``dm_messages`` / ``dm_receipts`` plus their append-only triggers. DMs were
#: the ONE piece of fleet data the store's protections did not cover: they
#: lived in a ``threads.json`` sidecar, so WAL, store-identity stamping,
#: tombstones, no-shrink, export and snapshot all applied to cards and to
#: nothing the operator actually talks through. See
#: ``docs/design/dm-into-cards-db.md``.
#:
#: v6 (the optimistic lock) adds ``tasks.revision``. It is incremented by every
#: row-level write (v7's trigger, below) and it CAN be asserted in a write's
#: WHERE clause — but only by a caller that opts in, and no public verb does.
#:
#: THAT DISTINCTION IS LOAD-BEARING AND THIS COMMENT USED TO ERASE IT. It read
#: "incremented by every row-level write and asserted in the write's WHERE
#: clause", in the present tense, as a property of the schema. Nothing asserted
#: it at all until #790 (2026-08-10). scitex-dev read this line, reasonably
#: concluded the lock was protecting them, skipped the check, and LOST AN EDIT
#: to a concurrent writer — reporting the work done, because nothing said
#: otherwise. A comment that overstates a guard is worse than no comment: it
#: converts a reader's diligence into a reason to skip the check.
#:
#: WHAT IS TRUE TODAY — one SQL site, two callers, no public entry:
#:   * ``_db_bootstrap._insert_tasks(..., expected_revision=N)`` appends
#:     ``WHERE tasks.revision = ?`` — the only such clause in the package.
#:   * ``_db_mirror._write_card(..., expected_revision=N)`` forwards to it.
#:   * ``update_task`` / ``add_task`` accept no such argument, so every write
#:     through the public surface remains LAST-WRITE-WINS, deliberately —
#:     :func:`_migrate_v6_to_v7` ruled REJECT-by-default unusable across a fleet
#:     that cannot be made uniformly current.
#: So ``revision`` is a CAPABILITY, not a PROTECTION. Pinned by
#: ``tests/scitex_cards/test__revision_is_opt_in.py`` so this text cannot drift
#: again in EITHER direction: it fails if the WHERE clause disappears, and it
#: fails if a public verb starts accepting the argument while this still says
#: none does.
#:
#: The counter exists so a
#: writer can tell "the row is there" (which ``rowcount == 1`` already answers)
#: apart from "nobody changed it under me since I read" — the question that
#: actually prevents a lost update, and the one sac's state-db taught us is easy
#: to believe you have answered when you have not: it checks ``rowcount`` in
#: eight places and carries no revision column, so every one of those checks
#: passes in exactly the case a lock exists to catch.
#:
#: v7 makes the v6 counter DB-ENFORCED via ``tasks_bump_revision``, so no writer
#: version can skip the increment. Application-side incrementing would require
#: every one of ~90 agent containers to be current for the lock to mean anything,
#: and that condition is not establishable — measured 2026-07-30, the fleet was
#: simultaneously running 0.13.5 / 0.17.5 / 0.18.0 / 0.22.0. See
#: :func:`_migrate_v6_to_v7` for why ASSIGN rather than REJECT semantics.
#: v10 makes ``notifications`` a table that can be SYNCED and that no client can
#: write incompletely: the five sync columns from creation, and a BEFORE INSERT
#: trigger that fills ``record_json`` from the row's own columns. Three separate
#: writers omitted that payload, and each omission took every card write down
#: fleet-wide — a barrier in the engine is the only kind every version of every
#: container obeys. See :func:`_db_migrations._migrate_v9_to_v10`.
#: v11 gives a MIGRATED store the foreign keys the schema has always declared.
#: Measured 2026-08-10, the live store held 1 of 4 (control: 278 total
#: pg_constraint rows), and the one it held covered two empty tables — so every
#: table with data had zero enforced referential integrity. Inline ``REFERENCES``
#: reaches fresh stores only, which is this chain's documented
#: ``CREATE TABLE IF NOT EXISTS`` hole in its second instance. See
#: :func:`_db_foreign_keys._migrate_v10_to_v11`.
#:
#: ═══ THIS NUMBER IS A CLAIM ABOUT A CLIENT, NOT ABOUT A DATABASE ═══
#:
#: Raising it here changes what THIS CODE will assert the next time it opens a
#: database. It changes NOTHING about any database until a client CARRYING this
#: value actually opens one — the rungs run from ``init_schema``, so an
#: unvisited database keeps whatever shape it had, indefinitely.
#:
#: SO A RELEASE NOTE SAYING "v11 ADDS THE FOREIGN KEYS" IS TRUE OF THE PACKAGE
#: AND FALSE OF THE FLEET, until propagation. When 0.37.0 was tagged, the live
#: cards database still had ZERO enforced referential integrity on every table
#: holding data — the repair existed, was proven on three Python versions and a
#: real PostgreSQL, and had not run anywhere.
#:
#: This is written here rather than only in a release note because the release
#: note is read once, by the person who already knows. The reader who needs it
#: is the one who finds this constant at 11 and concludes the databases are at
#: 11. On 2026-08-11 the fleet ran 0.35.1 for hours while a fix for a
#: write-outage sat published and unreachable, and three agents kept hitting the
#: bug it fixed. MERGED IS NOT PUBLISHED; PUBLISHED IS NOT DEPLOYED; AND A
#: SCHEMA VERSION IS NOT A SCHEMA.
#:
#: The same shape appears twice more in this package and both are deliberate:
#: ``require_pinned_store`` REPORTS and gates nothing while nothing is pinned,
#: and :func:`_db_foreign_keys._migrate_v10_to_v11` is a no-op on SQLite. In
#: each case the artifact exists and its effect does not yet, which is a state
#: worth being able to name.
SCHEMA_VERSION = 11


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

    A POSTGRESQL TARGET IS REFUSED HERE, LOUDLY, RATHER THAN COERCED. This
    function used to run ``Path(value)`` over whatever it was given, and
    ``Path`` accepts a DSN without complaint: ``postgresql://host/db`` becomes
    the relative path ``postgresql:/host/db`` — the ``//`` silently collapses.

    MEASURED 2026-07-31, and it is the worst failure this store can have.
    With ``SCITEX_CARDS_DB`` set to a PostgreSQL URL:

        list_tasks()            ->     0 cards   (SQLite target: 2960)
        resolve-store `exists`  ->  True         the guard reported healthy
        and on disk:  ./postgresql:/scitex_cards@127.0.0.1:5432/scitex_cards
                      a real, freshly created, EMPTY 217 KB SQLite database

    So it did not merely resolve wrong. The store layer MANUFACTURED a new
    empty store at the mangled path, initialised its schema, declared it
    present, and served nothing. An empty board that reports itself healthy is
    the exact outage this package's read-door and retirement guards exist to
    prevent — the one that previously took this store from 2170 rows to 18.

    ``_db.connect`` learned to dispatch a PostgreSQL target in #685, but the
    STORE reaches the database through THIS function, so closing one door left
    the other open. Refusing is correct even once PostgreSQL is fully
    supported: this function's contract is to return a filesystem Path, and a
    DSN is not one. Routing a server target belongs in the caller
    (:mod:`scitex_cards._store_target` already provides
    ``resolve_store_target`` / ``resolve_store_backend`` for that).
    """
    from ._store_target import StoreTargetIsNotAPath  # noqa: PLC0415
    from ._store_url import is_postgres_url  # noqa: PLC0415

    def _as_path(value: str | Path, source: str) -> Path:
        if is_postgres_url(str(value)):
            raise StoreTargetIsNotAPath(
                f"{source} names a PostgreSQL server, not a file path: "
                f"{value!r}. resolve_db_path returns a filesystem Path, and "
                "coercing a DSN here silently creates an EMPTY SQLite store at "
                "a mangled path and serves 0 cards while reporting healthy. "
                "Use scitex_cards._store_target.resolve_store_target() to get "
                "the target, or _db.connect() to open it."
            )
        return Path(value).expanduser()

    if explicit is not None:
        return _as_path(explicit, "the explicit target")
    env_val = os.environ.get(ENV_DB)
    if env_val:
        return _as_path(env_val, f"${ENV_DB}")
    legacy_val = os.environ.get(ENV_DB_DEPRECATED)
    if legacy_val:
        logger.warning(
            "%s is deprecated (package renamed 2026-07-16); rename the "
            "export to %s. The legacy value is honoured for one "
            "transition window only.",
            ENV_DB_DEPRECATED,
            ENV_DB,
        )
        return _as_path(legacy_val, f"${ENV_DB_DEPRECATED}")
    # CONFIG TIER — kept in lockstep with resolve_store_target, whose docstring
    # promises this function's precedence is mirrored exactly. Routed through
    # _as_path deliberately: this function is typed `-> Path`, so a DSN written
    # into the config must produce the same loud refusal an env DSN does rather
    # than being coerced into a mangled relative path.
    from ._config import store_config_target

    configured = store_config_target()
    if configured:
        return _as_path(configured, "the configured store target")
    # Final tier — DELEGATE to the ecosystem user-canonical resolver.
    # Imported lazily so a caller passing an explicit / env path never
    # hard-requires scitex_config to be importable.
    from scitex_config._ecosystem import local_state

    return local_state.user_path(PKG_SHORT, DEFAULT_DB_FILENAME)


# The core schema DDL and the table roster live in ``_db_schema_sql`` --
# ``_db`` owns connections, not the shape of the store. Re-exported under
# the historical private name so existing callers and tests are unaffected.
from ._db_schema_sql import SCHEMA_SQL as _SCHEMA_SQL
from ._db_schema_sql import SCHEMA_TABLES


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

    A POSTGRESQL TARGET IS NOT A PATH, and this is where that stops mattering to
    callers. ``path`` may be a PostgreSQL URL or a libpq keyword/value conninfo;
    those are dispatched to :func:`scitex_cards._backend_connect.connect` and
    come back as a ``StoreConnection``. The SQLite branch is unchanged, so every
    existing caller still receives the exact ``sqlite3.Connection`` it always
    has — this widens what ``connect`` ACCEPTS without changing what it returns
    for the only target in production today.

    THE DISPATCH IS THE FIRST STATEMENT, before any ``Path`` handling, and that
    ordering is the point. ``Path(dsn)`` on a conninfo does not raise: it yields
    a plausible relative path, and ``mkdir`` + ``sqlite3.connect`` then
    MANUFACTURE a SQLite file named after the DSN — which accepts writes and
    answers queries while the real server sits untouched. That file was created
    and observed while testing #682. Nothing raised, and the store looked
    healthy, which is exactly why the check cannot live further down.
    """
    from ._store_url import is_postgres_url  # noqa: PLC0415 -- import cycle

    target = str(path)
    if is_postgres_url(target):
        from ._backend_connect import connect as _connect_backend  # noqa: PLC0415
        from ._min_client_version import enforce_min_client_version  # noqa: PLC0415

        # PRAGMAs are deliberately NOT applied: journal_mode / synchronous /
        # busy_timeout / foreign_keys are SQLite storage-engine settings that the
        # server owns instead. ``rows_by_name`` because the store reads columns
        # by name throughout.
        pg = _connect_backend(target, read_only=False, rows_by_name=True)
        try:
            enforce_min_client_version(pg)
        except Exception:
            pg.close()
            raise
        return pg

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



# THE SCHEMA ASSERTION MOVED to `_db_init_schema`. This module owns CONNECTIONS;
# that one owns making an open connection carry the current shape (the currency
# gate, the DDL, the v1->v10 ladder, the stamp). Re-exported under the same name
# so every existing `from ._db import init_schema` resolves unchanged.
from ._db_init_schema import init_schema  # noqa: E402


def open_db(explicit: str | Path | None = None) -> sqlite3.Connection:
    """Resolve → connect → ensure schema. The one-call adapter entry point.

    Combines :func:`resolve_store_target`, :func:`connect`, and
    :func:`init_schema`. Returns a ready-to-use connection whose schema is
    guaranteed present (created on first open; a no-op on an existing DB).

    RESOLVES THE TARGET, NOT A PATH. ``resolve_db_path`` is typed ``-> Path``
    and now refuses a DSN outright, so routing through it made this function --
    the one-call entry point the canonical read path uses -- structurally unable
    to reach PostgreSQL, no matter that :func:`connect` had already learned to
    dispatch one. That is why ``_store_target``'s docstring could say the seam
    existed and NOTHING imported it. Filesystem-only callers (snapshots,
    backups, the on-disk health probes) keep ``resolve_db_path`` deliberately.
    """
    from ._store_target import resolve_store_target  # noqa: PLC0415

    return _open_at(resolve_store_target(explicit))


def _open_at(path: str | Path) -> sqlite3.Connection:
    conn = connect(path)
    init_schema(conn)
    return conn


#: ``db verify`` lives in :mod:`scitex_cards._db_verify` (this module owns the
#: schema and the connection; that one only INSPECTS a file). Re-exported so
#: every existing ``from ._db import verify`` keeps resolving unchanged.
from ._db_verify import verify  # noqa: E402  (re-export, after the definitions)


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
