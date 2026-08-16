#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-package integration gate (PS-140) — runtime import contract.

``scitex_cards._django`` optionally integrates with two sibling SciTeX
packages: ``scitex_app._django`` (so the board can register as a scitex-hub
module) and ``scitex_ui`` (shared Django shell components). Both imports are
guarded in source, so a lean install still works. This gate proves that when
the ``[web]``/``[dev]`` extras ARE installed, those imports actually resolve —
catching a renamed/moved sibling API before it ships.

``CROSS_PACKAGE_IMPORTS`` is the audited source of truth: it must list exactly
the cross-package modules imported under ``src/`` (audit-project regenerates /
verifies it). Keep it in sync with the guarded imports in ``_django/apps.py``
and ``_django/settings.py``.
"""

from __future__ import annotations

import importlib

import pytest

# Exactly the cross-package imports found under src/ (PS-140 verifies this set).
CROSS_PACKAGE_IMPORTS = [
    "scitex_app._django",
    "scitex_dev._mcp_cli",
    "scitex_ui",
]


@pytest.mark.parametrize("module_name", CROSS_PACKAGE_IMPORTS)
def test_cross_package_dependency_imports_cleanly(module_name):
    # Arrange — skip when the optional sibling isn't installed (lean install).
    pytest.importorskip(module_name)
    # Act
    module = importlib.import_module(module_name)
    # Assert
    assert module is not None


def test_board_appconfig_subclasses_scitex_app_when_installed():
    # Arrange — only meaningful once scitex-app is on the path.
    scitex_app_django = pytest.importorskip("scitex_app._django")
    from scitex_cards._django.apps import ScitexCardsConfig

    # Act
    is_scitex_app_subclass = issubclass(
        ScitexCardsConfig, scitex_app_django.ScitexAppConfig
    )
    # Assert
    assert is_scitex_app_subclass


# EOF
