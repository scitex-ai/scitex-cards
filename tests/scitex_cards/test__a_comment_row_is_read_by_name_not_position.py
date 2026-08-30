#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A ``task_comments`` row must be read BY NAME, not by position.

0.49.0 shipped ``_key(row[0], row[1], row[2], row[3])`` and crashed in production::

    File "scitex_cards/_mirror_rows.py", line 194, in _merge_unseen_comment_rows
        key = _key(row[0], row[1], row[2], row[3])
    KeyError: 0

THE DEFECT WAS A TYPE DIFFERENCE THE SUITE COULD NOT SEE. The retired engine's
row type accepted BOTH ``row[0]`` and ``row["author"]``, so every test against
it passed. PostgreSQL — which is production, and now the only engine — uses
psycopg's DICT row factory, where ``row[0]`` is a lookup of the integer KEY
``0``, and there is no such key.

WHY A SMOKE TEST MISSED IT: ``_merge_unseen_comment_rows`` returns early when the
card has no comment rows, so the FIRST comment on a fresh card succeeded and every
comment on a card WITH HISTORY failed. Reported by scitex-dev as a bare ``"0"``
through MCP — which is ``str(KeyError(0))``.

WHAT WAS DELETED HERE WITH THE SECOND ENGINE, and why that is not a loss of
coverage: the round-trip through the permissive row type is gone. It asserted
that a type nothing constructs any more still worked. What it BOUGHT — the
knowledge that positional access is a backend-specific crash — is kept, and
kept in the form that can still fail: the AST guard below, which refuses
``row[<int>]`` in the module regardless of what any row object happens to
tolerate. That guard is the half that would have caught 0.49.0.

NO MOCKS: a plain ``dict`` is exactly the shape psycopg's dict row factory
yields, which is why it is the fixture rather than a stand-in for one.
"""

import ast
from pathlib import Path

from scitex_cards._mirror_rows import _comment_fields

EXPECTED = ("scitex-cards", "2026-08-23T15:00:00Z", "note", "hello")


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


def test_no_positional_row_indexing_survives_in_the_mirror_module():
    """A source guard, the same shape ``test__comment_ids.py`` already uses here.

    This is now the ONLY detector for the defect, and it is the stronger one:
    it fails on the SOURCE rather than waiting for a row object permissive
    enough to hide it. That permissive row type is what let 0.49.0 ship.

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
