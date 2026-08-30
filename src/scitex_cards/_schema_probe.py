#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ask a store which tables and triggers it has, from ONE catalogue.

WHY THIS IS SAFETY CODE RATHER THAN PLUMBING. Three call sites decide whether a
store can be trusted by looking up guard names:

    _store_canonical_read.py   feeds read_status() -- the retirement gate
    _schema_shape.py           _has_table, the version ladder
    _schema_shape.py           _has_trigger, the version ladder

Each used to query a catalogue that does not exist on the store, and both
failure modes are bad while one of them is silent:

  * the query RAISES -- and a driver error can escape uncaught through the
    read path;
  * the query returns NOTHING -- the store looks unguarded, and with
    ``unguarded_store=STATUS_CURRENT`` (today's setting) it is then reported
    HEALTHY AND CURRENT. A store that cannot prove anything answers "yes".

The second is the one that matters. It is indistinguishable from a good answer,
and it is the shape of the failure that took this board from 2170 rows to 18.

WHAT THIS MODULE DELIBERATELY DOES NOT DO. It does not change
``unguarded_store``. That flag is REQUIRED at every call site precisely because
the right value changes over time, and its docstring names the condition:
``"current"`` is correct only until the guards are installed on live stores.
Measured 2026-07-31, the live store carries all nine -- but the fleet image has
not shipped, so stores this session has not touched may still be unguarded, and
flipping to ``"refuse"`` now would darken them. Porting the probe and flipping
the policy are two changes; this is the first.
"""

from __future__ import annotations

import re

__all__ = [
    "trigger_names",
    "table_names",
    "column_names",
    "has_trigger",
    "has_table",
    "has_column",
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
#: The column catalogue. The ladder's column rungs are read from here; an
#: unreadable rung is reported ABSENT, which downgrades the observed version
#: rather than erroring. That is the same quiet direction :func:`has_trigger`
#: documents.
_PG_COLUMNS = (
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema = current_schema() AND table_name = '{table}'"
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

    TWO ROW SHAPES REACH THIS MODULE and only one of them indexes by position.
    A plain driver cursor yields tuples; psycopg's ``dict_row`` yields a real
    ``dict``, which accepts ONLY the name and raises ``KeyError: 0`` on the
    position.

    So ``{row[0] for row in rows}`` worked right up until a caller passed a
    connection opened with ``rows_by_name=True`` -- which is precisely what
    :func:`scitex_cards._db.connect` must do, because the rest of the store
    reads columns by name. The probe would then raise from inside a predicate
    whose whole job is to answer "does this table exist?", turning a routine
    question into a crash on the store it was written to read.

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
    return _query(conn, _PG_TRIGGERS)


def table_names(conn) -> set[str]:
    """Every base table on the store, excluding engine-internal ones."""
    return _query(conn, _PG_TABLES)


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
    return _query(conn, _PG_COLUMNS.format(table=table))


def has_column(conn, table: str, column: str) -> bool:
    return column in column_names(conn, table)


# EOF
