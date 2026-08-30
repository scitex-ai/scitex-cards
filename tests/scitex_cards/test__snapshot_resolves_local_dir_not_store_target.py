#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The off-site snapshot must survive a PostgreSQL store target.

INCIDENT, 2026-08-02: the hourly off-site snapshot died for ~31 hours the moment
``$SCITEX_CARDS_DB`` was pointed at PostgreSQL. Two independent defects, both
found only by running the real command against a real server:

  1. ``db_snapshot_cmd`` computed its OUTPUT DIRECTORY as
     ``resolve_db_path(db_path).parent / "snapshots"``. With a DSN,
     ``resolve_db_path`` raises ``StoreTargetIsNotAPath`` -- correctly, that
     guard exists so a DSN is never coerced into a mangled file path. The guard
     was right and the caller was wrong: STORE IDENTITY (may be a DSN) and
     LOCAL STATE DIR (always a real directory) are independent axes, and a
     backup needs the second.
  2. ``_live_task_fingerprint`` read its row POSITIONALLY (``row[0]``).
     the retired engine's row type accepted that; the PostgreSQL wrapper yields a dict-like row
     where ``row[0]`` raises ``KeyError: 0``. This surfaced only after (1) was
     fixed -- one defect was hiding the next.

WHY NOTHING CAUGHT IT: every existing snapshot test used a local file, where
both spellings work. The failure needs a DSN to exist at all, and the traceback
went to a log file (``StandardOutput=append:``) rather than journald, so
``systemctl status`` showed a bare "exit-code 1" with no reason.

So these tests assert on the DSN path specifically. They do NOT need a live
server: both defects are in path/row handling that fails before any query would
run, which is precisely why a DSN string alone reproduces them.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from scitex_cards._paths import resolve_tasks_path
from scitex_cards._store_target import resolve_store_target

_DSN = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"


class TestTheLocalDirIsResolvableFromADsn:
    """The axis the snapshot needs: a real directory, even for a DSN."""

    def test_a_dsn_yields_a_real_local_parent(self):
        # Arrange
        target = _DSN

        # Act
        parent = resolve_tasks_path(target).parent

        # Assert -- an absolute local directory, not a mangled DSN path
        assert parent.is_absolute()

    def test_the_local_parent_is_not_derived_from_the_dsn_text(self):
        """Guards the coercion the store-target guard exists to prevent.

        `Path("postgresql://h/db")` silently collapses to a relative
        `postgresql:/h/db`. If that ever leaks back in, the snapshot writes
        into a junk directory and reports success.
        """
        # Arrange
        target = _DSN

        # Act
        parent = str(resolve_tasks_path(target).parent)

        # Assert
        assert "postgresql" not in parent

    def test_the_store_identity_stays_the_dsn(self):
        """The other axis must NOT be rewritten into a local path."""
        # Arrange
        target = _DSN

        # Act
        identity = resolve_store_target(target)

        # Assert
        assert identity == _DSN


def _called_names(func) -> set[str]:
    """Every function name CALLED in ``func``, read from the AST.

    Deliberately not a substring scan. My first draft of these tests asserted
    ``"resolve_db_path(db_path).parent" not in source`` and went red against the
    FIXED code -- because the comment explaining the fix quotes the old
    expression verbatim. A source-text check cannot tell an offending call from
    prose describing one, so it fails exactly when the code is well documented.
    The AST sees calls and cannot see comments.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _positional_row_indexes(func) -> list[int]:
    """Integer subscripts applied to a name called ``row``.

    ``row[0]`` was valid on the retired engine's row type and raises ``KeyError: 0`` on the
    PostgreSQL wrapper. Same AST-not-text reasoning as above: the docstring
    explaining this hazard necessarily contains the offending spelling.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    found: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        if not (isinstance(node.value, ast.Name) and node.value.id == "row"):
            continue
        index = node.slice
        if isinstance(index, ast.Constant) and isinstance(index.value, int):
            found.append(index.value)
    return found


class TestTheSnapshotCommandUsesTheLocalAxis:
    """Which resolver the command CALLS -- that is what regressed."""

    def test_snapshot_does_not_call_the_store_target_resolver_for_its_dir(self):
        # Arrange
        from scitex_cards._cli._db import db_snapshot_cmd

        # Act
        called = _called_names(db_snapshot_cmd.callback)

        # Assert
        assert "resolve_db_path" not in called

    def test_snapshot_calls_the_local_axis_resolver(self):
        # Arrange
        from scitex_cards._cli._db import db_snapshot_cmd

        # Act
        called = _called_names(db_snapshot_cmd.callback)

        # Assert
        assert "resolve_tasks_path" in called


class TestTheLiveFingerprintReadsRowsByName:
    """`row[0]` worked on the retired engine and raises KeyError on the PostgreSQL wrapper."""

    def test_the_fingerprint_does_not_index_its_row_positionally(self):
        # Arrange
        from scitex_cards._cli._db import _live_task_fingerprint

        # Act
        positional = _positional_row_indexes(_live_task_fingerprint)

        # Assert
        assert positional == []

    def test_the_fingerprint_aliases_its_columns_so_names_exist(self):
        """Reading by name requires the SELECT to alias them.

        Checked on the SQL string literal, which is code rather than prose --
        the aliases must be present for `row["n"]` to resolve on either backend.
        """
        # Arrange
        from scitex_cards._cli._db import _live_task_fingerprint

        tree = ast.parse(textwrap.dedent(inspect.getsource(_live_task_fingerprint)))
        literals = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]

        # Act
        aliased = [s for s in literals if "AS n" in s and "AS newest" in s]

        # Assert
        assert aliased != []


# EOF
