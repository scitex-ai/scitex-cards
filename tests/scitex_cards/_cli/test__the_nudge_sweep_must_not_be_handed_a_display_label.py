#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`print-stats --nudge-quiet` wrote its dedup ledger into a local database file."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_STATS = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_cli"
    / "_stats.py"
)

# The label-producing call. A value derived from this must never reach a
# consumer that treats its argument as a database location.
_LABEL_FACTORY = "resolve_tasks_path"

# Consumers whose `store` argument is run through `_db_target`.
_STORE_CONSUMERS = {"_emit_stale_active_nudges"}


@pytest.fixture
def stats_tree():
    """The parsed source of the CLI module under test."""
    return ast.parse(_STATS.read_text(encoding="utf-8"))


@pytest.fixture
def label_names(stats_tree):
    """Names bound to the result of `resolve_tasks_path(...)` in this module."""
    bound: set[str] = set()
    for node in ast.walk(stats_tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        fn = value.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        if name != _LABEL_FACTORY:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                bound.add(target.id)
    return bound


@pytest.fixture
def store_args(stats_tree):
    """The `store` argument of every call to a store-consuming helper."""
    found = []
    for node in ast.walk(stats_tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        if name not in _STORE_CONSUMERS:
            continue
        # signature is (tasks, store)
        if len(node.args) >= 2:
            found.append((name, node.args[1]))
    return found


def test_the_module_really_does_bind_a_label(label_names):
    """Calibration: if this stops matching, the scan below is vacuous."""
    # Arrange
    expected = "path"
    # Act
    bound = label_names
    # Assert
    assert expected in bound, f"no name bound to {_LABEL_FACTORY}(); scan is dead"


def test_the_scan_really_finds_the_call(store_args):
    """Calibration: a scan that finds no call sites cannot fail."""
    # Arrange
    consumers = _STORE_CONSUMERS
    # Act
    found = [name for name, _arg in store_args]
    # Assert
    assert found, f"no call to any of {consumers} found; scan is dead"


def test_no_store_consumer_is_handed_the_display_label(store_args, label_names):
    """THE REGRESSION. A label reaching `_db_target` becomes a sibling .db."""
    # Arrange
    offenders = []
    # Act
    for name, arg in store_args:
        if isinstance(arg, ast.Name) and arg.id in label_names:
            offenders.append(f"{name}(..., {arg.id}) at line {arg.lineno}")
    # Assert
    assert not offenders, (
        "a display label is being passed as a store: "
        + "; ".join(offenders)
        + " — pass None instead; each consumer resolves the backend itself"
    )


def test_the_nudge_sweep_is_handed_none(store_args):
    """The positive form: it must receive the backend-agnostic value."""
    # Arrange
    nudge_calls = [a for n, a in store_args if n == "_emit_stale_active_nudges"]
    # Act
    kinds = [isinstance(a, ast.Constant) and a.value is None for a in nudge_calls]
    # Assert
    assert all(kinds), "_emit_stale_active_nudges must be called with store=None"


# EOF
