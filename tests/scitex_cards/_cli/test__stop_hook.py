#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards stop-hook`` — refuses a stop while the board holds work.

The cards half of the never-stop-when-task-remains mechanism (ADR-0012). The
runtime registers this into ``.claude/settings.json`` and knows nothing about
its output; cards owns both ends of the contract, so the format below is the
whole public surface.

CONTRACT: stdout is Claude Code Stop-hook JSON.
``{"decision": "block", "reason": ...}`` refuses the stop and feeds ``reason``
back as the agent's NEXT INSTRUCTION. ``{}`` allows it.

Pinned here, with real store files and no mocks:
* work remains → block, and the reason NAMES the cards and says what to do;
* board empty → allow;
* detector failure → ALLOW (fail-open), because an agent wedged by our own bug
  is worse than one that stopped early;
* another agent's work does not block this agent;
* the reason stays bounded when the board is large — an instruction listing
  forty cards is not an instruction.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli._stop_hook import stop_hook_cmd
from scitex_cards._inbox import poll_inbox
from scitex_cards._store import add_task


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return str(path)


def _run(store, agent="worker-x"):
    result = CliRunner().invoke(stop_hook_cmd, ["--agent", agent, "--tasks", store])
    return result, json.loads(result.stdout)


def _drain(store, agent):
    """Ack the created-events add_task enqueues, so they don't count as work."""
    poll_inbox(agent, unseen_only=True, mark_seen=True, store=store)


def _seed_runnable_card(store, card_id="w1", agent="worker-x"):
    """One runnable card owned by ``agent``, with its created-event drained."""
    add_task(store=store, id=card_id, title=card_id, status="in_progress", agent=agent)
    _drain(store, agent)


def _seed_many_runnable_cards(store, count=12, agent="worker-x"):
    """``count`` runnable cards owned by ``agent``, created-events drained."""
    for i in range(count):
        add_task(
            store=store,
            id=f"c{i}",
            title=f"c{i}",
            status="in_progress",
            agent=agent,
        )
    _drain(store, agent)


def _run_against_unreadable_store():
    """Invoke the hook against a store path that cannot be read at all."""
    return CliRunner().invoke(
        stop_hook_cmd, ["--agent", "worker-x", "--tasks", "/nonexistent/none.yaml"]
    )


def test_an_empty_board_allows_the_stop(store):
    # Arrange — the fixture store holds no cards.
    board = store
    # Act
    _result, payload = _run(board)
    # Assert — {} is "allow"; anything else would wedge an idle agent.
    assert payload == {}


def test_an_empty_board_exits_zero(store):
    # Arrange — the fixture store holds no cards.
    board = store
    # Act
    result, _payload = _run(board)
    # Assert
    assert result.exit_code == 0


def test_runnable_work_blocks_the_stop(store):
    # Arrange
    _seed_runnable_card(store)
    # Act
    _result, payload = _run(store)
    # Assert
    assert payload["decision"] == "block"


def test_a_refused_stop_still_exits_zero(store):
    # Arrange
    _seed_runnable_card(store)
    # Act
    result, _payload = _run(store)
    # Assert — the hook succeeded; it is the STOP that was refused.
    assert result.exit_code == 0


def test_the_reason_names_the_blocking_card(store):
    """A refusal that does not name the card leaves the agent guessing."""
    # Arrange
    _seed_runnable_card(store)
    # Act
    _, payload = _run(store)
    reason = payload["reason"]
    # Assert
    assert "w1" in reason


def test_the_reason_says_what_to_do_next(store):
    """A refusal that does not say what to do next leaves the agent idle."""
    # Arrange
    _seed_runnable_card(store)
    # Act
    _, payload = _run(store)
    reason = payload["reason"]
    # Assert
    assert "work it, update it, or close it" in reason


def test_the_reason_instructs_a_single_item(store):
    """The instruction is explicitly single-item, not a whole backlog."""
    # Arrange
    _seed_runnable_card(store)
    # Act
    _, payload = _run(store)
    reason = payload["reason"]
    # Assert
    assert "Pick ONE" in reason


def test_a_detector_failure_allows_the_stop(store):
    """FAIL-OPEN. Our bug must never be the reason an agent cannot finish."""
    # Arrange — an unreadable store stands in for a detector failure.
    # Act
    result = _run_against_unreadable_store()
    # Assert
    assert json.loads(result.stdout) == {}


def test_a_detector_failure_still_exits_zero(store):
    """FAIL-OPEN. The hook itself must not report an error status."""
    # Arrange — an unreadable store stands in for a detector failure.
    # Act
    result = _run_against_unreadable_store()
    # Assert
    assert result.exit_code == 0


def test_another_agents_work_does_not_block_this_agent(store):
    # Arrange
    _seed_runnable_card(store, card_id="w2", agent="worker-y")
    # Act
    _, payload = _run(store, agent="worker-x")
    # Assert
    assert payload == {}


def test_the_reason_caps_the_listed_cards(store):
    """An instruction listing forty cards is not an instruction."""
    # Arrange
    _seed_many_runnable_cards(store, count=12)
    # Act
    _, payload = _run(store)
    reason = payload["reason"]
    numbered = [
        ln for ln in reason.splitlines() if ln.strip()[:2].rstrip(".").isdigit()
    ]
    # Assert
    assert len(numbered) <= 6


def test_the_reason_flags_the_capped_remainder(store):
    """The remainder is COUNTED rather than dropped silently."""
    # Arrange
    _seed_many_runnable_cards(store, count=12)
    # Act
    _, payload = _run(store)
    reason = payload["reason"]
    # Assert
    assert "more runnable item(s)" in reason


def test_the_reason_reports_the_full_runnable_total(store):
    """An omission the agent cannot see is a lie about the board."""
    # Arrange
    _seed_many_runnable_cards(store, count=12)
    # Act
    _, payload = _run(store)
    reason = payload["reason"]
    # Assert
    assert "12 runnable item(s)" in reason


# EOF
