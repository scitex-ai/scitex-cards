#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ask a store which tables and triggers it has, without assuming SQLite.

WHY THIS IS SAFETY CODE RATHER THAN PLUMBING. Three call sites decide whether a
store can be trusted by looking up guard names:

    _store_canonical_read.py:153   feeds read_status() -- the retirement gate
    _schema_shape.py:316           _has_table, the version ladder
    _schema_shape.py:323           _has_trigger, the version ladder

All three query ``sqlite_master``, which does not exist on PostgreSQL. Both
failure modes are bad and one of them is silent:

  * the query RAISES -- and ``_store_canonical_read`` catches only
    ``sqlite3.OperationalError``, so a psycopg error escapes uncaught through
    the read path;
  * the query returns NOTHING -- the store looks unguarded, and with
    ``unguarded_store=STATUS_CURRENT`` (today's setting) it is then reported
    HEALTHY AND CURRENT. A store that cannot prove anything answers "yes".

The second is the one that matters. It is indistinguishable from a good answer,
and it is the shape of the failure that took this board from 2170 rows to 18.

WHAT THIS MODULE DELIBERATELY DOES NOT DO. It does not change
``unguarded_store``. That flag is REQUIRED at every call site precisely because
the right value changes over time, and its docstring names the condition:
``"current"`` is correct only until the guards are installed on live stores.
Measured 2026-07-31, the canonical SQLite store and the PostgreSQL store each
carry all nine -- but the fleet image has not shipped, so stores this session
has not touched may still be unguarded, and flipping to ``"refuse"`` now would
darken them. Porting the probe and flipping the policy are two changes; this is
the first.
"""

from __future__ import annotations

__all__ = [
    "trigger_names",
    "table_names",
    "has_trigger",
    "has_table",
]

#: PostgreSQL: exclude ``tgisinternal`` rows -- every FK constraint installs
#: internal triggers, and counting those would report a guard-free store as
#: richly guarded.
_PG_TRIGGERS = (
    "SELECT t.tgname FROM pg_trigger t "
    "JOIN pg_class c ON c.oid = t.tgrelid "
    "WHERE NOT t.tgisinternal"
)
_PG_TABLES = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
)
_SQLITE_TRIGGERS = "SELECT name FROM sqlite_master WHERE type = 'trigger'"
_SQLITE_TABLES = (
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
)


def _is_postgres(conn) -> bool:
    """Detect the backend from the connection, not from a caller's claim.

    A caller that has to be TOLD which backend it holds is a caller that can be
    told wrong, and being wrong here means querying the catalogue that does not
    exist. ``_backend_connect.StoreConnection`` carries an explicit ``backend``;
    a raw driver connection is identified by its module.
    """
    backend = getattr(conn, "backend", None)
    if isinstance(backend, str):
        return backend.startswith("postgres")
    return type(conn).__module__.split(".")[0] in {"psycopg", "psycopg2"}


def _query(conn, sql: str) -> set[str]:
    cur = conn.execute(sql)
    rows = cur.fetchall() if hasattr(cur, "fetchall") else cur
    return {row[0] for row in rows}


def trigger_names(conn) -> set[str]:
    """Every non-internal trigger on the store."""
    return _query(conn, _PG_TRIGGERS if _is_postgres(conn) else _SQLITE_TRIGGERS)


def table_names(conn) -> set[str]:
    """Every base table on the store, excluding engine-internal ones."""
    return _query(conn, _PG_TABLES if _is_postgres(conn) else _SQLITE_TABLES)


def has_trigger(conn, name: str) -> bool:
    return name in trigger_names(conn)


def has_table(conn, name: str) -> bool:
    return name in table_names(conn)


# EOF
