#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A ``task_comments`` row must be read BY NAME, because its TYPE varies by backend.

0.49.0 shipped ``_key(row[0], row[1], row[2], row[3])`` and crashed in production::

    File "scitex_cards/_mirror_rows.py", line 194, in _merge_unseen_comment_rows
        key = _key(row[0], row[1], row[2], row[3])
    KeyError: 0

``_db.py`` sets ``row_factory = sqlite3.Row``, and sqlite3.Row DOES support
positional indexing — so every SQLite test passed. The PostgreSQL path
(``_db_mirror.mirror_doc_incremental``) uses psycopg's DICT row factory, where
``row[0]`` is a lookup of the integer KEY ``0``. Production is PostgreSQL.

WHY A SMOKE TEST MISSED IT: ``_merge_unseen_comment_rows`` returns early when the
card has no comment rows, so the FIRST comment on a fresh card succeeded and every
comment on a card WITH HISTORY failed. Reported by scitex-dev as a bare ``"0"``
through MCP — which is ``str(KeyError(0))``.

THESE TESTS USE REAL ROW OBJECTS, NOT MOCKS: a plain ``dict`` is exactly the shape
psycopg's dict row factory yields, and the ``sqlite3.Row`` comes from a real
in-memory database. That is the whole point — the defect was invisible to a suite
that only ever saw one of the two row types.
"""

import ast
import sqlite3
from pathlib import Path

from scitex_cards._mirror_rows import _comment_fields

EXPECTED = ("scitex-cards", "2026-08-23T15:00:00Z", "note", "hello")


def _sqlite_row() -> sqlite3.Row:
    """A genuine sqlite3.Row from a real in-memory database."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE task_comments (author TEXT, ts TEXT, kind TEXT, text TEXT)"
    )
    conn.execute("INSERT INTO task_comments VALUES (?, ?, ?, ?)", EXPECTED)
    row = conn.execute("SELECT author, ts, kind, text FROM task_comments").fetchone()
    conn.close()
    return row


def test_a_mapping_row_is_read_correctly():
    """psycopg's dict row factory yields a mapping — the shape that crashed 0.49.0."""
    # Arrange
    row = {
        "author": EXPECTED[0],
        "ts": EXPECTED[1],
        "kind": EXPECTED[2],
        "text": EXPECTED[3],
    }
    # Act
    fields = _comment_fields(row)
    # Assert
    assert fields == EXPECTED


def test_a_sqlite_row_is_read_correctly():
    """The other real backend must keep working — the one 0.49.0 got right."""
    # Arrange
    row = _sqlite_row()
    # Act
    fields = _comment_fields(row)
    # Assert
    assert fields == EXPECTED


def test_no_positional_row_indexing_survives_in_the_mirror_module():
    """A source guard, the same shape ``test__comment_ids.py`` already uses here.

    Named access is the only form BOTH row types accept, so a reintroduced
    ``row[0]`` is a backend-specific crash that SQLite tests cannot catch.

    IT PARSES RATHER THAN GREPS, and the first draft of this test proved why: a
    substring scan flagged lines 112 and 114 of the module — both inside the
    docstring EXPLAINING the bug. A text detector cannot tell a defect from its
    own description, so the file that documents the problem best looks like the
    file that has it. ``ast`` sees code only.
    """
    # Arrange
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "scitex_cards"
        / "_mirror_rows.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    # Act
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "row"
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, int)
    ]
    # Assert
    assert offenders == []
