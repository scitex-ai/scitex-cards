#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Null-safe equality, spelled for whichever backend is actually connected.

WHY THIS EXISTS — THERE IS NO SINGLE SPELLING THAT WORKS ON BOTH. This is not a
style preference or a portability nicety; it is a hard constraint measured on
this deployment 2026-08-02:

    backend                     ``col IS ?``   ``col IS NOT DISTINCT FROM ?``
    host SQLite 3.37.2          WORKS          SYNTAX ERROR (needs >= 3.39)
    PostgreSQL                  SYNTAX ERROR   WORKS

``IS NOT DISTINCT FROM`` is the standard-SQL spelling and SQLite only learned it
in 3.39 (2022-06). The host runs 3.37.2, the containers run 3.45.1 — which is
exactly why the mismatch was INVISIBLE in CI and in every container, and why it
took a 36-hour silent delivery outage to surface (every enqueue raised, and a
fail-soft ``except`` swallowed it).

Fixing that outage meant rewriting the inbox's six comparisons to SQLite's
``IS ?``. Correct for the rail it runs on — and a hard blocker for moving that
rail onto Postgres, where ``IS $1`` does not parse. So the two constraints are
genuinely irreconcilable in a single literal, and the only honest resolution is
to pick the spelling at the point where the backend is known.

Paramstyle is NOT this module's problem: ``_db`` already translates ``?`` to
``%s``, so every fragment here is written with ``?`` and callers keep one
paramstyle. The OPERATOR is the part no paramstyle translator can fix, because
it is a different SQL construct rather than a different placeholder.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "POSTGRES_NULL_SAFE",
    "SQLITE_NULL_SAFE",
    "null_safe_eq",
    "null_safe_eq_for",
]

#: Standard SQL. Postgres always; SQLite only from 3.39.
POSTGRES_NULL_SAFE = "IS NOT DISTINCT FROM"

#: SQLite's null-safe ``IS``, available in every SQLite we support.
SQLITE_NULL_SAFE = "IS"


def null_safe_eq(column: str, *, postgres: bool) -> str:
    """Return ``<column> <op> ?`` using the null-safe operator for the backend.

    ``postgres=True`` yields the standard spelling, otherwise SQLite's ``IS``.
    Always emits a ``?`` placeholder — ``_db`` translates paramstyle.
    """
    operator = POSTGRES_NULL_SAFE if postgres else SQLITE_NULL_SAFE
    return f"{column} {operator} ?"


def null_safe_eq_for(conn: Any, column: str) -> str:
    """``null_safe_eq`` keyed off the CONNECTION rather than a caller's belief.

    Takes the live connection because the backend is a property of what is
    actually open, not of what the caller thinks is configured. Reading it from
    the connection is what keeps a call site from being correct in tests and
    wrong in production.
    """
    from ._schema_probe import _is_postgres  # noqa: PLC0415 -- import cycle

    return null_safe_eq(column, postgres=bool(_is_postgres(conn)))


# EOF
