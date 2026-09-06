#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-instance flock guard on ``print-stats --notify``.

Third store-size daemon of the 2026-07-08 incident
(incident-cards-wake-watcher-interval2-spiral-20260708): the managed
``*/10`` notify cron (``print-stats --by agent --notify --nudge-quiet``)
re-derives per-agent rollups from the ~9 MB / ~930-card store. A run that
overruns the 10-min period OVERLAPS the next tick and runs STACK. The cure
is a NON-BLOCKING ``flock`` on the side-effecting notify path only — the
cron/one-shot analogue of the wake-watcher lock (#344) and the MCP inbox
drain guard (#345).

Real objects, NO mocks (STX-NM002): a real store, a REAL ``flock`` held by
the test, and — where a claim is about what the run DID NOT do — the command's
own output plus an UNREADABLE store.

The spies that used to stand in for those two claims are gone. Each could only
see calls routed through the one module attribute it was installed on, and this
file's own history is the argument against them: the 0.7.47 regression computed
the rollup ABOVE the guard while the push stayed serialized, so a push-only spy
reported everything fine. What the command PRINTS, and whether it can survive a
store it cannot read, are properties of the run rather than of one binding.

AAA structure.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from scitex_cards._cli._main import main
from scitex_cards._singleflight import notify_lock_path, single_instance
from scitex_cards._store import add_task


def _seed_store() -> None:
    """One owned card in the store ``load_tasks`` reads.

    The store itself is provisioned per-test by ``tests/conftest.py``; the
    seed goes through the real write API, so this test never has to say WHERE
    the store is (the CLI under test resolves it the same way).
    """
    add_task(id="t1", title="Task one", status="in_progress", agent="proj-x")


#: What ``deliver`` RETURNS for an agent with no configured turn URL. The CLI
#: echoes this token next to the agent name for every push it attempts, so its
#: presence in the output is direct evidence that ``deliver`` ran — and its
#: absence that it did not. That replaced a spy wrapping ``_push.deliver``: the
#: spy could only see calls routed through that one module attribute, while the
#: output reports what the command actually did.
_DELIVER_RESULT = "no-turn-url-configured"


def _run_over_an_unreadable_store(env, tmp_path, argv, *, hold_lock):
    """Run ``argv`` against a store that CANNOT be read, and return the result.

    THE UNREADABLE STORE IS THE INSTRUMENT, and it is what replaced the counter
    wrapped around ``load_tasks``. "Did this run parse the store" is not
    directly observable from the outside — but "could this run have survived
    WITHOUT parsing it" is: point the canonical database at a path that does
    not exist and the parse becomes fatal. A run that still exits 0 provably
    never reached it; a run that fails provably tried.

    That is strictly better than the counter it replaces. The counter answered
    only whether one particular module attribute was called, so a parse reached
    by any other binding was invisible to it — the exact blind spot that let
    the 0.7.47 regression (rollup computed ABOVE the guard) hide behind a
    push-only spy.
    """
    env.set("SCITEX_CARDS_DB", str(tmp_path / "absent" / "cards.db"))
    if not hold_lock:
        return CliRunner().invoke(main, argv)
    with single_instance(notify_lock_path(None)):
        return CliRunner().invoke(main, argv)


# --------------------------------------------------------------------------- #
# lock HELD -> --notify skips cleanly, does NO store parse / rollup / push      #
# --------------------------------------------------------------------------- #
#: WHY the six `lock_is_held` tests below are split but share this rationale:
#: a clean skip is a conjunction, and the pieces fail independently. The run
#: must exit 0 (a cron that errors on a normal overlap pages someone), SAY why
#: it skipped, print no push section, and — the CRITICAL 0.7.48 regression —
#: do NO deliver AND no store parse. The 0.7.47 bug computed the expensive
#: rollup ABOVE the guard, so `loads` would have been >= 1 while every other
#: claim here still passed: the push was serialized, so a push-only spy saw
#: nothing wrong. Only the store-parse claim catches it, which is exactly why
#: it must not sit behind five earlier asserts.
@pytest.fixture()
def notify_run_while_lock_held():
    """Run the cron path while a prior run's flock is still held."""
    _seed_store()
    with single_instance(notify_lock_path(None)) as acquired:
        result = CliRunner().invoke(main, ["print-stats", "--by", "agent", "--notify"])
    return {"acquired": acquired, "result": result}


def test_the_test_itself_acquires_the_notify_lock(notify_run_while_lock_held):
    # Arrange
    scenario = notify_run_while_lock_held
    # Act
    acquired = scenario["acquired"]
    # Assert — the premise of every sibling below: the lock really was held.
    assert acquired


def test_notify_skips_when_lock_is_held(notify_run_while_lock_held):
    # Arrange
    scenario = notify_run_while_lock_held
    # Act
    result = scenario["result"]
    # Assert — a clean skip, not an error.
    assert result.exit_code == 0, result.output


def test_notify_skip_names_the_prior_holder(notify_run_while_lock_held):
    # Arrange
    scenario = notify_run_while_lock_held
    # Act
    result = scenario["result"]
    # Assert — the skip explains itself.
    assert "a prior run still holds the lock" in result.output


def test_notify_skip_prints_no_push_section(notify_run_while_lock_held):
    # Arrange
    scenario = notify_run_while_lock_held
    # Act
    result = scenario["result"]
    # Assert
    assert "# Notify push" not in result.output


def test_notify_skip_never_delivers(notify_run_while_lock_held):
    # Arrange
    scenario = notify_run_while_lock_held
    # Act
    result = scenario["result"]
    # Assert — the CLI echoes deliver()'s return value next to every agent it
    # pushes to, so the token's absence is the run reporting no delivery.
    assert _DELIVER_RESULT not in result.output


def test_notify_skip_never_parses_the_store(env, tmp_path):
    """CRITICAL regression (0.7.48): the EXPENSIVE store parse / rollup must
    NOT run when the lock is held.

    The 0.7.47 bug computed the rollup ABOVE the guard. Here the store cannot
    be read at all, so a run that reaches the parse CANNOT exit 0 — surviving
    is the proof the guard sits above it.
    """
    # Arrange
    # Act
    result = _run_over_an_unreadable_store(
        env, tmp_path, ["print-stats", "--by", "agent", "--notify"], hold_lock=True
    )
    # Assert
    assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------- #
# lock FREE -> --notify runs the notify path                                   #
# --------------------------------------------------------------------------- #
#: WHY the four `lock_is_free` tests below are split but share this rationale:
#: the guard must not be a mute button. With no prior holder the run has to do
#: all the work it skipped above — exit 0, print the push section, actually
#: push for the agent, and actually parse the store. A guard that always skips
#: would pass the whole lock-held group and fail only here.
@pytest.fixture()
def notify_run_with_lock_free():
    """Run the cron path with no prior holder."""
    _seed_store()
    result = CliRunner().invoke(main, ["print-stats", "--by", "agent", "--notify"])
    return {"result": result}


def test_notify_runs_when_lock_is_free(notify_run_with_lock_free):
    # Arrange
    scenario = notify_run_with_lock_free
    # Act
    result = scenario["result"]
    # Assert
    assert result.exit_code == 0, result.output


def test_notify_run_prints_the_push_section(notify_run_with_lock_free):
    # Arrange
    scenario = notify_run_with_lock_free
    # Act
    result = scenario["result"]
    # Assert
    assert "# Notify push" in result.output


def test_notify_run_pushes_for_the_owning_agent(notify_run_with_lock_free):
    # Arrange
    scenario = notify_run_with_lock_free
    # Act
    result = scenario["result"]
    # Assert — the push line names the agent it pushed for.
    assert "proj-x" in result.output


def test_notify_run_parses_the_store(env, tmp_path):
    """The complement of the skip case, and the reason the guard is not just a
    mute button: with no prior holder the rollup MUST reach the store.

    An unreadable store makes that reach fatal, so failing here is the
    evidence it was attempted. Without this test the guard could sit above
    everything unconditionally and the whole lock-held group would still pass.
    """
    # Arrange
    # Act
    result = _run_over_an_unreadable_store(
        env, tmp_path, ["print-stats", "--by", "agent", "--notify"], hold_lock=False
    )
    # Assert
    assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# plain read is UNGUARDED — runs even while the lock is held                   #
# --------------------------------------------------------------------------- #
#: WHY the seven `plain_read` tests below are split but share this rationale:
#: the flock guards the SIDE-EFFECTING notify path only. An interactive read
#: must never be blocked or skipped by a cron's lock, so while the lock is
#: held a plain `print-stats` must still exit 0, print the table, parse the
#: store — and at the same time show none of the notify path's output and
#: perform no push. Scoping a lock too widely is the classic over-fix, and it
#: shows up as exactly one of these claims flipping.
@pytest.fixture()
def plain_read_while_lock_held():
    """Hold the notify lock, then run a PLAIN print-stats (no --notify)."""
    _seed_store()
    with single_instance(notify_lock_path(None)) as acquired:
        result = CliRunner().invoke(main, ["print-stats", "--by", "agent"])
    return {"acquired": acquired, "result": result}


def test_plain_read_scenario_really_holds_the_lock(plain_read_while_lock_held):
    # Arrange
    scenario = plain_read_while_lock_held
    # Act
    acquired = scenario["acquired"]
    # Assert — the premise of every sibling below.
    assert acquired


def test_plain_read_is_not_guarded_by_the_lock(plain_read_while_lock_held):
    # Arrange
    scenario = plain_read_while_lock_held
    # Act
    result = scenario["result"]
    # Assert — the read ran to completion despite the held notify lock.
    assert result.exit_code == 0, result.output


def test_plain_read_prints_the_agent_table(plain_read_while_lock_held):
    # Arrange
    scenario = plain_read_while_lock_held
    # Act
    result = scenario["result"]
    # Assert
    assert "proj-x" in result.output


def test_plain_read_prints_no_push_section(plain_read_while_lock_held):
    # Arrange
    scenario = plain_read_while_lock_held
    # Act
    result = scenario["result"]
    # Assert
    assert "# Notify push" not in result.output


def test_plain_read_prints_no_skip_line(plain_read_while_lock_held):
    # Arrange
    scenario = plain_read_while_lock_held
    # Act
    result = scenario["result"]
    # Assert — it was never a candidate for skipping in the first place.
    assert "a prior run still holds the lock" not in result.output


def test_plain_read_never_delivers(plain_read_while_lock_held):
    # Arrange
    scenario = plain_read_while_lock_held
    # Act
    result = scenario["result"]
    # Assert — a read is a read: no push is attempted, so deliver's return
    # token never appears.
    assert _DELIVER_RESULT not in result.output


def test_plain_read_still_parses_the_store(env, tmp_path):
    """The plain read is UNGUARDED: it parses the store even while the notify
    lock is held, because an interactive read must never be blocked or skipped
    by a cron's lock.

    Scoping the lock too widely is the classic over-fix; with an unreadable
    store, a plain read that had been swept under the guard would exit 0 here
    instead of failing.
    """
    # Arrange
    # Act
    result = _run_over_an_unreadable_store(
        env, tmp_path, ["print-stats", "--by", "agent"], hold_lock=True
    )
    # Assert
    assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# the lock is released after the run                                          #
# --------------------------------------------------------------------------- #
#: WHY the six `lock_is_released` tests below are split but share this
#: rationale: a lock that is acquired but never released turns the guard into
#: a permanent mute after the first run — worse than no guard at all. Release
#: is proven three ways over one scenario: the first run works, a SECOND
#: --notify acquires cleanly (runs the push, prints no skip line), and the
#: test can itself take the flock afterwards.
@pytest.fixture()
def two_notify_runs_then_a_manual_lock():
    """Run --notify twice, then try to take the flock from the test itself."""
    _seed_store()
    first = CliRunner().invoke(main, ["print-stats", "--by", "agent", "--notify"])
    second = CliRunner().invoke(main, ["print-stats", "--by", "agent", "--notify"])
    with single_instance(notify_lock_path(None)) as acquired:
        pass
    return {"first": first, "second": second, "acquired": acquired}


def test_first_notify_run_exits_cleanly(two_notify_runs_then_a_manual_lock):
    # Arrange
    scenario = two_notify_runs_then_a_manual_lock
    # Act
    first = scenario["first"]
    # Assert
    assert first.exit_code == 0, first.output


def test_first_notify_run_prints_the_push_section(
    two_notify_runs_then_a_manual_lock,
):
    # Arrange
    scenario = two_notify_runs_then_a_manual_lock
    # Act
    first = scenario["first"]
    # Assert
    assert "# Notify push" in first.output


def test_second_notify_run_exits_cleanly(two_notify_runs_then_a_manual_lock):
    # Arrange
    scenario = two_notify_runs_then_a_manual_lock
    # Act
    second = scenario["second"]
    # Assert — it acquired the lock the first run released.
    assert second.exit_code == 0, second.output


def test_second_notify_run_prints_the_push_section(
    two_notify_runs_then_a_manual_lock,
):
    # Arrange
    scenario = two_notify_runs_then_a_manual_lock
    # Act
    second = scenario["second"]
    # Assert — it really ran the notify path, it did not merely exit 0.
    assert "# Notify push" in second.output


def test_second_notify_run_prints_no_skip_line(two_notify_runs_then_a_manual_lock):
    # Arrange
    scenario = two_notify_runs_then_a_manual_lock
    # Act
    second = scenario["second"]
    # Assert
    assert "a prior run still holds the lock" not in second.output


def test_lock_is_released_after_notify_run(two_notify_runs_then_a_manual_lock):
    # Arrange
    scenario = two_notify_runs_then_a_manual_lock
    # Act
    acquired = scenario["acquired"]
    # Assert — the test can take the flock now that the runs released it.
    assert acquired
