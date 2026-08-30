#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A ``task_comments`` row must be read BY NAME, not by position."""

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
