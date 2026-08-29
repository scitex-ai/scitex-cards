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

import re

__all__ = [
    "trigger_names",
    "table_names",
    "column_names",
    "index_names",
    "function_names",
    "has_trigger",
    "has_table",
    "has_column",
    "has_index",
    "has_function",
    "has_sequence",
    "row_values",
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
#: The column catalogue. ``PRAGMA table_info`` is SQLite-only, so the ladder's
#: column rungs could not be read on PostgreSQL at all -- and an unreadable rung
#: is reported ABSENT, which downgrades the observed version rather than
#: erroring. That is the same quiet direction :func:`has_trigger` documents.
_PG_COLUMNS = (
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema = current_schema() AND table_name = '{table}'"
)
# Indexes, functions and sequences are probed with the same care. All three are
# scoped to current_schema() FROM THE START (the scitex-dev #758 lesson, baked in
# rather than learned): unscoped, pg_indexes / pg_proc / pg_class span every
# schema the role can see, so a same-named object in another schema would read
# PRESENT, the DDL would be skipped, and the first read would die with
# UndefinedTable. current_schema() is where an unqualified CREATE lands, so the
# probe and the creator agree on which schema they mean.
_PG_INDEXES = (
    "SELECT indexname FROM pg_indexes "
    "WHERE schemaname = current_schema() AND tablename = '{table}'"
)
_PG_FUNCTIONS = (
    "SELECT proname FROM pg_proc p "
    "JOIN pg_namespace n ON n.oid = p.pronamespace "
    "WHERE n.nspname = current_schema()"
)
_PG_SEQUENCES = (
    "SELECT 1 FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = current_schema() AND c.relname = '{name}' AND c.relkind = 'S'"
)
_SQLITE_INDEXES = (
    "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = '{table}'"
)

#: The table name is INTERPOLATED, not bound, because the two engines disagree
#: on the placeholder (``?`` vs ``%s``) and this module deliberately holds no
#: paramstyle layer. Interpolation is only safe on a constrained identifier, so
#: the name is validated rather than trusted: every caller passes an internal
#: constant today, and this keeps that true if one ever stops.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def _sole_value(row):
    """The single column of a one-column row, whatever shape the row is.

    THREE ROW SHAPES REACH THIS MODULE and only two of them index by position.
    A plain ``sqlite3`` connection yields tuples; ``sqlite3.Row`` yields
    something that accepts BOTH ``row[0]`` and ``row["col"]``; psycopg's
    ``dict_row`` yields a real ``dict``, which accepts ONLY the name and raises
    ``KeyError: 0`` on the position.

    So ``{row[0] for row in rows}`` worked right up until a caller passed a
    connection opened with ``rows_by_name=True`` -- which is precisely what
    :func:`scitex_cards._db.connect` must do for PostgreSQL, because the rest of
    the store reads columns by name. The probe would then raise from inside a
    predicate whose whole job is to answer "does this table exist?", turning a
    routine question into a crash on the backend it was written to support.

    Keying off the row's TYPE rather than trying ``row[0]`` and catching the
    failure keeps the two cases explicit; a bare ``except KeyError`` here would
    also swallow a genuinely malformed row.
    """
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def row_values(row):
    """A row's columns IN SELECT ORDER, whatever shape the driver returned.

    THE COMPANION TO :func:`_sole_value`, for rows with more than one column.
    ``_sole_value`` covers ``fetchone()[0]``; this covers ``row[0], row[1]``,
    which a dozen call sites in this package still do.

    Why this is safe rather than a guess about dict ordering: psycopg builds a
    ``dict_row`` from ``cursor.description``, which IS the SELECT order, and
    Python dicts preserve insertion order. So ``list(row.values())[i]`` means
    exactly what ``row[i]`` meant on a tuple. Positional access is recoverable;
    it just is not spelled the same way on both drivers.

    Measured cost of not having this: three separate ``KeyError: 0`` crashes
    during the PostgreSQL port (``_read_stamps``, the canonical read's
    ``COUNT(*)``, and ``read_store_uuid``), each found one at a time by running
    the real server. Three instances is a pattern, and fixing a pattern one
    instance at a time is how the fourth one ships.
    """
    if isinstance(row, dict):
        return list(row.values())
    return list(row)


def _query(conn, sql: str) -> set[str]:
    cur = conn.execute(sql)
    rows = cur.fetchall() if hasattr(cur, "fetchall") else cur
    return {_sole_value(row) for row in rows}


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


def column_names(conn, table: str) -> set[str]:
    """Every column on ``table``; an empty set when the table is absent.

    An absent table yields an empty set on BOTH engines rather than raising:
    ``information_schema`` simply returns no rows, and ``PRAGMA table_info`` on
    an unknown table returns no rows too. The ladder's column rungs already ask
    ``has_table`` first, so this only has to agree with them, not duplicate them.
    """
    if not _IDENTIFIER_RE.match(table):
        raise ValueError(
            f"refusing to interpolate {table!r} into a catalogue query: only "
            "a plain SQL identifier is accepted here. See _IDENTIFIER_RE."
        )
    if _is_postgres(conn):
        return _query(conn, _PG_COLUMNS.format(table=table))
    # SQLite only. `PRAGMA table_info` returns (cid, name, type, ...) and the
    # name is at index 1, so `_sole_value` -- which takes the FIRST column --
    # would silently return the integer cid and every lookup would miss.
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    rows = cur.fetchall() if hasattr(cur, "fetchall") else cur
    return {row[1] for row in rows}


def has_column(conn, table: str, column: str) -> bool:
    return column in column_names(conn, table)


def index_names(conn, table: str) -> set[str]:
    """Every index on ``table`` in the current schema; empty when the table is absent.

    An absent table yields an empty set rather than raising, the same contract
    :func:`column_names` documents, so a caller may ask about an index on a table
    that does not exist yet and read "no rows" as "not there". SQLite's
    auto-indexes (``sqlite_autoindex_*``) are included, but a caller only ever
    asks about the named ``idx_*`` indexes the schema DDL installs, which are the
    explicit ones and never collide with an auto-index name.
    """
    if not _IDENTIFIER_RE.match(table):
        raise ValueError(
            f"refusing to interpolate {table!r} into a catalogue query: only "
            "a plain SQL identifier is accepted here. See _IDENTIFIER_RE."
        )
    if _is_postgres(conn):
        return _query(conn, _PG_INDEXES.format(table=table))
    return _query(conn, _SQLITE_INDEXES.format(table=table))


def has_index(conn, table: str, index: str) -> bool:
    return index in index_names(conn, table)


def function_names(conn) -> set[str]:
    """Every function in the current schema (PostgreSQL only).

    SQLite has no user-defined trigger functions -- its triggers are inline -- so
    there is nothing to enumerate there. The DDL gate only asks this on
    PostgreSQL; on SQLite it returns an empty set rather than raising, so the
    probe is safe to call from a backend-agnostic caller.
    """
    if not _is_postgres(conn):
        return set()
    return _query(conn, _PG_FUNCTIONS)


def has_function(conn, name: str) -> bool:
    return name in function_names(conn)


def has_sequence(conn, name: str) -> bool:
    """True when a SEQUENCE of this name exists in the current schema.

    PostgreSQL only. The v9 rail's generator is a real sequence, and the
    migration that installs it must not re-run (re-setting the column default and
    re-``setval``-ing) once it exists -- a DML-only role cannot do either, and
    re-running them changes nothing. SQLite has no sequences, so there is nothing
    to check and the answer is True: "the generator is present" is vacuously true
    for the ``rowid`` generator.
    """
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"refusing to interpolate {name!r} into a catalogue query: only "
            "a plain SQL identifier is accepted here. See _IDENTIFIER_RE."
        )
    if not _is_postgres(conn):
        return True
    return bool(_query(conn, _PG_SEQUENCES.format(name=name)))


# EOF
