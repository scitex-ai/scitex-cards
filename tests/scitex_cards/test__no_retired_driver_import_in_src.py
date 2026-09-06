#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Only psycopg may be imported as a database driver.

Stated as an ALLOWLIST rather than a ban. A ban has to name what it forbids,
which puts the forbidden name back in the tree; an allowlist names only what is
permitted and catches every other driver, including ones nobody has thought of.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "scitex_cards"

#: The only database drivers this package may bind.
ALLOWED_DRIVERS = frozenset({"psycopg", "psycopg2", "psycopg_pool"})

#: A module is a DATABASE DRIVER if it satisfies PEP 249 -- it exposes
#: ``connect`` and ``paramstyle``. Detected by that SIGNATURE rather than by a
#: list of names, which means this file names no engine at all and still catches
#: a driver nobody has thought of yet.
def _is_db_driver(name: str) -> bool:
    import importlib.util

    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError, ModuleNotFoundError):
        return False
    if spec is None:
        return False
    try:
        mod = importlib.import_module(name)
    except Exception:
        return False
    return hasattr(mod, "connect") and hasattr(mod, "paramstyle")


def imported_drivers(source: str) -> set[str]:
    """Top-level driver modules bound anywhere in ``source``.

    Parses rather than greps: the name appears in docstrings and refusal
    messages throughout this package, and a textual check would report the
    refusal machinery as the offence it exists to prevent.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def _offenders() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        imported = imported_drivers(path.read_text(encoding="utf-8"))
        bad = {m for m in imported - ALLOWED_DRIVERS if _is_db_driver(m)}
        if bad:
            out[str(path.relative_to(SRC))] = bad
    return out


def test_src_binds_no_database_driver_outside_the_allowlist() -> None:
    """A module that never imports a driver cannot manufacture a database."""
    # Arrange
    src_root = SRC

    # Act
    offenders = _offenders()

    # Assert
    assert not offenders, (
        "these modules bind a database driver that is not allowlisted:\n  "
        + "\n  ".join(f"{mod}: {sorted(drv)}" for mod, drv in offenders.items())
        + f"\n\nOnly {sorted(ALLOWED_DRIVERS)} may be imported. If you are "
        "detecting or REFUSING another store, you do not need its driver to do "
        "it — compare the target string instead."
    )


def test_the_signature_check_recognises_a_real_driver() -> None:
    """Positive control.

    Without this the assertion above passes just as well when the check is
    broken -- an empty result and a neutered probe look identical. psycopg is
    itself a PEP 249 driver, so it is the control that costs no name.
    """
    # Arrange
    known_driver = "psycopg"

    # Act
    verdict = _is_db_driver(known_driver)

    # Assert
    assert verdict is True


def test_the_signature_check_rejects_a_non_driver() -> None:
    """Negative control: an ordinary module must not look like a driver."""
    # Arrange
    not_a_driver = "json"

    # Act
    verdict = _is_db_driver(not_a_driver)

    # Assert
    assert verdict is False


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("import psycopg\n", id="allowed-driver"),
        pytest.param("import json\n", id="unrelated"),
        pytest.param('"""A docstring naming a driver, which is not an import."""\n',
                     id="docstring"),
        pytest.param("# a comment naming a driver\n", id="comment"),
    ],
)
def test_the_detector_ignores_what_is_not_a_forbidden_import(source: str) -> None:
    """Prose and permitted imports are not violations."""
    # Arrange
    allowed = ALLOWED_DRIVERS

    # Act
    found = {m for m in imported_drivers(source) - ALLOWED_DRIVERS
             if _is_db_driver(m)}

    # Assert
    assert found == set()
