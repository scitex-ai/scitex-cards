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

import re

__all__ = [
    "BACKEND_POSTGRES",
    "BACKEND_SQLITE",
    "POSTGRES_SCHEMES",
    "UnrecognisedStoreTarget",
    "backend_of",
    "is_attempted_dsn",
    "is_postgres_conninfo",
    "is_postgres_url",
    "reject_attempted_dsn",
    "to_paramstyle",
]

BACKEND_SQLITE = "sqlite"
BACKEND_POSTGRES = "postgresql"

#: Both spellings are accepted. libpq has honoured "postgres://" for years and
#: it appears in real config, so refusing it would be pedantry with an outage
#: attached.
POSTGRES_SCHEMES = ("postgresql://", "postgres://")

#: The scheme without its slashes. A DSN that has been through ``Path()`` has
#: had "//" collapsed to "/", so it no longer matches a scheme and no longer
#: looks like anything but a relative directory -- which is exactly how one got
#: built on disk. Matching the prefix catches the mangled form.
POSTGRES_PREFIXES = ("postgresql:", "postgres:")


class UnrecognisedStoreTarget(RuntimeError):
    """A target names a server, malformed, and must not become a filename.

    Separate from ``StoreTargetNotConfigured`` (nobody said WHERE) because this
    is the opposite failure: somebody said where, and said it wrong. Conflating
    them would send a reader looking for a missing variable that is present.
    """


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

    THAT TOTALITY IS ALSO THIS MODULE'S RECURRING DEFECT, so it is no longer the
    only thing standing between a botched DSN and a new file. See
    :func:`is_attempted_dsn` and :func:`reject_attempted_dsn`, which give
    "I do not recognise this" somewhere to live. This function keeps its total
    contract because thirteen call sites branch on it; the guard is enforced at
    the door where a guess does damage.
    """
    return BACKEND_POSTGRES if is_postgres_url(target) else BACKEND_SQLITE


#: A path is anchored. Anything starting this way was typed as a location on
#: disk and stays SQLite no matter what punctuation appears later in it -- a
#: directory may legitimately be named "a://b", and a store under it must keep
#: opening.
_PATH_ANCHORS = ("/", "./", "../", "~")

#: ``:55432`` and ``127.0.0.1:55432`` -- a port, with or without a host, and no
#: path separator anywhere. Nobody names a database file this way; everybody who
#: writes it means a server. Two-to-five digits keeps a plausible filename like
#: ``notes:1`` out of it.
_BARE_HOST_PORT = re.compile(r"^[A-Za-z0-9._-]*:\d{2,5}$")

#: A mangled DSN that has since been made ABSOLUTE. The production string is
#: relative -- ``Path("postgresql://h/d")`` yields ``postgresql:/h/d``, which is
#: why the artifact found on 2026-08-12 sat under the process's working
#: directory -- but any caller that resolves before opening turns it into
#: ``/some/where/postgresql:/h/d``, and the path-anchor rule below would wave
#: that straight through. No directory is deliberately named ``postgresql:``;
#: one existing is this defect's own residue.
_MANGLED_SEGMENT = re.compile(r"/postgres(?:ql)?:/")


def is_attempted_dsn(target: object) -> bool:
    """True iff ``target`` is TRYING to name a server and failing.

    This is the answer :func:`backend_of` cannot give. A classifier that is
    total by construction cannot report non-recognition, so every input outside
    its allowlist becomes a confident wrong answer -- and here the wrong answer
    is "SQLite", which means A FILENAME, which means a new and empty cards
    database that answers queries.

    THREE TIMES NOW, EACH TIME A DIFFERENT SPELLING:

      2026-07-31  ``host=127.0.0.1 port=55432 dbname=scitex_cards``
                  created a SQLite database in the working directory named
                  literally that, reported backend "sqlite", accepted writes.
                  Fixed by enumerating :data:`_LIBPQ_KEYWORDS`.
      2026-08-02  ``postgresql://scitex_cards@127.0.0.1:.../...`` reached
                  ``Path()``, which collapses "//" to "/", and the inbox
                  migration built a real store under
                  ``postgresql:/scitex_cards@.../runtime/`` IN THE SOURCE REPO.
                  Found 2026-08-12, ten days later, untracked and NOT ignored --
                  one ``git add -A`` from being committed.
      2026-08-12  ``:55432`` resolved to backend "sqlite", exists False, ready
                  to create a file named ":55432".

    Enumerating a fourth accepted spelling would fix the third and wait for the
    fourth. So the predicate is written from the other side: not "which server
    spellings do I know" but "which inputs are obviously not filenames".

    A PATH WINS FIRST. Anchored targets return False before any other rule, so
    no existing deployment can be broken by this -- every store in service today
    is an absolute path.
    """
    if not isinstance(target, str):
        return False
    head = target.strip()
    if not head:
        return False
    if head.startswith(_PATH_ANCHORS):
        # An anchored target is a path -- UNLESS it carries the wreckage of a
        # DSN in the middle of it, which is what absolutising the relative
        # mangled form produces.
        return _MANGLED_SEGMENT.search(head) is not None
    if is_postgres_url(head):
        return False
    lowered = head.lower()
    if "://" in lowered:
        return True
    if lowered.startswith(POSTGRES_PREFIXES):
        return True
    return bool(_BARE_HOST_PORT.match(head))


def reject_attempted_dsn(target: object) -> None:
    """Raise if ``target`` is a malformed DSN, else return.

    Call this at any door that OPENS a store. Resolution itself stays total and
    silent -- a one-shot ``resolve`` that reports a target is not doing damage,
    and callers that merely REPORT should show the ambiguity rather than raise
    on it.
    """
    if not is_attempted_dsn(target):
        return
    raise UnrecognisedStoreTarget(
        f"the cards database target {target!r} names a server and is malformed, "
        "so it will NOT be opened as a file.\n"
        "Refusing is deliberate: treated as a path this creates a NEW and EMPTY "
        "cards database that answers every query, and a wrong board that works "
        "is far worse than one that will not start.\n"
        "Accepted forms:\n"
        "    postgresql://scitex_cards@127.0.0.1:55432/scitex_cards\n"
        "    host=127.0.0.1 port=55432 dbname=scitex_cards user=scitex_cards\n"
        "    /an/absolute/path/to/cards.db\n"
        "Check $SCITEX_CARDS_DB, and note a DSN that has been through Path() "
        "loses one slash: 'postgresql:/host/db' is this error, not a directory."
    )


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
