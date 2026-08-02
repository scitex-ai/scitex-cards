#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.1 — `group` field on Task (lead a2a `74db4f2d`, 2026-06-14).

The parallelism-engine dispatcher uses `group` to ask "what's runnable
in dispatch-cluster <G>" so independent tasks in the cluster run
concurrently. Free-form non-empty string; absent = ungrouped. Distinct
from `_groups.py`'s project-cluster Group dataclass (viewer
aggregation; this is per-task dispatch metadata).

No mocks (STX-NM / PA-306). AAA pattern, one assertion per test.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._model import (
    Task,
    TaskValidationError,
    _validate_tasks,
    save_tasks,
)
from scitex_cards._store import add_task, list_tasks, update_task

# === Task dataclass carries `group` ========================================


def test_task_dataclass_has_group_field():
    # Arrange
    # Act
    t = Task(id="t-a", title="x", group="ci-recovery-wave")
    # Assert
    assert t.group == "ci-recovery-wave"


def test_task_dataclass_group_defaults_to_none():
    # Arrange
    # Act
    t = Task(id="t-a", title="x")
    # Assert
    assert t.group is None


def test_task_dataclass_roundtrips_group_through_dict():
    # Arrange — to_dict / from_dict roundtrip preserves the field.
    t = Task(id="t-a", title="x", group="paper-portfolio")
    # Act
    d = t.to_dict()
    rebuilt = Task.from_dict(d)
    # Assert
    assert rebuilt.group == "paper-portfolio"


def test_task_dataclass_omits_none_group_from_to_dict():
    # Arrange — None defaults must NOT bloat the YAML wire shape.
    t = Task(id="t-a", title="x")
    # Act
    d = t.to_dict()
    # Assert
    assert "group" not in d


# === _validate_tasks accepts a valid `group` string ========================


def test_validate_accepts_non_empty_group_string():
    # Arrange
    tasks = [
        {"id": "t-a", "title": "x", "status": "pending", "group": "paper-portfolio"}
    ]
    accepted = False
    # Act
    _validate_tasks(tasks, source="<test>")
    accepted = True
    # Assert — a non-empty group string passes validation (no raise).
    assert accepted is True


def test_validate_accepts_absent_group():
    # Arrange
    tasks = [{"id": "t-a", "title": "x", "status": "pending"}]
    accepted = False
    # Act
    _validate_tasks(tasks, source="<test>")
    accepted = True
    # Assert — an absent group field passes validation (no raise).
    assert accepted is True


# === _validate_tasks rejects bad `group` shapes ===========================


def test_validate_rejects_empty_string_group():
    # Arrange
    tasks = [{"id": "t-a", "title": "x", "status": "pending", "group": ""}]
    # Act
    # Assert
    with pytest.raises(TaskValidationError):
        _validate_tasks(tasks, source="<test>")


def test_validate_rejects_non_string_group():
    # Arrange
    tasks = [{"id": "t-a", "title": "x", "status": "pending", "group": ["paper", "ci"]}]
    # Act
    # Assert
    with pytest.raises(TaskValidationError):
        _validate_tasks(tasks, source="<test>")


def test_validate_rejects_integer_group():
    # Arrange
    tasks = [{"id": "t-a", "title": "x", "status": "pending", "group": 42}]
    # Act
    # Assert
    with pytest.raises(TaskValidationError):
        _validate_tasks(tasks, source="<test>")


# === Python API: add_task + update_task pipe `group` through ==============


def test_add_task_persists_group():
    # Arrange
    # Act
    add_task(id="t-a", title="x", group="paper-portfolio", assignee="agent:test-suite")
    # Assert
    loaded = [t for t in list_tasks(scope="") if t["id"] == "t-a"][0]
    assert loaded.get("group") == "paper-portfolio"


def test_update_task_sets_group():
    # Arrange
    add_task(id="t-a", title="x", assignee="agent:test-suite")
    # Act
    update_task(task_id="t-a", group="ci-recovery-wave")
    # Assert
    loaded = [t for t in list_tasks(scope="") if t["id"] == "t-a"][0]
    assert loaded.get("group") == "ci-recovery-wave"


def test_update_task_clears_group_when_none_passed():
    # Arrange — group set first; then cleared by passing group=None.
    add_task(id="t-a", title="x", group="paper", assignee="agent:test-suite")
    # Act
    update_task(task_id="t-a", group=None)
    # Assert
    loaded = [t for t in list_tasks(scope="") if t["id"] == "t-a"][0]
    assert "group" not in loaded


# === CLI: --group flag on add + update ===================================


def test_cli_add_with_group_persists():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "t-a",
            "x",
            "--group",
            "paper-portfolio",
            "-y",
        ],
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_cli_update_with_group_sets_field():
    # Arrange
    add_task(id="t-a", title="x", assignee="agent:test-suite")
    runner = CliRunner()
    # Act
    runner.invoke(
        main,
        ["update", "t-a", "--group", "paper-portfolio", "-y"],
    )
    # Assert
    loaded = [t for t in list_tasks(scope="") if t["id"] == "t-a"][0]
    assert loaded.get("group") == "paper-portfolio"


def test_cli_update_with_empty_group_clears():
    # Arrange — group set first; --group '' clears (per the
    # existing update-clear-via-empty-string convention).
    add_task(id="t-a", title="x", group="paper", assignee="agent:test-suite")
    runner = CliRunner()
    # Act
    runner.invoke(
        main,
        ["update", "t-a", "--group", "", "-y"],
    )
    # Assert
    loaded = [t for t in list_tasks(scope="") if t["id"] == "t-a"][0]
    assert "group" not in loaded
