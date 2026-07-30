#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Open the store, whichever backend it lives in, and speak its dialect for you.

THE DESIGN DECISION, AND WHY IT IS THE WHOLE POINT
--------------------------------------------------
There are 140 ``execute()`` sites across 10 modules, all written in SQLite's
``?`` paramstyle. The obvious port is to fix each one. That is 140 chances to
miss one, and every future query is a 141st -- a defence that depends on every
author remembering.

So the translation is bound to the CONNECTION instead. Code keeps writing ``?``;
a PostgreSQL connection rewrites it on the way through. Nobody has to remember,
because forgetting is not expressible.

This is the strongest of the three available places to put a guarantee, learned
the hard way today across two agents and several near-misses:

  1. make the bad state impossible to construct   <- this module
  2. make the rule unconditional
  3. remember to apply the rule correctly         <- everything that broke today

Level 1 is often unavailable -- you cannot choose that a store contains no NUL
bytes. Here it IS available, so it is taken.

WHAT THIS IS NOT
----------------
Read-only support. It opens a connection and runs queries; it does not port the
52 upsert sites, the 32 PRAGMA sites, or the 10 ``BEGIN IMMEDIATE`` blocks, and
it makes no claim about writes. Reads need none of those, which is why reads
come first: they are what lets the board see a PostgreSQL store at all.

AND IT IS VERIFIED AGAINST A REAL SERVER, NOT A FIXTURE
-------------------------------------------------------
Two fatal defects in scitex-db's migration tool on 2026-07-30 passed 196 tests
and appeared only against a live driver: a probe that aborted the transaction so
the first ``CREATE TABLE`` died, and indexes of excluded tables counted as
carried. Both looked like pure logic. Accordingly the tests for this module run
against the real PostgreSQL 18.4 holding the verified copy of the live store,
and skip -- loudly -- when it is unreachable, rather than passing on a mock.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from ._store_url import BACKEND_POSTGRES, backend_of, to_paramstyle

__all__ = ["StoreConnection", "connect"]


class StoreConnection:
    """A connection that accepts SQLite-dialect SQL for any backend.

    Deliberately thin. It is not an ORM and not an abstraction over the schema
    -- it translates paramstyle and otherwise gets out of the way, so a reader
    written against SQLite keeps working and stays readable.
    """

    def __init__(self, raw: Any, backend: str) -> None:
        self._raw = raw
        self._backend = backend

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def raw(self) -> Any:
        """The underlying driver connection, for backend-specific work.

        Named ``raw`` rather than exposed implicitly: reaching past the
        translation should be visible at the call site, because a statement
        written for one dialect will not run on the other.
        """
        return self._raw

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        """Run ``sql`` written in SQLite paramstyle, whatever the backend is."""
        return self._raw.execute(to_paramstyle(sql, self._backend), tuple(params))

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[Any]:
        """Convenience: psycopg cursors and sqlite3 cursors agree on this much."""
        return self.execute(sql, params).fetchall()

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> Any:
        return self.execute(sql, params).fetchone()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> "StoreConnection":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def connect(target: str, *, read_only: bool = True) -> StoreConnection:
    """Open ``target``, which is either a filesystem path or a PostgreSQL URL.

    ``read_only`` is honoured for SQLite (``mode=ro``, so a reader cannot
    create or modify a store by accident -- the failure mode that produced an
    empty board before). PostgreSQL read-only-ness is a property of the ROLE,
    not the connection, so it is not silently faked here: claiming to enforce
    something the connection does not enforce would be worse than not claiming
    it. Grant SELECT-only to the role if that is the guarantee you need.

    ``psycopg`` is imported lazily so SQLite-only deployments -- which is every
    deployment today -- do not need the driver installed.
    """
    backend = backend_of(target)
    if backend != BACKEND_POSTGRES:
        uri = f"file:{target}?mode=ro" if read_only else str(target)
        return StoreConnection(sqlite3.connect(uri, uri=read_only), backend)

    import psycopg  # noqa: PLC0415 -- optional dependency, resolved on demand

    return StoreConnection(psycopg.connect(target), backend)


# EOF
