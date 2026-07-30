#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.3 — blocked_tasks() introspection + `scitex-todo blocked` CLI.

Lead a2a `74db4f2d`, 2026-06-14. Inverse of `runnable_tasks`: for
every task the dispatcher can NOT pick up, name the reason
(`explicit-blocker` / `manual-block` / `depends-on` /
`reverse-blocks`) + the chain of upstream ids keeping it parked.

No mocks (STX-NM / PA-306). AAA pattern, one assertion per test.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._runnable import (
    BLOCKED_REASONS,
    BlockedSet,
    BlockedTask,
    blocked_tasks,
)
from scitex_cards._store import add_task

# === Reason discrimination ==================================================


def test_explicit_blocker_reason_when_blocker_field_set():
    # Arrange — status=blocked + blocker=<kind>.
    tasks = [
        {
            "id": "t-a",
            "title": "x",
            "status": "blocked",
            "blocker": "operator-decision",
        }
    ]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    assert result.tasks[0].reason == "explicit-blocker"


def test_explicit_blocker_chain_carries_blocker_label():
    # Arrange
    tasks = [
        {
            "id": "t-a",
            "title": "x",
            "status": "blocked",
            "blocker": "compute",
        }
    ]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    assert result.tasks[0].chain == ("compute",)


def test_manual_block_when_status_blocked_no_blocker_field():
    # Arrange — status=blocked + no blocker label.
    tasks = [{"id": "t-a", "title": "x", "status": "blocked"}]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    assert result.tasks[0].reason == "manual-block"


def test_manual_block_chain_is_empty():
    # Arrange
    tasks = [{"id": "t-a", "title": "x", "status": "blocked"}]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    assert result.tasks[0].chain == ()


def test_depends_on_reason_when_upstream_deferred():
    # Arrange — t-b's depends_on is unresolved.
    tasks = [
        {"id": "t-up", "title": "up", "status": "deferred"},
        {"id": "t-b", "title": "down", "status": "deferred", "depends_on": ["t-up"]},
    ]
    # Act
    result = blocked_tasks(tasks)
    # Assert — t-b is blocked by depends-on; t-up is RUNNABLE so NOT
    # in the blocked list.
    bt_b = [bt for bt in result.tasks if bt.id == "t-b"][0]
    assert bt_b.reason == "depends-on"


def test_depends_on_chain_carries_unresolved_upstream_ids():
    # Arrange
    tasks = [
        {"id": "t-up", "title": "up", "status": "deferred"},
        {"id": "t-b", "title": "down", "status": "deferred", "depends_on": ["t-up"]},
    ]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    bt_b = [bt for bt in result.tasks if bt.id == "t-b"][0]
    assert bt_b.chain == ("t-up",)


def test_reverse_blocks_reason_when_upstream_z_blocks_us():
    # Arrange — Z has `blocks: [t-b]` and Z is deferred.
    tasks = [
        {"id": "t-z", "title": "blocker", "status": "deferred", "blocks": ["t-b"]},
        {"id": "t-b", "title": "downstream", "status": "deferred"},
    ]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    bt_b = [bt for bt in result.tasks if bt.id == "t-b"][0]
    assert bt_b.reason == "reverse-blocks"


# === Excluded statuses (not "blocked," just finished or by-design) =========


def test_done_task_not_in_blocked_list():
    # Arrange
    tasks = [{"id": "t-a", "title": "x", "status": "done"}]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    assert result.tasks == []


def test_deferred_task_not_in_blocked_list():
    # Arrange
    tasks = [{"id": "t-a", "title": "x", "status": "deferred"}]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    assert result.tasks == []


def test_failed_task_not_in_blocked_list():
    # Arrange
    tasks = [{"id": "t-a", "title": "x", "status": "failed"}]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    assert result.tasks == []


def test_goal_umbrella_not_in_blocked_list():
    # Arrange — goals accumulate by design; not blocked.
    tasks = [{"id": "t-a", "title": "x", "status": "goal"}]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    assert result.tasks == []


# === Runnable task is NOT in the blocked list =============================


def test_runnable_task_is_excluded():
    # Arrange — deferred task with no deps; this is RUNNABLE, not blocked.
    tasks = [{"id": "t-a", "title": "x", "status": "deferred"}]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    assert result.tasks == []


# === by_reason histogram ===================================================


def test_by_reason_histogram_counts_each_reason():
    # Arrange — 2 explicit-blockers + 1 depends-on.
    tasks = [
        {
            "id": "t-a",
            "title": "x",
            "status": "blocked",
            "blocker": "operator-decision",
        },
        {"id": "t-b", "title": "y", "status": "blocked", "blocker": "compute"},
        {"id": "t-up", "title": "up", "status": "deferred"},
        {"id": "t-c", "title": "down", "status": "deferred", "depends_on": ["t-up"]},
    ]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    assert result.by_reason.get("explicit-blocker") == 2


def test_total_count_matches_task_list_length():
    # Arrange
    tasks = [
        {"id": "t-a", "title": "x", "status": "blocked"},
        {
            "id": "t-b",
            "title": "y",
            "status": "blocked",
            "blocker": "operator-decision",
        },
    ]
    # Act
    result = blocked_tasks(tasks)
    # Assert
    assert result.total == len(result.tasks)


# === agent + group filters mirror runnable's =============================


def test_agent_filter_narrows_blocked_list():
    # Arrange
    tasks = [
        {
            "id": "t-mine",
            "title": "x",
            "status": "blocked",
            "agent": "proj-scitex-todo",
        },
        {"id": "t-other", "title": "y", "status": "blocked", "agent": "proj-other"},
    ]
    # Act
    result = blocked_tasks(tasks, agent="proj-scitex-todo")
    # Assert
    assert [bt.id for bt in result.tasks] == ["t-mine"]


def test_group_filter_narrows_blocked_list():
    # Arrange
    tasks = [
        {
            "id": "t-paper",
            "title": "x",
            "status": "blocked",
            "group": "paper-portfolio",
        },
        {"id": "t-ci", "title": "y", "status": "blocked", "group": "ci-recovery"},
    ]
    # Act
    result = blocked_tasks(tasks, group="paper-portfolio")
    # Assert
    assert [bt.id for bt in result.tasks] == ["t-paper"]


# === BLOCKED_REASONS sanity ================================================


def test_blocked_reasons_constant_has_four_entries():
    # Arrange
    # Act
    # Assert
    assert BLOCKED_REASONS == frozenset(
        {
            "explicit-blocker",
            "manual-block",
            "depends-on",
            "reverse-blocks",
        }
    )


# === CLI =================================================================


def test_cli_blocked_lists_blocked_tasks():
    # Arrange
    add_task(
        id="t-a",
        title="x",
        status="blocked",
        blocker="operator-decision",
        assignee="agent:test-suite",
    )
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["blocked"])
    # Assert
    assert result.exit_code == 0


def test_cli_blocked_json_emits_structured_payload():
    # Arrange
    add_task(
        id="t-a",
        title="x",
        status="blocked",
        blocker="operator-decision",
        assignee="agent:test-suite",
    )
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["blocked", "--json"],
    )
    # Assert
    payload = json.loads(result.output)
    assert set(payload.keys()) == {"tasks", "total", "by_reason"}


def test_cli_blocked_empty_queue_emits_clear_message():
    # Arrange — no blocked tasks at all.
    add_task(id="t-a", title="x", status="deferred", assignee="agent:test-suite")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["blocked"])
    # Assert — exit 0 (queue clear is a SUCCESS not a failure).
    assert result.exit_code == 0
