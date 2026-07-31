#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Opening a WRITE transaction on either backend.

``BEGIN IMMEDIATE`` is SQLite-only spelling. Issued against PostgreSQL it does
not degrade, it raises::

    syntax error at or near "IMMEDIATE"
    LINE 1: BEGIN IMMEDIATE

which is how every DM write died after the PostgreSQL cutover — before writing
anything, so no data was harmed and no partial row landed. Reported by
scitex-db 2026-08-01 with a live reproduction.

WHY NOT JUST ``BEGIN``
----------------------
Because ``IMMEDIATE`` is not decoration. SQLite takes the write lock at BEGIN
rather than at first write, so two appenders SERIALISE instead of racing. The
DM append reads ``max(seq)`` and then inserts ``seq + 1``; ``_dm_write_rows``
says so in as many words ("Read inside the caller's ``BEGIN IMMEDIATE``, so two
writers cannot observe" the same value).

PostgreSQL's default isolation is READ COMMITTED, under which a plain ``BEGIN``
lets both appenders read the SAME ``max(seq)`` and both insert. So swapping in
``BEGIN`` would have parsed, run, passed a smoke test, and silently
reintroduced exactly the race ``IMMEDIATE`` exists to prevent — a strictly
worse outcome than the syntax error, which at least announced itself.

SERIALIZABLE would detect the conflict, but by ABORTING one side with a
serialization failure, which every call site would then have to retry. The
construct that matches ``BEGIN IMMEDIATE``'s actual behaviour — block, do not
abort — is a transaction-scoped advisory lock. It is released automatically on
commit OR rollback, so no path can leak it.

THE LOCK IS STORE-WIDE, matching SQLite, where the write lock is over the whole
database file. That is coarser than PostgreSQL needs, and deliberately so: this
is a compatibility seam, not a performance rewrite. Making it finer-grained
would be a real change in concurrency semantics between the two backends, which
is the class of difference this module exists to abolish.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = ["STORE_WRITE_LOCK_KEY", "begin_write_transaction"]

#: The advisory-lock key standing for "the scitex-cards store write lock".
#: Arbitrary but FIXED and store-wide: every writer must name the same key or
#: they do not exclude each other. Fits in the int64 psycopg sends.
STORE_WRITE_LOCK_KEY: Final[int] = 0x5CDB000000000001


def begin_write_transaction(conn: Any) -> None:
    """Open a transaction that EXCLUDES other writers, on either backend.

    Parameters
    ----------
    conn
        An open store connection (``sqlite3`` or the PostgreSQL-backed
        :class:`~scitex_cards._backend_connect.StoreConnection`).

    Notes
    -----
    The ``rollback()`` on the PostgreSQL path is deliberate and is the same
    precaution ``_store_canonical_read`` takes: ``BEGIN`` must be the first
    statement of a transaction, and psycopg may already have opened one
    implicitly on an earlier statement.

    The lock is taken INSIDE the transaction, so it is held for exactly the
    transaction's lifetime and released by commit or rollback alike.
    """
    from ._schema_probe import _is_postgres  # noqa: PLC0415

    if _is_postgres(conn):
        conn.rollback()
        conn.execute("BEGIN")
        # The key is a module constant, never caller input, so interpolating it
        # keeps this free of paramstyle translation on a statement that only
        # ever runs against PostgreSQL.
        conn.execute(f"SELECT pg_advisory_xact_lock({STORE_WRITE_LOCK_KEY})")
    else:
        conn.execute("BEGIN IMMEDIATE")
