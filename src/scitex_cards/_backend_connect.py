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

from ._store_url import (
    BACKEND_POSTGRES,
    backend_of,
    reject_attempted_dsn,
    reject_unexpanded_variable,
    to_paramstyle,
)

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

    # --- the write path -------------------------------------------------- #
    #
    # Added because the seam could not carry a write without them, which is why
    # nothing imported it: `_db.init_schema` alone needs executescript and
    # commit, and every mutation module needs commit/rollback. The module's
    # header used to say it "makes no claim about writes" -- these are that
    # claim, and each one is a DELEGATION rather than a reimplementation, so
    # the driver keeps owning its own semantics.

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> Any:
        """Batch form of :meth:`execute`, translated once rather than per row.

        GOES THROUGH A CURSOR because the two drivers disagree about where this
        method lives: ``sqlite3.Connection`` has ``executemany``, psycopg's
        ``Connection`` does NOT -- it is cursor-only, and calling it on the
        connection raises ``AttributeError``. Both drivers agree on
        ``cursor().executemany(...)``, so that is the form used.
        """
        cur = self._raw.cursor()
        cur.executemany(
            to_paramstyle(sql, self._backend), [tuple(p) for p in seq_of_params]
        )
        return cur

    def executescript(self, script: str) -> int:
        """Run a multi-statement DDL script; return how many statements ran.

        NOT delegated to ``sqlite3.executescript``, which psycopg does not have
        and which would silently commit first. Goes through the shared splitter
        so a trigger body's internal semicolons do not sever it -- a severed
        trigger's first fragment still parses as a complete CREATE TRIGGER, and
        every by-name presence probe then reports the truncated guard PRESENT.

        Returns a count for the same reason ``execute_ddl`` does: a script that
        installed nothing must not look like one that installed nine triggers.
        """
        from ._ddl import execute_ddl  # noqa: PLC0415 -- avoids an import cycle

        return execute_ddl(self, script)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> "StoreConnection":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def connect(
    target: str, *, read_only: bool = True, rows_by_name: bool = False
) -> StoreConnection:
    """Open ``target``, which is either a filesystem path or a PostgreSQL URL.

    ``read_only`` is honoured for SQLite (``mode=ro``, so a reader cannot
    create or modify a store by accident -- the failure mode that produced an
    empty board before). PostgreSQL read-only-ness is a property of the ROLE,
    not the connection, so it is not silently faked here: claiming to enforce
    something the connection does not enforce would be worse than not claiming
    it. Grant SELECT-only to the role if that is the guarantee you need.

    ``rows_by_name`` makes rows indexable as ``row["column"]`` on EITHER
    backend, which the store's callers require -- ``_db.connect`` sets
    ``sqlite3.Row`` and mutation code reads columns by name throughout.

    ONE ASYMMETRY REMAINS AND IS NOT PAPERED OVER. ``sqlite3.Row`` supports BOTH
    ``row["col"]`` and ``row[0]``; psycopg's ``dict_row`` supports only the
    former. So positional access keeps working on SQLite and raises on
    PostgreSQL. Faking it (a wrapper that accepts both) would hide the
    difference until a caller hit it on the live store; leaving it visible means
    the port finds those call sites while it is still cheap. Use column names.

    ``psycopg`` is imported lazily so SQLite-only deployments -- which is every
    deployment today -- do not need the driver installed.
    """
    # THE DOOR WHERE A GUESS DOES DAMAGE. Resolution is total and stays total;
    # opening is where a malformed DSN stops being a wrong string and becomes a
    # real, empty cards database that answers queries. Checked before the
    # dispatch below, because the SQLite branch CREATES its target.
    reject_unexpanded_variable(target)
    reject_attempted_dsn(target)

    backend = backend_of(target)
    if backend != BACKEND_POSTGRES:
        uri = f"file:{target}?mode=ro" if read_only else str(target)
        raw = sqlite3.connect(uri, uri=read_only)
        if rows_by_name:
            raw.row_factory = sqlite3.Row
        return StoreConnection(raw, backend)

    try:
        import psycopg  # noqa: PLC0415 -- optional dependency, resolved on demand
    except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent
        # A BARE ModuleNotFoundError NAMES THE SYMPTOM, NOT THE FIX. Measured
        # 2026-07-31: psycopg is declared in the `all`, `dev` and `postgres`
        # extras but NOT in `mcp`, and `mcp` is what both deployment paths
        # install -- the host venv and the container recipe. So the driver is
        # absent by default on every machine that has not asked for it, and the
        # failure surfaces only when something first touches PostgreSQL, which
        # is at cutover.
        #
        # Every other check passes in that state: the wheel contains this
        # module, connect() dispatches correctly, the schema builds. The code is
        # right and the environment cannot run it. Saying so, with the command
        # that fixes it, is the difference between a five-minute repair and a
        # confusing outage.
        raise ModuleNotFoundError(
            "PostgreSQL support needs the psycopg driver, which is NOT "
            "installed here. It ships in an optional extra that 'mcp' does not "
            "include, so a default install has no driver:\n"
            "    pip install 'scitex-cards[all]'\n"
            "    (or add psycopg[binary]>=3.1 to whatever installs this env)\n"
            f"target was: {target!r}"
        ) from exc

    if rows_by_name:
        from psycopg.rows import dict_row  # noqa: PLC0415

        return StoreConnection(psycopg.connect(target, row_factory=dict_row), backend)
    return StoreConnection(psycopg.connect(target), backend)


# EOF
