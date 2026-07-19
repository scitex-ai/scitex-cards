#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``may_stop`` — the never-stop detector: runnable work blocks a stop.

The cards half of the never-stop infrastructure (operator 2026-07-18:
「通知だけしてもエージェントが無視する。決定的に止めるにはフックしかない」;
card ``may-stop-hook-cards-runnable-work-20260718``). The verdict's exit
code is the contract sac's Stop hook keys on: 0 = the board is empty for
this agent, 2 = runnable work exists and stderr carries the hint list the
idle-at-prompt re-drive injects.

Real store files, AAA, no mocks. The CLI is driven through click's
CliRunner (real argv → real exit codes).
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli._may_stop import may_stop_cmd
from scitex_cards._inbox import enqueue, poll_inbox
from scitex_cards._may_stop import may_stop
from scitex_cards._store import add_task


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return str(path)


def _drain(store, agent):
    """Ack the created-event notifications add_task itself enqueues."""
    poll_inbox(agent, unseen_only=True, mark_seen=True, store=store)


@pytest.fixture()
def in_progress_store(store):
    """One in_progress card owned by worker-a, its created-event drained."""
    add_task(store=store, id="w1", title="w1", status="in_progress", agent="worker-a")
    _drain(store, "worker-a")
    return store


@pytest.fixture()
def ungated_blocked_store(store):
    """A blocked card whose blocker 'none' names nothing — the agent must act."""
    add_task(
        store=store,
        id="w3",
        title="w3",
        status="blocked",
        blocker="none",
        agent="worker-a",
    )
    _drain(store, "worker-a")
    return store


@pytest.fixture()
def unread_inbox_store(store):
    """One unread DM notification for worker-a, no cards at all."""
    enqueue(
        "worker-a",
        event_type="dm",
        card_id="dm:x",
        body="hello",
        actor="peer",
        store=store,
    )
    return store


@pytest.fixture()
def cli_runnable_store(store):
    """One in_progress card (w7) for the CLI exit-2 contract."""
    add_task(store=store, id="w7", title="w7", status="in_progress", agent="worker-a")
    _drain(store, "worker-a")
    return store


# === the verdict ===========================================================


def test_empty_board_means_the_agent_may_stop(store):
    # Arrange
    agent = "worker-a"
    # Act
    verdict = may_stop(agent, store)
    # Assert
    assert verdict["runnable"] is False


def test_empty_board_verdict_lists_no_items(store):
    # Arrange
    agent = "worker-a"
    # Act
    verdict = may_stop(agent, store)
    # Assert
    assert verdict["items"] == []


def test_empty_board_verdict_has_no_idle_seconds(store):
    # Arrange
    agent = "worker-a"
    # Act
    verdict = may_stop(agent, store)
    # Assert — nothing is in flight, so there is no idle clock to report.
    assert verdict["idle_seconds"] is None


def test_an_in_progress_card_is_runnable_work(in_progress_store):
    # Arrange
    agent = "worker-a"
    # Act
    verdict = may_stop(agent, in_progress_store)
    # Assert
    assert verdict["runnable"] is True


def test_an_in_progress_card_is_listed_in_the_verdict(in_progress_store):
    # Arrange
    agent = "worker-a"
    # Act
    verdict = may_stop(agent, in_progress_store)
    # Assert — the agent is told WHICH card is holding it.
    assert [i["card_id"] for i in verdict["items"]] == ["w1"]


def test_an_in_progress_card_reports_idle_seconds(in_progress_store):
    # Arrange
    agent = "worker-a"
    # Act
    verdict = may_stop(agent, in_progress_store)
    # Assert
    assert verdict["idle_seconds"] is not None and verdict["idle_seconds"] >= 0


def test_a_blocked_card_with_a_named_gate_is_the_one_legitimate_wait(store):
    # Arrange: blocked WITH a named external gate — not runnable.
    add_task(
        store=store,
        id="w2",
        title="w2",
        status="blocked",
        blocker="dependency",
        agent="worker-a",
    )
    _drain(store, "worker-a")
    # Act
    verdict = may_stop("worker-a", store)
    # Assert
    assert verdict["runnable"] is False


def test_a_blocked_card_with_no_named_gate_is_runnable(ungated_blocked_store):
    # Arrange
    agent = "worker-a"
    # Act
    verdict = may_stop(agent, ungated_blocked_store)
    # Assert
    assert verdict["runnable"] is True


def test_a_blocked_card_with_no_named_gate_names_the_reason(ungated_blocked_store):
    # Arrange
    agent = "worker-a"
    # Act
    verdict = may_stop(agent, ungated_blocked_store)
    # Assert
    assert verdict["items"][0]["reason"] == "blocked with no named gate"


def test_a_deferred_card_whose_schedule_arrived_is_runnable(store):
    # Arrange: one past-scheduled, one future-scheduled.
    add_task(
        store=store,
        id="w4",
        title="w4",
        status="deferred",
        scheduled="2020-01-01",
        agent="worker-a",
    )
    add_task(
        store=store,
        id="w5",
        title="w5",
        status="deferred",
        scheduled="2099-01-01",
        agent="worker-a",
    )
    _drain(store, "worker-a")
    # Act
    verdict = may_stop("worker-a", store)
    # Assert: only the arrived schedule counts.
    assert [i["card_id"] for i in verdict["items"]] == ["w4"]


def test_unread_inbox_notifications_are_runnable_work(unread_inbox_store):
    # Arrange
    agent = "worker-a"
    # Act
    verdict = may_stop(agent, unread_inbox_store)
    # Assert
    assert verdict["runnable"] is True


def test_unread_inbox_item_is_labelled_inbox(unread_inbox_store):
    # Arrange
    agent = "worker-a"
    # Act
    verdict = may_stop(agent, unread_inbox_store)
    # Assert — an inbox item has no card, so it says so instead of faking one.
    assert verdict["items"][0]["card_id"] == "(inbox)"


def test_other_agents_cards_do_not_bind_this_agent(store):
    # Arrange
    add_task(store=store, id="w6", title="w6", status="in_progress", agent="worker-b")
    # Act
    verdict = may_stop("worker-a", store)
    # Assert
    assert verdict["runnable"] is False


# === the CLI contract (exit codes + hints) =================================


def test_cli_exit_zero_and_json_on_an_empty_board(store):
    # Arrange
    argv = ["--agent", "worker-a", "--tasks", store]
    # Act
    result = CliRunner().invoke(may_stop_cmd, argv)
    # Assert — exit 0 is the Stop hook's "you may stop" code.
    assert result.exit_code == 0


def test_cli_json_verdict_on_an_empty_board_is_not_runnable(store):
    # Arrange
    argv = ["--agent", "worker-a", "--tasks", store]
    # Act
    result = CliRunner().invoke(may_stop_cmd, argv)
    # Assert
    assert json.loads(result.output)["runnable"] is False


def test_cli_exits_two_on_runnable_work(cli_runnable_store):
    # Arrange
    argv = ["--agent", "worker-a", "--tasks", cli_runnable_store]
    # Act
    result = CliRunner().invoke(may_stop_cmd, argv)
    # Assert — exit 2 is the Stop hook's "refuse to stop" code.
    assert result.exit_code == 2


def test_cli_stdout_carries_the_runnable_json_verdict(cli_runnable_store):
    # Arrange
    argv = ["--agent", "worker-a", "--tasks", cli_runnable_store]
    # Act
    result = CliRunner().invoke(may_stop_cmd, argv)
    # Assert — stdout stays machine-readable even on the refusal path.
    assert json.loads(result.stdout)["runnable"] is True


def test_cli_exit_two_with_numbered_stderr_hints_on_runnable_work(cli_runnable_store):
    # Arrange
    argv = ["--agent", "worker-a", "--tasks", cli_runnable_store]
    # Act
    result = CliRunner().invoke(may_stop_cmd, argv)
    # Assert — stderr carries the numbered hints the re-drive injects.
    assert "1. w7" in result.stderr


def test_cli_stderr_names_why_the_card_is_runnable(cli_runnable_store):
    # Arrange
    argv = ["--agent", "worker-a", "--tasks", cli_runnable_store]
    # Act
    result = CliRunner().invoke(may_stop_cmd, argv)
    # Assert
    assert "in_progress card" in result.stderr


# EOF
