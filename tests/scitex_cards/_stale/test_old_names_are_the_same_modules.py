#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The ``_stale_active*`` -> ``_stale/`` move keeps the old names IMPORTABLE
and, more importantly, keeps them the SAME MODULE OBJECTS (no mocks).

Grouping the family into a subpackage (2026-08-15, operator: 144 flat
top-level modules was "shockingly dirty") repointed every in-repo caller to the
new path. That means THE SUITE ITSELF CANNOT NOTICE if the old names stop
working — every test was updated to the new path in the same change, so a
totally broken shim would still show a green run.

This file is the pin for that blind spot, and it asserts IDENTITY rather than
mere importability. A shim written as ``from ._stale.active import *`` would
satisfy "the old name imports" while creating a SECOND module object, and a
second execution forks module-level state — thresholds read once from the
environment, caches, and anything else evaluated at import time. Two modules
disagreeing about the nudge threshold is a silent behaviour change that no
import check would catch. :mod:`scitex_cards`'s package-level shim avoids the
same hazard the same way and documents it.

Card ``cards-package-144-flat-modules-20260815``; pattern from PR #785.
"""

from __future__ import annotations

import importlib

import pytest

# (old top-level name, new dotted path inside the subpackage)
MOVED = [
    ("scitex_cards._stale_active", "scitex_cards._stale.active"),
    ("scitex_cards._stale_active_clocks", "scitex_cards._stale.active_clocks"),
    ("scitex_cards._stale_active_lines", "scitex_cards._stale.active_lines"),
    ("scitex_cards._stale_active_nudge", "scitex_cards._stale.active_nudge"),
    (
        "scitex_cards._stale_active_thresholds",
        "scitex_cards._stale.active_thresholds",
    ),
]


@pytest.mark.parametrize(("old", "new"), MOVED)
def test_the_old_top_level_name_is_the_same_module_object(old, new):
    # Arrange
    # Act
    old_mod = importlib.import_module(old)
    new_mod = importlib.import_module(new)
    # Assert
    assert old_mod is new_mod


@pytest.mark.parametrize(("old", "new"), MOVED)
def test_the_moved_module_is_importable_at_its_new_path(old, new):
    # Arrange
    # Act
    mod = importlib.import_module(new)
    # Assert
    assert mod is not None


@pytest.mark.parametrize(("old", "_new"), MOVED)
def test_the_old_name_resolves_as_a_package_attribute(old, _new):
    # `from scitex_cards import _stale_active` binds the module as a plain
    # NAME on the package — the import shape #785 found by a test failure
    # after a dotted-prefix grep reported clean. Attribute access is a
    # different code path from import_module, so it gets its own pin.
    # Arrange
    pkg = importlib.import_module("scitex_cards")
    attr = old.rsplit(".", 1)[1]
    importlib.import_module(old)
    # Act
    resolved = getattr(pkg, attr)
    # Assert
    assert resolved is importlib.import_module(_new)


def test_the_public_sweep_entry_point_survived_the_move():
    # The one name the rest of the package actually calls across the boundary.
    # Arrange
    # Act
    mod = importlib.import_module("scitex_cards._stale.active_nudge")
    # Assert
    assert callable(mod.sweep_and_nudge)


def test_the_detector_entry_point_survived_the_move():
    # Arrange
    # Act
    mod = importlib.import_module("scitex_cards._stale.active")
    # Assert
    assert callable(mod.detect_stale_active)
