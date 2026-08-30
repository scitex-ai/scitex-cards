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

from click.testing import CliRunner

from scitex_cards._cli._stop_hook import stop_hook_cmd
from scitex_cards._inbox import poll_inbox
from scitex_cards._store import add_task


def _run(agent="worker-x"):
    result = CliRunner().invoke(stop_hook_cmd, ["--agent", agent])
    return result, json.loads(result.stdout)


def _drain(agent):
    """Ack the created-events add_task enqueues, so they don't count as work."""
    poll_inbox(agent, unseen_only=True, mark_seen=True)


def _seed_runnable_card(card_id="w1", agent="worker-x"):
    """One runnable card owned by ``agent``, with its created-event drained."""
    add_task(id=card_id, title=card_id, status="in_progress", agent=agent)
    _drain(agent)


def _seed_many_runnable_cards(count=12, agent="worker-x"):
    """``count`` runnable cards owned by ``agent``, created-events drained."""
    for i in range(count):
        add_task(
            id=f"c{i}",
            title=f"c{i}",
            status="in_progress",
            agent=agent,
        )
    _drain(agent)


def _run_against_unreadable_store():
    """Invoke the hook against a store that cannot be read at all.

    The store is database-only now and the ``--tasks`` CLI option is gone, so a
    detector failure is simulated faithfully by pointing the resolved database
    env at a path that cannot even be created: the read raises ``RuntimeError``
    and the hook must fail open. (The old form named a missing ``tasks.yaml``
    via ``--tasks`` for the same effect.)

    The path changed 2026-07-29. It used to be ``/nonexistent/scitex-cards/
    none.db``, which was MEASURED not to raise at all — it reads as an EMPTY
    BOARD, so both tests below passed for the wrong reason and proved nothing
    about fail-open. A fail-open test whose arrange step does not fail is
    vacuous.
    """
    return CliRunner().invoke(
        stop_hook_cmd,
        ["--agent", "worker-x"],
        env={"SCITEX_CARDS_DB": "/proc/1/definitely-not-a-directory/cards.db"},
    )


def test_an_empty_board_allows_the_stop():
    # Arrange — the per-test store holds no cards.
    # Act
    _result, payload = _run()
    # Assert — {} is "allow"; anything else would wedge an idle agent.
    assert payload == {}


def test_an_empty_board_exits_zero():
    # Arrange — the per-test store holds no cards.
    # Act
    result, _payload = _run()
    # Assert
    assert result.exit_code == 0


def test_runnable_work_blocks_the_stop():
    # Arrange
    _seed_runnable_card()
    # Act
    _result, payload = _run()
    # Assert
    assert payload["decision"] == "block"


def test_a_refused_stop_still_exits_zero():
    # Arrange
    _seed_runnable_card()
    # Act
    result, _payload = _run()
    # Assert — the hook succeeded; it is the STOP that was refused.
    assert result.exit_code == 0


def test_the_reason_names_the_blocking_card():
    """A refusal that does not name the card leaves the agent guessing."""
    # Arrange
    _seed_runnable_card()
    # Act
    _, payload = _run()
    reason = payload["reason"]
    # Assert
    assert "w1" in reason


def test_the_reason_says_what_to_do_next():
    """A refusal that does not say what to do next leaves the agent idle."""
    # Arrange
    _seed_runnable_card()
    # Act
    _, payload = _run()
    reason = payload["reason"]
    # Assert
    assert "work it, update it, or close it" in reason


def test_the_reason_instructs_a_single_item():
    """The instruction is explicitly single-item, not a whole backlog."""
    # Arrange
    _seed_runnable_card()
    # Act
    _, payload = _run()
    reason = payload["reason"]
    # Assert
    assert "Pick ONE" in reason


def test_a_detector_failure_allows_the_stop():
    """FAIL-OPEN. Our bug must never be the reason an agent cannot finish."""
    # Arrange — an unreadable store stands in for a detector failure.
    # Act
    result = _run_against_unreadable_store()
    # Assert
    assert json.loads(result.stdout) == {}


def test_a_detector_failure_still_exits_zero():
    """FAIL-OPEN. The hook itself must not report an error status."""
    # Arrange — an unreadable store stands in for a detector failure.
    # Act
    result = _run_against_unreadable_store()
    # Assert
    assert result.exit_code == 0


def test_another_agents_work_does_not_block_this_agent():
    # Arrange
    _seed_runnable_card(card_id="w2", agent="worker-y")
    # Act
    _, payload = _run(agent="worker-x")
    # Assert
    assert payload == {}


def test_the_reason_caps_the_listed_cards():
    """An instruction listing forty cards is not an instruction."""
    # Arrange
    _seed_many_runnable_cards(count=12)
    # Act
    _, payload = _run()
    reason = payload["reason"]
    numbered = [
        ln for ln in reason.splitlines() if ln.strip()[:2].rstrip(".").isdigit()
    ]
    # Assert
    assert len(numbered) <= 6


def test_the_reason_flags_the_capped_remainder():
    """The remainder is COUNTED rather than dropped silently."""
    # Arrange
    _seed_many_runnable_cards(count=12)
    # Act
    _, payload = _run()
    reason = payload["reason"]
    # Assert
    assert "more runnable item(s)" in reason


def test_the_reason_reports_the_full_runnable_total():
    """An omission the agent cannot see is a lie about the board."""
    # Arrange
    _seed_many_runnable_cards(count=12)
    # Act
    _, payload = _run()
    reason = payload["reason"]
    # Assert
    assert "12 runnable item(s)" in reason


# EOF
