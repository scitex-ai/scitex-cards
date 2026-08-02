#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No module may depend on being imported SECOND.

WHY THIS FILE EXISTS (2026-07-14): `import scitex_cards._store_write` raised
ImportError when it was the FIRST scitex_cards import in a fresh interpreter --

    ImportError: cannot import name '_git_autocommit_store' from partially
    initialized module 'scitex_cards._store_write' (most likely due to a
    circular import)

-- while `import scitex_cards._model, scitex_cards._store_write` worked fine.
`_store_write` imports from `_model`; `_model` re-exported back from
`_store_write`. That cycle survives in exactly ONE direction.

Nothing inside the package tripped it, because every internal path reaches
`_store_write` through `_model`. So it was invisible for as long as we were the
only callers -- and then it killed a live data-repair script whose first import
was `from scitex_cards._store_write import edit_tasks`. The ImportError blamed a
circular import rather than the caller's line, which is the worst kind of error
message: technically true, and it points away from the fix.

THE TEST MUST USE A SUBPROCESS. In-process, `sys.modules` is already populated
by whatever the test session imported earlier, so EVERY order passes and the
test proves nothing. This is the same trap as running a benchmark against a
warm cache: the harness must test the thing under test.
"""

import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PKG = SRC / "scitex_cards"


def _discover_modules() -> list[str]:
    """Every top-level module in the package, DISCOVERED — not hand-listed.

    A hardcoded list is an enumeration, and an enumeration rots: a new module is
    born uncovered, silently. (It also lets you typo a name that does not exist
    and spend ten minutes debugging a "failure" in a module you invented — which
    is exactly what happened while writing this file.) Globbing means a module
    added tomorrow is tested tomorrow.

    Sub-packages (`_cli`, `_django`, `_notify`, ...) are excluded: they pull in
    optional extras (django, click plugins) whose absence is a DIFFERENT failure
    and would make this test flaky for reasons that have nothing to do with
    import order.
    """
    names = ["scitex_cards"]
    for path in sorted(PKG.glob("*.py")):
        if path.stem == "__init__":
            continue
        names.append(f"scitex_cards.{path.stem}")
    return names


MODULES = _discover_modules()


def _import_first(statement: str) -> subprocess.CompletedProcess:
    """Run `statement` as the FIRST scitex_cards import in a clean interpreter."""
    return subprocess.run(
        [sys.executable, "-c", statement],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
    )


@pytest.mark.parametrize("module", MODULES)
def test_module_can_be_imported_first(module):
    """Importing any module first must not raise. No module may require a warm-up."""
    # Arrange
    statement = f"import {module}"
    # Act
    result = _import_first(statement)
    # Assert
    assert result.returncode == 0, (
        f"`import {module}` FAILS as the first scitex_cards import:\n"
        f"{result.stderr}\n"
        "A module that only imports correctly when something else was imported "
        "first is a landmine for external callers. Break the cycle (lazy "
        "re-export / leaf module) -- do NOT fix it by relying on import order."
    )


def test_the_exact_regression_from_the_data_repair_script():
    """The real caller that broke: a from-import of a store verb, cold."""
    # Arrange
    statement = (
        "from scitex_cards._store_write import edit_tasks, save_tasks; "
        "assert callable(edit_tasks) and callable(save_tasks)"
    )
    # Act
    result = _import_first(statement)
    # Assert
    assert result.returncode == 0, result.stderr


def test_lazy_reexports_are_still_reachable_through_model():
    """Breaking the cycle must NOT break the re-export surface it existed for.

    43 test files and every fleet agent do `from scitex_cards._model import ...`.
    PEP 562 __getattr__ keeps that working -- `from X import Y` falls back to
    X.__getattr__ -- but only if the names actually resolve. Pin that.
    """
    # Arrange
    from scitex_cards._model import _STORE_WRITE_EXPORTS

    names = sorted(_STORE_WRITE_EXPORTS)
    # Act
    results = {
        name: _import_first(
            f"from scitex_cards._model import {name}; assert {name} is not None"
        )
        for name in names
    }
    failures = {n: r.stderr for n, r in results.items() if r.returncode != 0}
    # Assert — every name the eager re-export provided must still resolve.
    assert failures == {}, (
        "`from scitex_cards._model import <name>` broke -- the lazy re-export "
        "dropped names the eager one provided:\n"
        + "\n".join(f"{n}: {err}" for n, err in failures.items())
    )


#: WHY the three `*_reexport_is_the_same_object` tests below are split but share
#: one rationale: a re-export must be an ALIAS, not a copy. Identity, not just
#: presence — a name that resolves to a DIFFERENT object silently forks the
#: module-level store lock and the write verbs that depend on it.


def test_save_tasks_reexport_is_the_same_object():
    # Arrange
    import scitex_cards._model as model
    import scitex_cards._store_write as store_write

    # Act
    reexported = model.save_tasks
    # Assert
    assert reexported is store_write.save_tasks


def test_edit_tasks_reexport_is_the_same_object():
    # Arrange
    import scitex_cards._model as model
    import scitex_cards._store_write as store_write

    # Act
    reexported = model.edit_tasks
    # Assert
    assert reexported is store_write.edit_tasks


def test_store_lock_reexport_is_the_same_object():
    # Arrange
    import scitex_cards._model as model
    import scitex_cards._store_write as store_write

    # Act
    reexported = model._store_lock
    # Assert — two lock objects would mean two writers believing they hold it.
    assert reexported is store_write._store_lock


def test_unknown_attribute_still_raises_attribute_error():
    """__getattr__ must not swallow typos into an ImportError or None."""
    # Arrange
    import scitex_cards._model as model

    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(AttributeError):
        model.no_such_name_exists
