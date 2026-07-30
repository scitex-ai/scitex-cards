#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.2 — runnable_tasks() Python API + `scitex-todo runnable` CLI verb.

Lead a2a `74db4f2d`, 2026-06-14. The parallelism-engine dispatcher
asks "what's runnable right now?" — base filter + dep-closure + agent
+ group filters. Sister to `next_task` (single pick); this is the
batch view that lets the lead fan out work across agents/groups.

No mocks (STX-NM / PA-306). AAA pattern, one assertion per test.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._runnable import (
    RESOLVED_STATUSES,
    RUNNABLE_STATUSES,
    RunnableSet,
    runnable_tasks,
)
from scitex_cards._store import add_task

# === Base filter (status + blocker) ========================================


def test_deferred_task_is_runnable():
    # Arrange
    tasks = [{"id": "t-a", "title": "x", "status": "deferred"}]
    # Act
    result = runnable_tasks(tasks)
    # Assert
    assert [t["id"] for t in result.tasks] == ["t-a"]


def test_in_progress_task_is_also_runnable():
    # Arrange — `in_progress` is in RUNNABLE_STATUSES (agent resumes work).
    tasks = [{"id": "t-a", "title": "x", "status": "in_progress"}]
    # Act
    result = runnable_tasks(tasks)
    # Assert
    assert [t["id"] for t in result.tasks] == ["t-a"]


def test_done_task_is_not_runnable():
    # Arrange
    tasks = [{"id": "t-a", "title": "x", "status": "done"}]
    # Act
    result = runnable_tasks(tasks)
    # Assert
    assert result.tasks == []


def test_blocked_task_is_not_runnable():
    # Arrange — explicit blocker set; dispatcher should not pick it up.
    tasks = [
        {
            "id": "t-a",
            "title": "x",
            "status": "deferred",
            "blocker": "operator-decision",
        }
    ]
    # Act
    result = runnable_tasks(tasks)
    # Assert
    assert result.tasks == []


# === Dependency closure ====================================================


def test_task_blocked_by_deferred_upstream_is_filtered_out():
    # Arrange — t-b depends on t-a which is still deferred.
    tasks = [
        {"id": "t-a", "title": "upstream", "status": "deferred"},
        {
            "id": "t-b",
            "title": "downstream",
            "status": "deferred",
            "depends_on": ["t-a"],
        },
    ]
    # Act
    result = runnable_tasks(tasks)
    # Assert — only t-a runnable (t-a itself is dep-free).
    assert [t["id"] for t in result.tasks] == ["t-a"]


def test_task_becomes_runnable_when_upstream_done():
    # Arrange
    tasks = [
        {"id": "t-a", "title": "upstream", "status": "done"},
        {
            "id": "t-b",
            "title": "downstream",
            "status": "deferred",
            "depends_on": ["t-a"],
        },
    ]
    # Act
    result = runnable_tasks(tasks)
    # Assert
    assert [t["id"] for t in result.tasks] == ["t-b"]


def test_blocks_field_creates_implicit_upstream():
    # Arrange — t-a has `blocks: [t-b]` which is the SAME as t-b depends_on
    # t-a. The runnable engine respects both forms symmetrically.
    tasks = [
        {"id": "t-a", "title": "upstream", "status": "deferred", "blocks": ["t-b"]},
        {"id": "t-b", "title": "downstream", "status": "deferred"},
    ]
    # Act
    result = runnable_tasks(tasks)
    # Assert — only t-a runnable; t-b is implicitly blocked by t-a.
    assert [t["id"] for t in result.tasks] == ["t-a"]


def test_unknown_dep_id_is_permissive():
    # Arrange — depends_on points at an id that doesn't exist in the
    # store. The validator's ref-integrity check handles the
    # consistency case; the runnable engine is permissive so a stale
    # ref doesn't park the task forever.
    tasks = [
        {
            "id": "t-a",
            "title": "x",
            "status": "deferred",
            "depends_on": ["never-existed"],
        },
    ]
    # Act
    result = runnable_tasks(tasks)
    # Assert
    assert [t["id"] for t in result.tasks] == ["t-a"]


def test_blocked_by_deps_count_reflects_filtered_set():
    # Arrange — 1 task is base-runnable but blocked by deps; 1 is
    # truly runnable. Diagnostic counters should distinguish them.
    tasks = [
        {"id": "t-up", "title": "up", "status": "deferred"},
        {"id": "t-down", "title": "down", "status": "deferred", "depends_on": ["t-up"]},
    ]
    # Act
    result = runnable_tasks(tasks)
    # Assert
    assert result.blocked_by_deps_count == 1


# === agent filter ==========================================================


def test_agent_filter_matches_agent_field():
    # Arrange
    tasks = [
        {
            "id": "t-mine",
            "title": "x",
            "status": "deferred",
            "agent": "proj-scitex-todo",
        },
        {"id": "t-other", "title": "y", "status": "deferred", "agent": "proj-other"},
    ]
    # Act
    result = runnable_tasks(tasks, agent="proj-scitex-todo")
    # Assert
    assert [t["id"] for t in result.tasks] == ["t-mine"]


def test_agent_filter_matches_legacy_assignee_field():
    # Arrange — legacy `assignee` matches too (back-compat).
    tasks = [
        {
            "id": "t-a",
            "title": "x",
            "status": "deferred",
            "assignee": "proj-scitex-todo",
        }
    ]
    # Act
    result = runnable_tasks(tasks, agent="proj-scitex-todo")
    # Assert
    assert [t["id"] for t in result.tasks] == ["t-a"]


# === group filter (T1.1) ===================================================


def test_group_filter_matches_T11_group_field():
    # Arrange
    tasks = [
        {"id": "t-a", "title": "x", "status": "deferred", "group": "paper-portfolio"},
        {"id": "t-b", "title": "y", "status": "deferred", "group": "ci-recovery"},
    ]
    # Act
    result = runnable_tasks(tasks, group="paper-portfolio")
    # Assert
    assert [t["id"] for t in result.tasks] == ["t-a"]


def test_group_empty_string_matches_ungrouped_only():
    # Arrange — `group=""` is the "residual / ungrouped" filter.
    tasks = [
        {
            "id": "t-grouped",
            "title": "x",
            "status": "deferred",
            "group": "paper-portfolio",
        },
        {"id": "t-ungrouped", "title": "y", "status": "deferred"},
    ]
    # Act
    result = runnable_tasks(tasks, group="")
    # Assert
    assert [t["id"] for t in result.tasks] == ["t-ungrouped"]


# === Sort order: priority first, then recency ==============================


def test_lower_priority_number_picked_first():
    # Arrange — priority 1 picks before priority 5.
    tasks = [
        {"id": "t-late", "title": "x", "status": "deferred", "priority": 5},
        {"id": "t-early", "title": "y", "status": "deferred", "priority": 1},
    ]
    # Act
    result = runnable_tasks(tasks)
    # Assert
    assert [t["id"] for t in result.tasks] == ["t-early", "t-late"]


# === CLI verb ==============================================================


def test_cli_runnable_lists_runnable_tasks():
    # Arrange
    add_task(id="t-a", title="x", group="paper-portfolio", assignee="agent:test-suite")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["runnable"],
    )
    # Assert — exit 0 (something runnable), one line of output.
    assert result.exit_code == 0


def test_cli_runnable_json_emits_full_payload():
    # Arrange
    add_task(id="t-a", title="x", group="paper-portfolio", assignee="agent:test-suite")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["runnable", "--json"],
    )
    # Assert — JSON parses + has the expected keys.
    payload = json.loads(result.output)
    assert set(payload.keys()) == {"tasks", "candidate_count", "blocked_by_deps_count"}


def test_cli_runnable_group_filter_narrows_results():
    # Arrange
    add_task(
        id="t-paper", title="x", group="paper-portfolio", assignee="agent:test-suite"
    )
    add_task(id="t-ci", title="y", group="ci-recovery", assignee="agent:test-suite")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["runnable", "--group", "paper-portfolio", "--json"],
    )
    # Assert
    payload = json.loads(result.output)
    assert [t["id"] for t in payload["tasks"]] == ["t-paper"]


def test_cli_runnable_exit_1_when_queue_empty():
    # Arrange — empty store.
    add_task(id="t-done", title="x", status="done", assignee="agent:test-suite")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["runnable"],
    )
    # Assert — exit 1 lets shell scripts test "did the queue empty?"
    assert result.exit_code == 1


def test_cli_runnable_mine_uses_env_agent(env):
    # Arrange — --mine reads $SCITEX_TODO_AGENT_ID.
    add_task(id="t-a", title="x", agent="proj-scitex-todo")
    env.set("SCITEX_TODO_AGENT_ID", "proj-scitex-todo")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["runnable", "--mine", "--json"],
    )
    # Assert
    payload = json.loads(result.output)
    assert [t["id"] for t in payload["tasks"]] == ["t-a"]


# === Diagnostic constants exported =========================================


def test_runnable_statuses_contains_deferred_and_in_progress():
    # Arrange
    # Act
    # Assert
    assert RUNNABLE_STATUSES == frozenset({"deferred", "in_progress"})


def test_resolved_statuses_contains_done_and_goal():
    # upstream because they accumulate by design (not actionable).
    # Arrange
    # Act
    # Assert
    assert RESOLVED_STATUSES == frozenset({"done", "goal"})
