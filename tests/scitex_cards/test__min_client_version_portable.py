#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``read_floor`` must answer the same question on both backends.

TWO SQLITE-SHAPED ASSUMPTIONS LIVED IN ONE SMALL FUNCTION, and both of them
fail CLOSED on PostgreSQL in a way that turns a no-op into a crash:

1. ``except sqlite3.OperationalError`` around the SELECT was how the function
   recognised "``schema_meta`` does not exist yet". PostgreSQL raises
   ``psycopg.errors.UndefinedTable`` for that condition, which the clause does
   not catch — so opening a BRAND-NEW PostgreSQL store would raise out of a
   function whose documented contract is "no floor stamped, gate is a no-op".

2. ``row[0]`` is POSITIONAL. ``sqlite3.Row`` accepts both ``row[0]`` and
   ``row["value"]``; psycopg's ``dict_row`` accepts only the latter.
   :func:`scitex_cards._backend_connect.connect` deliberately declines to paper
   over that asymmetry so the port finds these call sites while they are cheap.

The PostgreSQL tests run against a REAL server or skip loudly, for the reason
given in ``test__backend_connect.py``: a mock agrees with whatever you tell it.
The skip reason names the server, so a green run that never reached PostgreSQL
cannot be mistaken for one that did.
"""

import os
import sqlite3

import pytest

from scitex_cards._backend_connect import connect as backend_connect
from scitex_cards._min_client_version import (
    KEY_MIN_CLIENT_VERSION,
    read_floor,
)

PG_URL = os.environ.get(
    "SCITEX_CARDS_TEST_PG", "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
)


def _postgres_reachable() -> tuple[bool, str]:
    try:
        import psycopg
    except ImportError:
        return False, "psycopg is not installed (pip install 'psycopg[binary]')"
    try:
        psycopg.connect(PG_URL, connect_timeout=4).close()
    except Exception as exc:
        return False, f"{PG_URL} unreachable: {type(exc).__name__}"
    return True, ""


_PG_OK, _PG_WHY = _postgres_reachable()
requires_postgres = pytest.mark.skipif(
    not _PG_OK, reason=_PG_WHY or "postgres available"
)


def test_absent_schema_meta_reads_as_no_floor_on_sqlite(tmp_path):
    # Arrange
    conn = sqlite3.connect(tmp_path / "fresh.db")
    conn.row_factory = sqlite3.Row

    # Act
    floor = read_floor(conn)

    # Assert
    assert floor is None
    conn.close()


def test_stamped_floor_is_read_by_column_name_on_sqlite(tmp_path):
    # Arrange
    conn = sqlite3.connect(tmp_path / "stamped.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?)",
        (KEY_MIN_CLIENT_VERSION, "0.27.0"),
    )

    # Act
    floor = read_floor(conn)

    # Assert
    assert floor == "0.27.0"
    conn.close()


def test_present_table_without_the_key_reads_as_no_floor(tmp_path):
    # Arrange
    conn = sqlite3.connect(tmp_path / "empty_meta.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")

    # Act
    floor = read_floor(conn)

    # Assert
    assert floor is None
    conn.close()


@requires_postgres
def test_absent_schema_meta_reads_as_no_floor_on_postgres():
    """The case the old ``except sqlite3.OperationalError`` could not catch."""
    # Arrange -- an empty SCHEMA stands in for an empty database, because the
    # test role has no CREATE DATABASE privilege and a schema is equally
    # isolated. search_path is pointed at it so an unqualified `schema_meta`
    # resolves there and NOWHERE else.
    conn = backend_connect(PG_URL, read_only=False, rows_by_name=True)
    conn.execute("DROP SCHEMA IF EXISTS floorprobe CASCADE")
    conn.execute("CREATE SCHEMA floorprobe")
    conn.execute("SET search_path TO floorprobe")

    # Act
    floor = read_floor(conn)

    # Assert
    assert floor is None
    conn.execute("DROP SCHEMA floorprobe CASCADE")
    conn.commit()
    conn.close()


@requires_postgres
def test_stamped_floor_is_read_by_column_name_on_postgres():
    """The case ``row[0]`` could not survive: dict_row refuses position."""
    # Arrange
    conn = backend_connect(PG_URL, read_only=False, rows_by_name=True)
    conn.execute("DROP SCHEMA IF EXISTS floorprobe2 CASCADE")
    conn.execute("CREATE SCHEMA floorprobe2")
    conn.execute("SET search_path TO floorprobe2")
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?)",
        (KEY_MIN_CLIENT_VERSION, "0.27.0"),
    )

    # Act
    floor = read_floor(conn)

    # Assert
    assert floor == "0.27.0"
    conn.execute("DROP SCHEMA floorprobe2 CASCADE")
    conn.commit()
    conn.close()


# EOF
