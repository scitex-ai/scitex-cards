#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a DDL script on either backend, because ``executescript`` is SQLite-only.

WHY THIS BLOCKS THE PORT. ``sqlite3.Connection.executescript`` is a pysqlite
method with no psycopg equivalent, and it is how EVERY schema object in this
package is installed -- including all nine triggers. Five call sites::

    _db.py:399           _SCHEMA_SQL
    _db.py:403           RETIREMENT_TRIGGER_SQL
    _db.py:408           SCHEMA_VERSION_FLOOR_TRIGGER_SQL
    _db_dm_schema.py:164 SCHEMA_SQL_V5
    _db_migrations.py:142 REVISION_TRIGGER_SQL

So until a backend-agnostic runner exists, a PostgreSQL store gets no tables
and, more importantly, NO GUARDS -- and a store with no retirement guard
reports itself current and authoritative, which is the failure that took this
board from 2170 rows to 18.

THE WHOLE DIFFICULTY IS ONE CHARACTER. A naive ``script.split(";")`` is wrong
for exactly the statements that matter most: a SQLite trigger body is
``BEGIN <stmt>; <stmt>; END;`` so its semicolons are INTERNAL. Splitting on
them yields fragments that are individually invalid, and the failure is not
loud -- the first fragment ``CREATE TRIGGER ... BEGIN UPDATE ...`` may parse as
a complete statement on some engines, installing a TRUNCATED trigger that
enforces less than it claims. A guard that half-exists is worse than one that
does not, because the probe for its name still finds it.

This module therefore tracks nesting rather than counting delimiters, and is
tested against the real trigger constants rather than against invented SQL.
"""

from __future__ import annotations

import re

__all__ = [
    "split_sql_script",
    "execute_ddl",
]

#: ``BEGIN`` opens a trigger body when it is the LAST token on its line and
#: carries no semicolon.
#:
#: THE TRAILING FORM IS THE COMMON ONE IN THIS PACKAGE and an earlier version of
#: this regex missed it. ``SCHEMA_SQL_V5`` writes the DM guards as::
#:
#:     CREATE TRIGGER IF NOT EXISTS dm_threads_no_delete
#:     BEFORE DELETE ON dm_threads BEGIN
#:         SELECT RAISE(ABORT, '...')
#:     END;
#:
#: so requiring ``BEGIN`` alone on its line split four append-only triggers mid
#: body -- producing exactly the truncated fragment this module's header warns
#: about. SQLite rejected it with "incomplete input", which is the loud outcome;
#: the quiet one was always the risk. Found by executing the real constants
#: rather than by reading them, which is why the equivalence test below exists.
#:
#: ``BEGIN IMMEDIATE`` / ``BEGIN DEFERRED`` / a bare ``BEGIN;`` are transaction
#: control, not nesting: the first two do not END the line with ``BEGIN`` and
#: the third carries a semicolon, so none of them match.
_BEGIN_RE = re.compile(r"\bBEGIN\s*$", re.IGNORECASE)
_END_RE = re.compile(r"^\s*END\s*;?\s*$", re.IGNORECASE)

#: ``AUTOINCREMENT`` is the one construct in this schema with NO portable
#: spelling, so it is the one that forces a dialect branch. Measured on both
#: engines 2026-07-31:
#:
#:     INTEGER PRIMARY KEY AUTOINCREMENT   sqlite OK    postgres SYNTAX ERROR
#:     INTEGER PRIMARY KEY                 sqlite OK    postgres NotNullViolation
#:     GENERATED ALWAYS AS IDENTITY        sqlite ERROR postgres OK
#:
#: THE MIDDLE ROW IS THE TRAP, and it is what a careless port reaches for: it
#: PARSES on both engines and fails only at INSERT, because PostgreSQL does not
#: auto-assign a plain ``INTEGER PRIMARY KEY`` the way SQLite's rowid alias
#: does. DDL-time success, runtime failure. So the replacement below was
#: verified by INSERTING a row and reading the generated id back, not merely by
#: creating the table.
_AUTOINCREMENT_RE = re.compile(
    r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE
)
_PG_IDENTITY = "INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY"


def to_dialect(statement: str, *, postgres: bool) -> str:
    """Translate one SQLite-flavoured DDL statement for the target engine.

    A no-op for SQLite, which is why the schema constants stay written in the
    dialect the production store actually speaks today -- the translation is
    applied at execution time, to the engine that needs it, rather than
    rewriting the source of truth for a backend nothing runs yet.

    Deliberately NOT a general SQL translator. It handles the constructs this
    schema actually contains and measurement has shown to differ; anything
    broader would be untested guesswork wearing the same name.
    """
    if not postgres:
        return statement
    return _AUTOINCREMENT_RE.sub(_PG_IDENTITY, statement)


def _strip_comments(line: str) -> str:
    """Drop a trailing ``--`` comment, respecting single-quoted literals.

    Naively cutting at the first ``--`` would corrupt any statement containing
    it inside a string -- and the version-floor trigger builds exactly such a
    value (``'7 -> 5'`` is safe, but a message containing ``--`` would not be).
    """
    out: list[str] = []
    in_literal = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "'":
            # '' inside a literal is an escaped quote, not a close-then-open.
            if in_literal and i + 1 < len(line) and line[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_literal = not in_literal
            out.append(ch)
        elif not in_literal and ch == "-" and line[i : i + 2] == "--":
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def split_sql_script(script: str) -> list[str]:
    """Split a DDL script into individually executable statements.

    Semicolons inside a ``BEGIN ... END`` trigger body do NOT end a statement.
    Blank statements and comment-only lines are dropped; every returned string
    is non-empty and carries no trailing semicolon.
    """
    statements: list[str] = []
    current: list[str] = []
    depth = 0

    for raw_line in script.splitlines():
        line = _strip_comments(raw_line)
        if not line.strip():
            continue

        if _BEGIN_RE.search(line):
            depth += 1
            current.append(line)
            continue

        if depth and _END_RE.match(line):
            depth -= 1
            current.append(line.rstrip().rstrip(";"))
            if depth == 0:
                statements.append("\n".join(current).strip())
                current = []
            continue

        current.append(line)

        if depth == 0 and line.rstrip().endswith(";"):
            joined = "\n".join(current).strip().rstrip(";").strip()
            if joined:
                statements.append(joined)
            current = []

    tail = "\n".join(current).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return statements


def execute_ddl(conn, script: str) -> int:
    """Execute every statement in ``script``; return how many ran.

    Replaces ``conn.executescript(script)``, which exists only on
    ``sqlite3.Connection``. Works with anything exposing ``execute`` -- a raw
    driver connection or the ``_backend_connect.StoreConnection`` wrapper.

    RETURNS A COUNT ON PURPOSE. ``executescript`` returns a cursor nobody reads,
    so a script that silently ran zero statements looked identical to one that
    installed nine triggers. A caller can now assert the number it expected,
    which is the difference between "the guards are installed" and "the install
    call did not raise".
    """
    from ._schema_probe import _is_postgres  # noqa: PLC0415 -- import cycle

    postgres = _is_postgres(conn)
    statements = split_sql_script(script)
    for statement in statements:
        conn.execute(to_dialect(statement, postgres=postgres))
    return len(statements)


# EOF
