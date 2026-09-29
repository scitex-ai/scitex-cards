#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PS-211 smoke layer: fast (<60s) subprocess-driven CLI happy-path tests.

Runs on every PR. Hermetic by construction: every case below drives the real
installed entry point (``python -m scitex_cards``) as a subprocess with
``--help``-level arguments only, so no store — throwaway or otherwise — is
ever touched. Anything that needs a store belongs in ``tests/e2e/``.

PA-307: exactly one ``assert`` per test, with ``# Arrange`` / ``# Act`` /
``# Assert`` markers in order — hence one behaviour per test function.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.smoke


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scitex_cards", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_top_level_help_exits_zero() -> None:
    # Arrange
    args = ("--help",)
    # Act
    result = _run(*args)
    # Assert
    assert result.returncode == 0


def test_top_level_help_lists_the_add_verb() -> None:
    # Arrange
    args = ("--help",)
    # Act
    result = _run(*args)
    # Assert
    assert "add" in result.stdout


def test_top_level_help_lists_the_list_tasks_verb() -> None:
    # Arrange
    args = ("--help",)
    # Act
    result = _run(*args)
    # Assert
    assert "list-tasks" in result.stdout


def test_add_help_exits_zero() -> None:
    # Arrange
    args = ("add", "--help")
    # Act
    result = _run(*args)
    # Assert
    assert result.returncode == 0


def test_add_help_documents_the_id_title_contract() -> None:
    # Arrange
    args = ("add", "--help")
    # Act
    result = _run(*args)
    # Assert
    assert "ID TITLE" in result.stdout


def test_version_flag_exits_zero() -> None:
    # Arrange
    args = ("--version",)
    # Act
    result = _run(*args)
    # Assert
    assert result.returncode == 0


def test_version_flag_reports_a_version() -> None:
    # Arrange
    args = ("--version",)
    # Act
    result = _run(*args)
    # Assert
    assert result.stdout.strip() or result.stderr.strip()
