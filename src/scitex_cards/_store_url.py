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
    "is_postgres_conninfo",
    "is_postgres_url",
    "to_paramstyle",
]

BACKEND_SQLITE = "sqlite"
BACKEND_POSTGRES = "postgresql"

#: Both spellings are accepted. libpq has honoured "postgres://" for years and
#: it appears in real config, so refusing it would be pedantry with an outage
#: attached.
POSTGRES_SCHEMES = ("postgresql://", "postgres://")


#: libpq accepts a KEYWORD/VALUE conninfo string as well as a URL --
#: ``host=127.0.0.1 port=5432 dbname=cards`` -- and psycopg.connect() takes it
#: happily. Only the URL form was recognised here, so a keyword/value DSN was
#: classified SQLITE and opened AS A FILENAME.
#:
#: That is not theoretical: on 2026-07-31, testing this very module, I passed
#: ``host=127.0.0.1 port=5432 dbname=scitex_cards user=scitex_cards`` and it
#: created a SQLite database in the working directory literally named that,
#: reported backend "sqlite", accepted writes, and answered queries. A wrong
#: store that works is the failure this package keeps meeting: nothing raises,
#: and the board looks healthy and empty.
#:
#: Detection is by KEYWORD rather than by "contains =", because a filesystem
#: path may legitimately contain "=" and must keep resolving to SQLite.
_LIBPQ_KEYWORDS = frozenset(
    {
        "host",
        "hostaddr",
        "port",
        "dbname",
        "user",
        "password",
        "passfile",
        "service",
        "sslmode",
        "sslrootcert",
        "connect_timeout",
        "application_name",
        "options",
    }
)


def is_postgres_conninfo(target: object) -> bool:
    """True iff ``target`` is a libpq KEYWORD/VALUE conninfo string.

    Requires the FIRST token to be ``<known-keyword>=``: a path such as
    ``/srv/data/a=b/cards.db`` contains an ``=`` but does not begin with a libpq
    keyword, so it stays SQLite.
    """
    if not isinstance(target, str):
        return False
    head = target.strip()
    if "=" not in head:
        return False
    first = head.split(maxsplit=1)[0]
    key, sep, _ = first.partition("=")
    return bool(sep) and key.lower() in _LIBPQ_KEYWORDS


def is_postgres_url(target: object) -> bool:
    """True iff ``target`` explicitly names a PostgreSQL server.

    Accepts BOTH spellings libpq accepts: the URL form (``postgresql://``,
    ``postgres://``) and the keyword/value conninfo form. The name is kept for
    its callers; see :func:`is_postgres_conninfo` for why the second form is
    here at all.

    Scheme comparison is case-insensitive because URL schemes are, and leading
    whitespace is tolerated because environment variables collect it. Anything
    that is not a string is not a URL -- the caller's own validation decides
    what a non-string target means.
    """
    if not isinstance(target, str):
        return False
    head = target.strip().lower()
    return head.startswith(POSTGRES_SCHEMES) or is_postgres_conninfo(target)


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
