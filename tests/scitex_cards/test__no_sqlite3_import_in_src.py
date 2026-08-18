#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A module that never imports ``sqlite3`` cannot manufacture a database.

WHY THIS IS A TEST AND NOT A RULE IN A DOCUMENT
------------------------------------------------
The operator's ruling on 2026-08-17 was that SQLite is abolished from this
package: removed from source, migrated to PostgreSQL, and -- the part a written
rule cannot deliver -- *confirmed unable to be recreated*. The strongest
available form of that confirmation is structural rather than behavioural. A
guard that inspects a target string can be bypassed by the next call site that
forgets to call it; a module that does not have the driver in its namespace has
no expressible way to open, create, or migrate a SQLite file at all.

This is level 1 of the three places a guarantee can live, the same ladder
``_backend_connect`` names in its own header:

  1. make the bad state impossible to construct   <- this test
  2. make the rule unconditional
  3. remember to apply the rule correctly

THE ALLOWLIST IS A RATCHET, NOT AN EXEMPTION
--------------------------------------------
:data:`KNOWN_SQLITE_IMPORTERS` names the modules that still import the driver as
of this test landing. It exists so the barrier can be installed BEFORE the last
removal is finished -- a guard deferred until the work is complete is a guard
that never lands. Two properties make it a ratchet rather than a loophole:

  * A module NOT on the list that starts importing ``sqlite3`` fails
    immediately. New offenders are impossible.
  * A module ON the list that STOPS importing ``sqlite3`` also fails, until it
    is deleted from the list. The list cannot silently retain stale entries and
    quietly re-authorise a module that had already been cleaned.

So the only edit this file accepts is a DELETION from the list. Every entry
removed is one module that can no longer create a cards database, and the count
is the migration's actual progress metric.

WHY AST AND NOT ``grep``
------------------------
``grep`` for ``import sqlite3`` matches the word inside docstrings, comments and
string literals -- and this package is FULL of those, deliberately: most of its
SQLite vocabulary is the abolition guard, prose whose whole job is to name
SQLite in order to refuse it. A textual check would report the refusal machinery
as the offence it exists to prevent. Parsing answers the question actually being
asked: is the driver bound into this module's namespace?
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "scitex_cards"

#: Modules that still bind the ``sqlite3`` driver, with WHY each one still does.
#: Delete an entry when its module stops importing the driver -- the test fails
#: if you forget, so the list cannot rot.
KNOWN_SQLITE_IMPORTERS: dict[str, str] = {
    # -- the two create-capable doors. Removing these is the behavioural change
    #    the migration still owes; everything else here is downstream of them.
    "_backend_connect.py": "the seam's SQLite branch: sqlite3.connect(uri, uri=read_only)",
    "_db.py": "the main store door: mkdir(parents=True) then sqlite3.connect(str(p))",
    # -- a THIRD create-capable door, and a separate database entirely: the
    #    derived search index at ~/.scitex/card/.tasks.index.sqlite. Not the
    #    cards store, so it needs its own migration decision, not this one's.
    "_index.py": "derived FTS index; creates its own SQLite file",
    # -- the live SQLite inbox backend, still selected by _inbox._use_sqlite().
    "_inbox_sqlite.py": "SQLite inbox backend (annotation use only, but the module IS the backend)",
    "_inbox_sqlite_schema.py": "SQLite inbox DDL + open_connection",
    "_inbox_receipt.py": "reads/writes the SQLite inbox receipt rows",
    "_channel_rail.py": "read-only probe of the SQLite rail (mode=ro)",
    "_db_dm_schema.py": "catches sqlite3.OperationalError from the DM schema probe",
    # -- legacy readers. All open mode=ro, so none of them can CREATE a store;
    #    they exist to read the retired SQLite predecessor or migrate off it.
    "_dual_write.py": "mode=ro identity probe of the legacy store",
    "_health_store.py": "mode=ro health probe",
    "_health_store_identity.py": "mode=ro store_uuid probe",
    "_health_stranded_backlog.py": "mode=ro stranded-backlog probe",
    "_inbox_migrate_postgres.py": "mode=ro reader that migrates the inbox INTO PostgreSQL",
    "_store_canonical_read.py": "mode=ro retirement check on the legacy store",
    "_store_uuid.py": "mode=ro store_uuid reader",
}


def _imports_sqlite3(source: str) -> bool:
    """True iff parsing ``source`` shows the driver bound into its namespace.

    Counts BOTH spellings that create the binding -- ``import sqlite3`` and
    ``from sqlite3 import ...`` (including submodules such as ``sqlite3.dbapi2``,
    which is the same driver under a second name). Deliberately blind to
    docstrings, comments and string literals, which is the entire reason this is
    a parse and not a text search.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(a.name == "sqlite3" or a.name.startswith("sqlite3.") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "sqlite3" or module.startswith("sqlite3."):
                return True
    return False


def _offenders() -> dict[str, str]:
    """Every module under ``src/scitex_cards`` that binds the driver.

    Keyed by path relative to the package root so a module inside a subpackage
    (``_dm/read.py``) is distinguishable from one at the top level.
    """
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "sqlite3" not in source:  # cheap pre-filter; the parse below decides
            continue
        if _imports_sqlite3(source):
            found[str(path.relative_to(SRC))] = str(path)
    return found


# --------------------------------------------------------------------------- #
# THE POSITIVE CONTROL. A guard that has never failed is not a guard -- it is a
# function that returns True. These two run the REAL detector over deliberately
# constructed samples, so a refactor that neuters `_imports_sqlite3` (an early
# return, a swallowed SyntaxError, a walk that stops at module level) turns this
# file red instead of leaving a silent no-op guarding the package.
# --------------------------------------------------------------------------- #

OFFENDING_SAMPLES = pytest.mark.parametrize(
    "source",
    [
        pytest.param("import sqlite3\n", id="plain-import"),
        pytest.param("import sqlite3 as db\n", id="aliased"),
        pytest.param("from sqlite3 import connect\n", id="from-import"),
        pytest.param("from sqlite3.dbapi2 import connect\n", id="submodule"),
        pytest.param("import json, sqlite3\n", id="comma-separated"),
        pytest.param("def f():\n    import sqlite3\n    return sqlite3\n", id="function-local"),
        pytest.param("if True:\n    import sqlite3\n", id="conditional"),
        pytest.param("try:\n    import sqlite3\nexcept ImportError:\n    pass\n", id="guarded"),
    ],
)

INNOCENT_SAMPLES = pytest.mark.parametrize(
    "source",
    [
        pytest.param('"""We refuse sqlite3 here. import sqlite3 is banned."""\n', id="docstring"),
        pytest.param("# import sqlite3  <- never do this\n", id="comment"),
        pytest.param('BANNED = "import sqlite3"\n', id="string-literal"),
        pytest.param("from ._store_url import BACKEND_SQLITE\n", id="guard-import"),
        pytest.param("def f(conn: 'sqlite3.Connection') -> None: ...\n", id="string-annotation"),
    ],
)


@OFFENDING_SAMPLES
def test_the_detector_catches_a_deliberately_offending_sample(source: str) -> None:
    """RED-PROOF: the detector fires on every spelling that binds the driver.

    Function-local and conditional imports are included on purpose -- they are
    exactly how a driver sneaks back in after a top-level sweep, and two of the
    modules still on the allowlist (``_dual_write``, ``_health_store``) import
    the driver inside a function body today.
    """
    # Arrange: `source` is a deliberately offending sample from the parametrize.
    # Act
    detected = _imports_sqlite3(source)
    # Assert
    assert detected is True


@INNOCENT_SAMPLES
def test_the_detector_ignores_sqlite_vocabulary_that_is_not_an_import(source: str) -> None:
    """The other half of the control: it must not fire on the abolition guard.

    This is the failure mode that matters most here. Most SQLite vocabulary in
    this package is prose that NAMES SQLite in order to REFUSE it, and a
    detector that flagged it would push the next reader to delete the very
    machinery preventing recreation.
    """
    # Arrange: `source` names SQLite without importing it.
    # Act
    detected = _imports_sqlite3(source)
    # Assert
    assert detected is False


# --------------------------------------------------------------------------- #
# THE BARRIER ITSELF.
# --------------------------------------------------------------------------- #


def test_no_module_outside_the_allowlist_imports_the_sqlite3_driver() -> None:
    """No NEW module may bind the driver. This is the half that cannot regress."""
    # Arrange
    allowed = set(KNOWN_SQLITE_IMPORTERS)
    # Act
    unexpected = sorted(set(_offenders()) - allowed)
    # Assert
    assert not unexpected, (
        "these modules import the sqlite3 driver and are NOT on the allowlist:\n  "
        + "\n  ".join(unexpected)
        + "\n\nSQLite is abolished in this package (operator ruling 2026-08-17). A "
        "module holding the driver can create a cards database, which is the "
        "failure this barrier exists to make unexpressible. Use "
        "`_backend_connect.connect()` with a resolved store target instead. If "
        "you are detecting or REFUSING a SQLite target, you do not need the "
        "driver -- `_store_url` does that on strings alone."
    )


def test_the_allowlist_has_no_stale_entries() -> None:
    """A cleaned module must LEAVE the list, or the list stops being a ratchet.

    Without this, an entry outlives the removal it described and silently
    re-authorises the module to import the driver again -- the allowlist would
    decay from a shrinking debt into a permanent exemption.
    """
    # Arrange
    listed = set(KNOWN_SQLITE_IMPORTERS)
    # Act
    stale = sorted(listed - set(_offenders()))
    # Assert
    assert not stale, (
        "these modules no longer import sqlite3 but are still allowlisted:\n  "
        + "\n  ".join(stale)
        + "\n\nDelete them from KNOWN_SQLITE_IMPORTERS. Every deletion is one "
        "module that can no longer create a cards database."
    )


def test_the_abolition_guard_itself_needs_no_driver() -> None:
    """``_store_url`` REFUSES SQLite targets and must do it without the driver.

    Pinned because it is the load-bearing asymmetry of this whole migration: a
    guard recognises a SQLite-shaped target from a STRING. If this module ever
    grew an ``import sqlite3``, the package's refusal machinery would itself
    become capable of the thing it refuses.
    """
    # Arrange
    source = (SRC / "_store_url.py").read_text(encoding="utf-8")
    # Act
    detected = _imports_sqlite3(source)
    # Assert
    assert detected is False


def test_the_abolition_guard_is_not_on_the_allowlist() -> None:
    """The refusal machinery must never be granted the driver by exemption.

    Separate from the test above so that "the guard imported sqlite3" and
    "somebody allowlisted the guard" fail as two distinct, individually named
    lines in CI rather than one masking the other.
    """
    # Arrange
    guard = "_store_url.py"
    # Act
    allowlisted = guard in KNOWN_SQLITE_IMPORTERS
    # Assert
    assert allowlisted is False


# EOF
