#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The ``_reminder_*`` -> ``_reminder/`` move keeps the old names IMPORTABLE
and, more importantly, keeps them the SAME MODULE OBJECTS (no mocks).

Grouping the family into a subpackage (operator: 144 flat top-level modules was
"shockingly dirty") repointed every in-repo caller to the new path. That means
THE SUITE ITSELF CANNOT NOTICE if the old names stop working — every caller was
updated in the same change, so a totally broken shim would still show a green
run.

This file is the pin for that blind spot, and it asserts IDENTITY rather than
mere importability. A shim written as ``from ._reminder.bodies import *`` would
satisfy "the old name imports" while creating a SECOND module object, and a
second execution forks module-level state — the digest floor read once from the
environment, caches, anything else evaluated at import time. Two modules
disagreeing about the floor is a silent behaviour change that no import check
would catch.

Second family after ``_stale/`` (#856); card
``cards-package-144-flat-modules-20260815``; pattern from PR #785.
"""

from __future__ import annotations

import importlib

import pytest

# (old top-level name, new dotted path inside the subpackage)
MOVED = [
    ("scitex_cards._reminder_bodies", "scitex_cards._reminder.bodies"),
    ("scitex_cards._reminder_cadence", "scitex_cards._reminder.cadence"),
    ("scitex_cards._reminder_enqueue", "scitex_cards._reminder.enqueue"),
    ("scitex_cards._reminder_liveness", "scitex_cards._reminder.liveness"),
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
    # `from scitex_cards import _reminder_bodies` binds the module as a plain
    # NAME on the package — the import shape #785 found by a test failure after
    # a dotted-prefix grep reported clean. Attribute access is a different code
    # path from import_module, so it gets its own pin.
    # Arrange
    pkg = importlib.import_module("scitex_cards")
    attr = old.rsplit(".", 1)[1]
    importlib.import_module(old)
    # Act
    resolved = getattr(pkg, attr)
    # Assert
    assert resolved is importlib.import_module(_new)


def test_the_engine_still_imports_its_four_slices():
    # `_reminders` is the state machine these four were extracted from and the
    # only in-package caller of all four — if the new paths are wrong there,
    # nothing else in the family runs.
    # Arrange
    # Act
    mod = importlib.import_module("scitex_cards._reminders")
    # Assert
    assert callable(mod.sweep_reminders)


def test_the_cross_family_caller_still_reaches_enqueue():
    # `_stale/active_nudge.py` imports `_safe_enqueue` from this family, so the
    # move crosses a subpackage boundary a same-package test would miss.
    # Arrange
    # Act
    mod = importlib.import_module("scitex_cards._stale.active_nudge")
    # Assert
    assert callable(mod.sweep_and_nudge)
