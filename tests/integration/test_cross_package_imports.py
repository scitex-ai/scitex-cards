#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-package integration gate (PS-140) — runtime import contract.

``scitex_cards`` imports modules from sibling SciTeX packages. Those imports are
guarded in source so a lean install still works, which means a renamed or
deleted sibling API does not fail at import time — it silently takes a fallback.
This gate is what turns that silence into a test failure.

TWO DEFECTS FIXED HERE 2026-08-16, both of which made this gate report success
while covering less than it claimed.

1. IT SKIPPED THE FAILURE IT EXISTS TO CATCH. Every case ran
   ``pytest.importorskip(module_name)`` on the FULL dotted path.
   ``importorskip`` skips on ``ModuleNotFoundError``, which is an ``ImportError``
   subclass — so a submodule that has been RENAMED OR DELETED while its parent
   is installed produces a SKIP, and the suite reports green. That is exactly
   the scenario in the old docstring's own example. Measured: ``scitex_dev`` was
   installed, ``scitex_dev._mcp_cli`` was gone, and this gate skipped it.

   The fix separates two cases that were spelled identically:

       pytest.importorskip(ROOT)              # peer absent  -> legitimately skip
       importlib.import_module(FULL_PATH)     # peer present -> must IMPORT or FAIL

2. IT COVERED 3 OF 7 WHILE ASSERTING IT COVERED ALL. The list named
   ``scitex_app._django``, ``scitex_dev._mcp_cli`` and ``scitex_ui`` under a
   docstring saying it "must list exactly the cross-package modules imported
   under src/". src/ imported seven. scitex-dev ran their collector against this
   tree and confirmed the generator is correct and this FILE was stale — written
   when the tree had three and never regenerated as it grew. A gate whose stated
   scope exceeds its real scope is worse than a smaller honest one, because
   nobody re-checks a box that says covered.

``scitex_dev._mcp_cli`` is deliberately ABSENT from the list below: the helper
was retired upstream and ``_cli/_mcp.py`` no longer imports it, so listing it
would re-introduce the drift this file is meant to detect. Keep the list in step
with the source — ``scitex-dev`` audit PS-140 recomputes it and reports drift in
BOTH directions, so the invariant is "the auditor agrees", not "this list looked
right once".

No mocks (STX-NM / PA-306).
"""

from __future__ import annotations

import importlib

import pytest

#: Exactly the cross-package modules imported under src/ (PS-140 verifies this).
CROSS_PACKAGE_IMPORTS = [
    "scitex_app._django",
    "scitex_config._ecosystem",
    "scitex_dev.ecosystem",
    "scitex_dev.jobs",
    "scitex_dev.staleness",
    "scitex_ui",
]


def _root_of(dotted: str) -> str:
    """The installable distribution's top-level package name."""
    return dotted.split(".", 1)[0]


@pytest.mark.parametrize("module_name", CROSS_PACKAGE_IMPORTS)
def test_cross_package_dependency_imports_cleanly(module_name):
    # Arrange -- skip ONLY when the sibling package itself is absent (lean
    # install). A present package with a missing submodule must NOT skip.
    pytest.importorskip(_root_of(module_name))
    # Act
    module = importlib.import_module(module_name)
    # Assert
    assert module is not None


def test_board_appconfig_subclasses_scitex_app_when_installed():
    # Arrange -- importorskip on the ROOT, then import the submodule for real;
    # the old form skipped on the dotted path and so could not fail.
    pytest.importorskip("scitex_app")
    scitex_app_django = importlib.import_module("scitex_app._django")
    from scitex_cards._django.apps import ScitexCardsConfig

    # Act
    is_scitex_app_subclass = issubclass(
        ScitexCardsConfig, scitex_app_django.ScitexAppConfig
    )
    # Assert
    assert is_scitex_app_subclass


def test_a_missing_submodule_of_a_present_package_raises_rather_than_skips():
    # Arrange -- the positive control. This gate's whole purpose is to notice a
    # renamed/deleted submodule, and the previous shape turned that into a skip.
    # `pytest` is certainly installed, so the root import cannot be the reason.
    pytest.importorskip("pytest")
    # Act
    absent = "pytest._this_submodule_does_not_exist"
    # Assert
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(absent)


def test_importorskip_on_a_dotted_path_would_have_skipped_instead():
    # Arrange -- demonstrates WHY the shape changed, so a future reader does not
    # "simplify" it back. Same absent submodule, old form: skipped, not raised.
    outcome = "raised"
    # Act
    try:
        pytest.importorskip("pytest._this_submodule_does_not_exist")
    except BaseException as exc:  # noqa: BLE001 -- Skipped is a BaseException
        outcome = type(exc).__name__
    # Assert
    assert outcome == "Skipped", f"expected the old form to skip, got {outcome}"


# EOF
