#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every declared foreign key must be DEFERRABLE INITIALLY DEFERRED.

WHY THE DECLARATION, NOT JUST THE LIVE STORE. `NOT DEFERRABLE` was never a
decision here — it is what an inline `REFERENCES` gives you when nobody thinks
about ordering, and it was written before directed replay existed as a
requirement. Under replay a foreign key is an ORDERING constraint: a child row
arriving before its parent must not fail the transaction, it must be checked at
COMMIT. That applies to FRESH stores exactly as much as to migrated ones, so
there is ONE target shape and it is deferrable.

WHAT THIS FILE PREVENTS, and it is not a style violation — it is a LOOP.
scitex-db runs a reconciler that ALTERs live constraints toward deferrable.
A rung that restores the shape this module DECLARES would put them back:

    their ALTER      -> deferrable
    declared shape   -> non-deferrable, so a restore rung reverts it
    their next run   -> observes PRESENT_NOT_DEFERRABLE, alters it again
    ... forever

Neither log shows a fault. Theirs reports "altered to deferrable"; the other
reports "restored to declared shape". The oscillation is visible only to
someone reading both, which is nobody by default. Two mechanisms enforcing
different target shapes is not a race to be timed better — it is a
disagreement about what the right answer IS, and no schedule resolves it.
Agreeing in the DECLARATION is what ends it.

The test enumerates rather than spot-checks, because the failure that matters
is the FIFTH foreign key somebody adds next month without reading any of this.
"""

import re
import sqlite3

from scitex_cards._db_schema_sql import SCHEMA_SQL

#: Matches a REFERENCES clause and captures everything up to the line's comma or
#: closing paren, so the assertion reads the WHOLE clause rather than the word.
_REFERENCES = re.compile(
    r"REFERENCES\s+\w+\s*\([^)]*\)(?P<tail>(?:[^,)]|\([^)]*\))*)", re.IGNORECASE
)


def _clauses(sql):
    """(matched text, is_deferrable) for every REFERENCES clause in `sql`."""
    out = []
    for m in _REFERENCES.finditer(sql):
        tail = m.group("tail")
        out.append((m.group(0).split("\n")[0].strip(), "DEFERRABLE" in tail.upper()))
    return out


def test_the_scanner_finds_the_foreign_keys_that_are_known_to_exist():
    """POSITIVE CONTROL. If the regex matched nothing, the real test below would
    pass vacuously — every member of an empty set is deferrable. The schema has
    four inline REFERENCES clauses (task_comments, task_edges, task_roles,
    user_names), so anything less than four means the scanner is broken."""
    # Arrange
    expected_minimum = 4
    # Act
    found = _clauses(SCHEMA_SQL)
    # Assert
    assert len(found) >= expected_minimum


def test_every_declared_foreign_key_is_deferrable():
    """THE BARRIER. A new FK declared without DEFERRABLE fails here, naming the
    clause — before it can reach a store and start the loop described above."""
    # Arrange
    clauses = _clauses(SCHEMA_SQL)
    # Act
    non_deferrable = [text for text, deferrable in clauses if not deferrable]
    # Assert
    assert non_deferrable == []


def test_sqlite_accepts_the_declared_schema():
    """The store is SQLite-canonical AND PostgreSQL. `DEFERRABLE INITIALLY
    DEFERRED` is valid in both, but a syntax error would only surface at store
    creation — that is, in production rather than here."""
    # Arrange
    conn = sqlite3.connect(":memory:")
    # Act
    conn.executescript(SCHEMA_SQL)
    # Assert
    assert conn.execute("SELECT 1").fetchone() == (1,)


def test_the_created_sqlite_store_records_the_deferrable_clause():
    """Declaring it is not the same as the engine STORING it. SQLite keeps the
    original CREATE TABLE text in sqlite_master, which is what a shape probe
    reads back — so this asserts the constraint survives creation rather than
    being parsed and dropped."""
    # Arrange
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    # Act
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'task_comments'"
    ).fetchone()
    # Assert
    assert "DEFERRABLE INITIALLY DEFERRED" in row[0]


def test_task_edges_dst_is_still_unconstrained():
    """PRECISION GUARD. `dst_task_id` is deliberately FK-free — a forward
    reference to a card that does not exist yet is supported, and
    `_diagram/_mermaid.py` skips an unknown dst with a WARN rather than
    failing. Making every column deferrable must not turn into making every
    column referential."""
    # Arrange
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    # Act
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'task_edges'"
    ).fetchone()
    # Assert
    assert "dst_task_id TEXT NOT NULL," in row[0]


# EOF
