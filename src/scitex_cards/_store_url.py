#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decide whether a store target names the store, and translate SQL paramstyle.

This is the pure half of the store layer: the part that can be written and
tested without a server. It exists because 140 ``execute()`` call sites across
10 modules are written in the ``?`` paramstyle and PostgreSQL uses ``%s``.
Translating at each call site would be 140 opportunities to get it wrong;
translating in one place is one.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not connect, and it does not claim the reader works. Two fatal defects
in scitex-db's migration tool on 2026-07-30 were invisible to 196 passing tests
and appeared only against a real driver -- a probe that aborted the transaction
so the first CREATE TABLE died, and indexes of excluded tables counted as
carried. Both were pure-logic-looking bugs that pure logic could not catch. So
nothing here should be read as evidence that the connection layer works.

THERE IS EXACTLY ONE ACCEPTED SHAPE
-----------------------------------
A store target is a PostgreSQL DSN -- either the URL form (``postgresql://``,
``postgres://``) or the libpq keyword/value conninfo form. Nothing else is a
store. A filesystem path is NOT a store target and is refused by
:func:`reject_non_postgres_target`, which is the restatement of a protection
this package earned the hard way: on 2026-07-31, 2026-08-02 and 2026-08-12 a
target that was not a DSN became a real, empty, query-answering cards database
on disk, and a wrong board that works is far worse than one that will not
start.

Note this module answers "is this the store's shape", never "which store". A
DSN is a LOCATION and locations fail both ways -- the same database reached as
``localhost`` and ``127.0.0.1`` is string-unequal, and a restored backup at the
same address is a different store wearing the right name. Store IDENTITY lives
inside the store (``schema_meta.store_uuid``) and is checked there. This module
must never be mistaken for that check.
"""

from __future__ import annotations

import re

__all__ = [
    "BACKEND_POSTGRES",
    "BACKEND_UNSUPPORTED",
    "POSTGRES_SCHEMES",
    "UnrecognisedStoreTarget",
    "backend_of",
    "is_attempted_dsn",
    "is_postgres_conninfo",
    "is_postgres_url",
    "is_unexpanded_variable",
    "reject_attempted_dsn",
    "reject_non_postgres_target",
    "reject_unexpanded_variable",
    "to_paramstyle",
]

BACKEND_POSTGRES = "postgresql"

#: What :func:`backend_of` answers for anything that is not a PostgreSQL DSN.
#: NOT the name of a second engine -- there is no second engine. It is the
#: symbol for "this target names no store I can open", so a caller that
#: branches on the backend gets a value it must handle rather than a plausible
#: alternative it can quietly accept.
BACKEND_UNSUPPORTED = "unsupported"

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
#: not recognised as a store target at all and was opened AS A FILENAME.
#:
#: That is not theoretical: on 2026-07-31, testing this very module, I passed
#: ``host=127.0.0.1 port=5432 dbname=scitex_cards user=scitex_cards`` and a
#: database file was created in the working directory literally named that,
#: which accepted writes and answered queries. A wrong store that works is the
#: failure this package keeps meeting: nothing raises, and the board looks
#: healthy and empty.
#:
#: Detection is by KEYWORD rather than by "contains =", because a target may
#: legitimately contain "=" in a password or an ``options`` value.
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

    Requires the FIRST token to be ``<known-keyword>=``: a string such as
    ``/srv/data/a=b/cards.db`` contains an ``=`` but does not begin with a libpq
    keyword, so it is not a conninfo.
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
    that is not a PostgreSQL DSN is :data:`BACKEND_UNSUPPORTED`.

    THAT TOTALITY WAS THIS MODULE'S RECURRING DEFECT while the "else" branch
    named a real engine: an unrecognised target became a confident wrong answer,
    and the wrong answer meant A FILENAME, which meant a new and empty cards
    database that answers queries. Naming the else-branch UNSUPPORTED is what
    makes non-recognition representable. This function keeps its total contract
    because thirteen call sites branch on it; the refusal is enforced at the
    door where a guess does damage -- see :func:`reject_non_postgres_target`.
    """
    return BACKEND_POSTGRES if is_postgres_url(target) else BACKEND_UNSUPPORTED


#: A path is anchored. Anything starting this way was typed as a location on
#: disk, so it is not a MALFORMED DSN however it is punctuated later -- a
#: directory may legitimately be named "a://b". It is still not a store target;
#: :func:`reject_non_postgres_target` is what refuses it, with the diagnostic
#: that fits.
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

    A DSN that is merely MALFORMED gets a diagnostic of its own, separate from
    the blanket refusal in :func:`reject_non_postgres_target`, because "you
    meant a server and mistyped it" and "this is not a store target" send a
    reader to different places. Historically this predicate was the whole guard:
    a classifier that is total by construction cannot report non-recognition, so
    every input outside its allowlist became a confident wrong answer -- and the
    wrong answer meant A FILENAME, which meant a new and empty cards database
    that answers queries.

    THREE TIMES NOW, EACH TIME A DIFFERENT SPELLING:

      2026-07-31  ``host=127.0.0.1 port=55432 dbname=scitex_cards``
                  created a database file in the working directory named
                  literally that, which accepted writes.
                  Fixed by enumerating :data:`_LIBPQ_KEYWORDS`.
      2026-08-02  ``postgresql://scitex_cards@127.0.0.1:.../...`` reached
                  ``Path()``, which collapses "//" to "/", and the inbox
                  migration built a real store under
                  ``postgresql:/scitex_cards@.../runtime/`` IN THE SOURCE REPO.
                  Found 2026-08-12, ten days later, untracked and NOT ignored --
                  one ``git add -A`` from being committed.
      2026-08-12  ``:55432`` was classified as a filename, exists False, ready
                  to create a file named ":55432".

    Enumerating a fourth accepted spelling would fix the third and wait for the
    fourth. So the predicate is written from the other side: not "which server
    spellings do I know" but "which inputs are obviously not filenames".

    A PATH WINS FIRST. Anchored targets return False before any other rule: an
    anchored target is not a mistyped server, so it gets the other refusal
    rather than this one.
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
        "Check $SCITEX_CARDS_DB, and note a DSN that has been through Path() "
        "loses one slash: 'postgresql:/host/db' is this error, not a directory."
    )


def reject_non_postgres_target(target: object) -> None:
    """Raise unless ``target`` names the PostgreSQL store. THE OPENING DOOR.

    Call this at every door that OPENS a store, ahead of the driver. It is the
    single refusal the two narrower ones fold into: it runs
    :func:`reject_unexpanded_variable` and :func:`reject_attempted_dsn` first so
    a caller that mistyped a server still gets the diagnostic that names the
    mistake, and only then refuses everything that is left.

    WHY IT REFUSES A PERFECTLY GOOD FILE PATH. A path used to be the accepted
    spelling, so the surviving hazard is not a typo -- it is a stale but
    syntactically fine configuration pointing at a filename. Opened, that
    filename becomes a new, empty, query-answering cards database; measured on
    this package three times (2026-07-31, 2026-08-02, 2026-08-12), each time
    with every health probe green. Refusing to open a target that is not the
    store is the only version of this guard that cannot be threaded.
    """
    reject_unexpanded_variable(target)
    reject_attempted_dsn(target)
    if is_postgres_url(target):
        return
    raise UnrecognisedStoreTarget(
        f"the cards store target {target!r} does not name the store.\n"
        "The store is a PostgreSQL database and the target must be a DSN:\n"
        "    postgresql://scitex_cards@127.0.0.1:55432/scitex_cards\n"
        "    host=127.0.0.1 port=55432 dbname=scitex_cards user=scitex_cards\n"
        "Refusing is deliberate: opened as a file, this target MANUFACTURES a "
        "new and empty cards database that answers every query, and a wrong "
        "board that works is far worse than one that will not start.\n"
        "Fix the SOURCE of the value -- $SCITEX_CARDS_DB or the config that "
        "sets it. Repointing a live store at a fresh target is how the board "
        "was destroyed 2026-07-19."
    )


#: Shell constructs a shell would already have consumed. Their SURVIVAL into a
#: store target is the whole signal: the value reached us through a reader that
#: does not expand -- a JSON or YAML config, a single-quoted assignment, a spec
#: template rendered without substitution -- so what we hold is the RECIPE for a
#: target, not a target.
_UNEXPANDED_VARIABLE = re.compile(r"\$\{|\$\(")


def is_unexpanded_variable(target: object) -> bool:
    """True iff ``target`` still carries an unexpanded shell expansion.

    THE THIRD SHAPE THAT IS NOT A FILENAME, and the one that threaded between
    the two guards already here. Measured 2026-08-18: with
    ``SCITEX_CARDS_DB='${SCITEX_CARDS_DB}'`` -- the literal, brace and all --
    :func:`~scitex_cards._store_target.resolve_store_target` returned that
    string as a legitimate store target, because

      * it is NON-EMPTY, so ``refuse_zero_config_default`` never fires; and
      * it is NOT DSN-SHAPED -- no path anchor, no ``://``, no libpq keyword,
        not bare ``host:port`` -- so :func:`is_attempted_dsn` returns False and
        :func:`reject_attempted_dsn` never inspects it.

    Two correct checks with a gap between them. Both were written by asking
    "does this look like a server?"; neither asks "did this value ever get
    resolved at all?".

    WHAT IT COST. Eight handyman agents on scitex-compute-03 held exactly this
    literal in their environment, so every cards client resolved to one database
    file named ``${SCITEX_CARDS_DB}`` in the project directory. Four direct
    messages addressed to the operator were written into it and delivered to
    nobody. Two of those agents diagnosed the defect themselves, at 00:20 and
    00:41, and declined to redirect the store -- citing the 2026-07-19 board
    destruction by name. Their escalation went into the store it was about.

    BRACED AND COMMAND FORMS ONLY -- ``$FOO`` IS DELIBERATELY NOT MATCHED, and
    that gap is chosen rather than overlooked. ``$`` is a legal character in a
    POSIX filename, so a bare-``$`` rule could refuse a store that works today,
    and this module's standing promise is that no deployment in service breaks
    (see :func:`is_attempted_dsn`: "a path wins first"). ``${`` and ``$(``
    cannot survive any shell that ran, which is what makes them decidable.
    A bare ``$FOO`` target is still a defect; it is simply one this predicate
    reports as False rather than guess about.
    """
    if not isinstance(target, str):
        return False
    return _UNEXPANDED_VARIABLE.search(target) is not None


def reject_unexpanded_variable(target: object) -> None:
    """Raise if ``target`` is an unexpanded expansion, else return.

    Call this at any door that OPENS a store, beside
    :func:`reject_attempted_dsn` -- same placement, same reason. Resolution
    stays total and silent so a caller that merely REPORTS a target can show
    the ambiguity instead of raising on it.
    """
    if not is_unexpanded_variable(target):
        return
    raise UnrecognisedStoreTarget(
        f"the cards database target {target!r} still contains an UNEXPANDED "
        "shell variable, so it names no store and will NOT be opened.\n"
        "Something read this value without expanding it -- a JSON/YAML config, "
        "a single-quoted assignment, or a spec template rendered literally -- "
        "so it is the recipe for a target, not a target.\n"
        "Refusing is deliberate: treated as a path it creates a NEW and EMPTY "
        "cards database, named after the variable, that answers every query. "
        "On 2026-08-18 that silently collected four undelivered operator "
        "messages on scitex-compute-03.\n"
        "Fix the SOURCE of the value, never the symptom: repointing a live "
        "store at a fresh target is how the board was destroyed 2026-07-19.\n"
        "Check $SCITEX_CARDS_DB and the config that sets it; the intended "
        "value looks like\n"
        "    postgresql://scitex_cards@127.0.0.1:55432/scitex_cards"
    )


def to_paramstyle(sql: str, backend: str) -> str:
    """Rewrite ``?`` placeholders for ``backend``.

    The SQL in this package is written in the ``?`` paramstyle; a PostgreSQL
    connection rewrites it on the way through. Any other backend is returned
    unchanged, which is unreachable in a deployment (there is no other backend)
    and kept so this function is total rather than partial.

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
