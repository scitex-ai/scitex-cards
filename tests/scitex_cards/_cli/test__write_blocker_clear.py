#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI gap fix: `scitex-todo update --blocker ''` / `--blocker none`
should CLEAR the blocker field (dev a2a, lead `f5a54f85`).

Lives in a sibling test file (not `test__write.py`) to stay under the
512-line file-size limit on tests/. AAA, no mocks (STX-NM / PA-306),
one assertion per test.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._store import add_task, list_tasks

# === fixtures ===============================================================


@pytest.fixture()
def store_with_blocked_task() -> None:
    add_task(
        id="t-blocker-clear",
        title="card to test clear-blocker",
        status="blocked",
        blocker="operator-decision",
        assignee="agent:test-suite",
    )


def _read_back(tid: str) -> dict:
    for t in list_tasks():
        if t.get("id") == tid:
            return t
    raise AssertionError(f"missing {tid!r} in the store")


# === --blocker '' clears the field ==========================================


def test_empty_string_clear_blocker_runs_without_error(store_with_blocked_task):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["update", "t-blocker-clear", "--blocker", "", "-y"],
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_empty_string_clear_blocker_deletes_field(store_with_blocked_task):
    # Arrange
    runner = CliRunner()
    # Act
    runner.invoke(
        main,
        ["update", "t-blocker-clear", "--blocker", "", "-y"],
    )
    # Assert
    assert "blocker" not in _read_back("t-blocker-clear")


# === --blocker none clears the field (string sentinel) =====================


def test_none_sentinel_clears_blocker_field(store_with_blocked_task):
    # Arrange
    runner = CliRunner()
    # Act
    runner.invoke(
        main,
        ["update", "t-blocker-clear", "--blocker", "none", "-y"],
    )
    # Assert
    assert "blocker" not in _read_back("t-blocker-clear")


def test_none_sentinel_case_insensitive(store_with_blocked_task):
    # Arrange — caller passes "NONE" (mixed case); should still clear.
    runner = CliRunner()
    # Act
    runner.invoke(
        main,
        ["update", "t-blocker-clear", "--blocker", "NONE", "-y"],
    )
    # Assert
    assert "blocker" not in _read_back("t-blocker-clear")


# === closed-enum values still pass through unchanged =======================


def test_setting_blocker_to_valid_enum_value_still_works():
    # Arrange — round-trip a SET (not a clear) to make sure the new
    # ParamType doesn't break the existing closed-enum behavior.
    # Uses `compute` (a real VALID_BLOCKERS member, see _model.py).
    add_task(
        id="t-set",
        title="set blocker",
        status="pending",
        assignee="agent:test-suite",
    )
    runner = CliRunner()
    # Act
    runner.invoke(
        main,
        [
            "update",
            "t-set",
            "--status",
            "blocked",
            "--blocker",
            "compute",
            "-y",
        ],
    )
    # Assert
    assert _read_back("t-set").get("blocker") == "compute"


# === invalid values STILL rejected (no closed-enum bypass) ==================


def test_invalid_blocker_value_is_rejected(store_with_blocked_task):
    # Arrange — make sure '' / 'none' are the ONLY sentinels; arbitrary
    # strings still fail at parse time.
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        [
            "update",
            "t-blocker-clear",
            "--blocker",
            "not-a-real-blocker-kind",
            "-y",
        ],
    )
    # Assert
    assert result.exit_code != 0


# === omitting --blocker doesn't touch the field =============================


def test_omitting_blocker_flag_leaves_field_intact(store_with_blocked_task):
    # Arrange — update some OTHER field without naming --blocker; the
    # existing blocker must survive untouched.
    runner = CliRunner()
    # Act
    runner.invoke(
        main,
        [
            "update",
            "t-blocker-clear",
            "--note",
            "drive-by edit",
            "-y",
        ],
    )
    # Assert
    assert _read_back("t-blocker-clear").get("blocker") == "operator-decision"
