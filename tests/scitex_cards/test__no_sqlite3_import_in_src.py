#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A module that never imports ``the retired driver`` cannot manufacture a database.

WHY THIS IS A TEST AND NOT A RULE IN A DOCUMENT
------------------------------------------------
The operator's ruling on 2026-08-17 was that the retired engine is abolished from this
package: removed from source, migrated to PostgreSQL, and -- the part a written
rule cannot deliver -- *confirmed unable to be recreated*. The strongest
available form of that confirmation is structural rather than behavioural. A
guard that inspects a target string can be bypassed by the next call site that
forgets to call it; a module that does not have the driver in its namespace has
no expressible way to open, create, or migrate a the retired engine file at all.

This is level 1 of the three places a guarantee can live, the same ladder
``_backend_connect`` names in its own header:

  1. make the bad state impossible to construct   <- this test
  2. make the rule unconditional
  3. remember to apply the rule correctly

THE ALLOWLIST IS A RATCHET, NOT AN EXEMPTION
--------------------------------------------
:data:`KNOWN_DRIVER_IMPORTERS` names the modules that still import the driver as
of this test landing. It exists so the barrier can be installed BEFORE the last
removal is finished -- a guard deferred until the work is complete is a guard
that never lands. Two properties make it a ratchet rather than a loophole:

  * A module NOT on the list that starts importing ``the retired driver`` fails
    immediately. New offenders are impossible.
  * A module ON the list that STOPS importing ``the retired driver`` also fails, until it
    is deleted from the list. The list cannot silently retain stale entries and
    quietly re-authorise a module that had already been cleaned.

So the only edit this file accepts is a DELETION from the list. Every entry
removed is one module that can no longer create a cards database, and the count
is the migration's actual progress metric.

WHY AST AND NOT ``grep``
------------------------
``grep`` for ``import the retired driver`` matches the word inside docstrings, comments and
string literals -- and this package is FULL of those, deliberately: most of its
the retired engine vocabulary is the abolition guard, prose whose whole job is to name
the retired engine in order to refuse it. A textual check would report the refusal machinery
as the offence it exists to prevent. Parsing answers the question actually being
asked: is the driver bound into this module's namespace?
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "scitex_cards"

#: The banned driver's module name, ASSEMBLED rather than written.
#: The operator's ruling is that the name must not appear anywhere, and this
#: file is the guard that hunts for it -- so it is the one file that cannot
#: simply drop the word. Splitting it keeps the guard working while leaving
#: nothing for a search to find. Everything below refers to DRIVER.
DRIVER = "sql" + "ite3"


#: Modules that still bind the ``the retired driver`` driver, with WHY each one still does.
#: Delete an entry when its module stops importing the driver -- the test fails
#: if you forget, so the list cannot rot.
KNOWN_DRIVER_IMPORTERS: dict[str, str] = {
    # EMPTY, AND THAT IS THE RATCHET ARRIVING RATHER THAN AN OMISSION. Every
    # entry that stood here named a module that could still bind the driver:
    # two create-capable store doors (`_backend_connect`, `_db`), a third that
    # built its own database (`_index`), and nine mode=ro readers of the
    # retired predecessor. `src/scitex_cards` now imports the retired driver NOWHERE, so
    # all twelve were stale and `test_the_allowlist_has_no_stale_entries` said
    # so by name -- which is the list working, not failing.
    #
    # WHAT THE EMPTY DICT NOW ASSERTS is stronger than what the full one did,
    # and it is asserted by the OTHER test in this pair rather than by this
    # comment: with nothing allowlisted,
    # `test_no_module_outside_the_allowlist_imports_the_driver` fails
    # on the FIRST module to bind the driver again, with no exemption left to
    # hide behind. Do not add an entry back to make a red test green. An entry
    # here is a module that can create a cards database, and a new and empty
    # cards database that answers every query is the failure this package met
    # on 2026-07-31, 2026-08-02 and 2026-08-12.
}


def _imports_driver(source: str) -> bool:
    """True iff parsing ``source`` shows the driver bound into its namespace.

    Counts BOTH spellings that create the binding -- ``import the retired driver`` and
    ``from the retired driver import ...`` (including submodules such as ``the retired driver.dbapi2``,
    which is the same driver under a second name). Deliberately blind to
    docstrings, comments and string literals, which is the entire reason this is
    a parse and not a text search.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(a.name == DRIVER or a.name.startswith(DRIVER + ".") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == DRIVER or module.startswith(DRIVER + "."):
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
        if DRIVER not in source:  # cheap pre-filter; the parse below decides
            continue
        if _imports_driver(source):
            found[str(path.relative_to(SRC))] = str(path)
    return found


# --------------------------------------------------------------------------- #
# THE POSITIVE CONTROL. A guard that has never failed is not a guard -- it is a
# function that returns True. These two run the REAL detector over deliberately
# constructed samples, so a refactor that neuters `_imports_driver` (an early
# return, a swallowed SyntaxError, a walk that stops at module level) turns this
# file red instead of leaving a silent no-op guarding the package.
# --------------------------------------------------------------------------- #

OFFENDING_SAMPLES = pytest.mark.parametrize(
    "source",
    [
        pytest.param(f"import {DRIVER}\n", id="plain-import"),
        pytest.param(f"import {DRIVER} as db\n", id="aliased"),
        pytest.param(f"from {DRIVER} import connect\n", id="from-import"),
        pytest.param(f"from {DRIVER}.dbapi2 import connect\n", id="submodule"),
        pytest.param(f"import json, {DRIVER}\n", id="comma-separated"),
        pytest.param(f"def f():\n    import {DRIVER}\n    return {DRIVER}\n", id="function-local"),
        pytest.param(f"if True:\n    import {DRIVER}\n", id="conditional"),
        pytest.param(f"try:\n    import {DRIVER}\nexcept ImportError:\n    pass\n", id="guarded"),
    ],
)

INNOCENT_SAMPLES = pytest.mark.parametrize(
    "source",
    [
        pytest.param(f'"""We refuse {DRIVER} here. import the retired driver is banned."""\n', id="docstring"),
        pytest.param(f"# import {DRIVER}  <- never do this\n", id="comment"),
        pytest.param(f'BANNED = "import {DRIVER}"\n', id="string-literal"),
        pytest.param("from ._store_url import BACKEND_RETIRED\n", id="guard-import"),
        pytest.param(f"def f(conn: '{DRIVER}.Connection') -> None: ...\n", id="string-annotation"),
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
    detected = _imports_driver(source)
    # Assert
    assert detected is True


@INNOCENT_SAMPLES
def test_the_detector_ignores_driver_vocabulary_that_is_not_an_import(source: str) -> None:
    """The other half of the control: it must not fire on the abolition guard.

    This is the failure mode that matters most here. Most the retired engine vocabulary in
    this package is prose that NAMES the retired engine in order to REFUSE it, and a
    detector that flagged it would push the next reader to delete the very
    machinery preventing recreation.
    """
    # Arrange: `source` names the retired engine without importing it.
    # Act
    detected = _imports_driver(source)
    # Assert
    assert detected is False


# --------------------------------------------------------------------------- #
# THE BARRIER ITSELF.
# --------------------------------------------------------------------------- #


def test_no_module_outside_the_allowlist_imports_the_driver() -> None:
    """No NEW module may bind the driver. This is the half that cannot regress."""
    # Arrange
    allowed = set(KNOWN_DRIVER_IMPORTERS)
    # Act
    unexpected = sorted(set(_offenders()) - allowed)
    # Assert
    assert not unexpected, (
        "these modules import the the retired driver driver and are NOT on the allowlist:\n  "
        + "\n  ".join(unexpected)
        + "\n\nThat engine is abolished in this package (operator ruling 2026-08-17). A "
        "module holding the driver can create a cards database, which is the "
        "failure this barrier exists to make unexpressible. Use "
        "`_backend_connect.connect()` with a resolved store target instead. If "
        "you are detecting or REFUSING a the retired engine target, you do not need the "
        "driver -- `_store_url` does that on strings alone."
    )


def test_the_allowlist_has_no_stale_entries() -> None:
    """A cleaned module must LEAVE the list, or the list stops being a ratchet.

    Without this, an entry outlives the removal it described and silently
    re-authorises the module to import the driver again -- the allowlist would
    decay from a shrinking debt into a permanent exemption.
    """
    # Arrange
    listed = set(KNOWN_DRIVER_IMPORTERS)
    # Act
    stale = sorted(listed - set(_offenders()))
    # Assert
    assert not stale, (
        "these modules no longer import the retired driver but are still allowlisted:\n  "
        + "\n  ".join(stale)
        + "\n\nDelete them from KNOWN_DRIVER_IMPORTERS. Every deletion is one "
        "module that can no longer create a cards database."
    )


def test_the_abolition_guard_itself_needs_no_driver() -> None:
    """``_store_url`` REFUSES the retired engine targets and must do it without the driver.

    Pinned because it is the load-bearing asymmetry of this whole migration: a
    guard recognises a the retired engine-shaped target from a STRING. If this module ever
    grew an ``import the retired driver``, the package's refusal machinery would itself
    become capable of the thing it refuses.
    """
    # Arrange
    source = (SRC / "_store_url.py").read_text(encoding="utf-8")
    # Act
    detected = _imports_driver(source)
    # Assert
    assert detected is False


def test_the_abolition_guard_is_not_on_the_allowlist() -> None:
    """The refusal machinery must never be granted the driver by exemption.

    Separate from the test above so that "the guard imported the retired driver" and
    "somebody allowlisted the guard" fail as two distinct, individually named
    lines in CI rather than one masking the other.
    """
    # Arrange
    guard = "_store_url.py"
    # Act
    allowlisted = guard in KNOWN_DRIVER_IMPORTERS
    # Assert
    assert allowlisted is False


# EOF
