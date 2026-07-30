#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decide WHICH backend a store target names, and translate SQL paramstyle.

This is the pure half of PostgreSQL support: the part that can be written and
tested without a server. It exists because 140 ``execute()`` call sites across
10 modules currently hardcode SQLite's ``?`` paramstyle, and PostgreSQL uses
``%s``. Translating at each call site would be 140 opportunities to get it
wrong; translating in one place is one.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not connect, and it does not claim the reader works. Two fatal defects
in scitex-db's migration tool on 2026-07-30 were invisible to 196 passing tests
and appeared only against a real driver -- a probe that aborted the transaction
so the first CREATE TABLE died, and indexes of excluded tables counted as
carried. Both were pure-logic-looking bugs that pure logic could not catch. So
the connection layer is being built against a real PostgreSQL, and nothing here
should be read as evidence that it works.

A PATH IS THE LEGACY SPELLING, AND STAYS THE DEFAULT
----------------------------------------------------
``$SCITEX_CARDS_DB`` has always held a filesystem path, and every existing
deployment sets one. So anything that is not explicitly a PostgreSQL URL
resolves to SQLite: that keeps every current store working untouched. Only an
explicit ``postgresql://`` (or ``postgres://``) opts in.

Note this module answers "which backend", never "which store". A DSN is a
LOCATION and locations fail both ways -- the same database reached as
``localhost`` and ``127.0.0.1`` is string-unequal, and a restored backup at the
same address is a different store wearing the right name. Store IDENTITY lives
inside the store (``schema_meta.store_uuid``) and is checked there. This module
must never be mistaken for that check.
"""

from __future__ import annotations

__all__ = [
    "BACKEND_POSTGRES",
    "BACKEND_SQLITE",
    "POSTGRES_SCHEMES",
    "backend_of",
    "is_postgres_url",
    "to_paramstyle",
]

BACKEND_SQLITE = "sqlite"
BACKEND_POSTGRES = "postgresql"

#: Both spellings are accepted. libpq has honoured "postgres://" for years and
#: it appears in real config, so refusing it would be pedantry with an outage
#: attached.
POSTGRES_SCHEMES = ("postgresql://", "postgres://")


def is_postgres_url(target: object) -> bool:
    """True iff ``target`` explicitly names a PostgreSQL server.

    Scheme comparison is case-insensitive because URL schemes are, and leading
    whitespace is tolerated because environment variables collect it. Anything
    that is not a string is not a URL -- the caller's own validation decides
    what a non-string target means.
    """
    if not isinstance(target, str):
        return False
    head = target.strip().lower()
    return head.startswith(POSTGRES_SCHEMES)


def backend_of(target: object) -> str:
    """Return the backend constant for ``target``.

    Deliberately total: every input gets an answer, and the answer for anything
    that is not a PostgreSQL URL is SQLite. That is what keeps existing stores
    -- all of which are paths -- working with no migration of configuration.
    """
    return BACKEND_POSTGRES if is_postgres_url(target) else BACKEND_SQLITE


def to_paramstyle(sql: str, backend: str) -> str:
    """Rewrite SQLite ``?`` placeholders for ``backend``.

    SQLite is returned unchanged -- the SQL in this package is already written
    in its paramstyle, so the common path costs nothing and cannot corrupt.

    A ``?`` INSIDE A STRING LITERAL IS NOT A PLACEHOLDER and must survive. This
    is not hypothetical: card and message bodies routinely contain question
    marks, and a naive ``sql.replace("?", "%s")`` would silently corrupt any
    literal containing one -- the kind of defect that produces wrong data rather
    than an error. Single-quoted literals are scanned and skipped, including
    SQL's doubled-quote escape (``'it''s'``).

    A literal ``%`` must also be doubled for the ``%s`` paramstyle, or a LIKE
    pattern such as ``'%foo%'`` becomes a format specifier and raises at
    execution time.
    """
    if backend != BACKEND_POSTGRES:
        return sql

    out: list[str] = []
    in_literal = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_literal:
            if ch == "'":
                # A doubled quote is an escaped quote, not the end of the
                # literal: consume both and stay inside.
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    out.append("''")
                    i += 2
                    continue
                in_literal = False
            out.append("%%" if ch == "%" else ch)
        elif ch == "'":
            in_literal = True
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# EOF
