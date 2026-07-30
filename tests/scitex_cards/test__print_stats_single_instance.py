#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-instance flock guard on ``print-stats --notify``.

Third store-size daemon of the 2026-07-08 incident
(incident-todo-wake-watcher-interval2-spiral-20260708): the managed
``*/10`` notify cron (``print-stats --by agent --notify --nudge-quiet``)
re-derives per-agent rollups from the ~9 MB / ~930-card store. A run that
overruns the 10-min period OVERLAPS the next tick and runs STACK. The cure
is a NON-BLOCKING ``flock`` on the side-effecting notify path only — the
cron/one-shot analogue of the wake-watcher lock (#344) and the MCP inbox
drain guard (#345).

Real fakes, NO mocks (STX-NM): a real ``tmp_path`` YAML store, a REAL
``flock`` held by the test, and a plain call-counter SPY wrapping the real
``scitex_cards._push.deliver`` to prove whether the notify path was entered.
AAA structure.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

import scitex_cards._cli._stats as _stats
import scitex_cards._push as _push
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


def _deliver_spy(monkeypatch):
    """Install a call-counter that WRAPS the real ``deliver`` (no mock).

    Returns the mutable ``calls`` list so a test can assert whether the
    notify/push path was entered. ``proj-x`` has no configured turn URL, so
    the wrapped real ``deliver`` returns ``no-turn-url-configured`` WITHOUT
    any network I/O — the spy observes real behaviour, it does not fake it.
    """
    calls: list = []
    real = _push.deliver

    def spy(agent, body, **kwargs):
        calls.append(agent)
        return real(agent, body, **kwargs)

    monkeypatch.setattr(_push, "deliver", spy)
    return calls


def _load_tasks_spy(monkeypatch):
    """Install a call-counter that WRAPS the real store parse (``load_tasks``).

    This is the assertion the 0.7.47 test was MISSING. The bug was that the
    expensive per-agent rollup — which begins by parsing the ~9 MB store via
    ``load_tasks`` — ran ABOVE the flock guard, so two overlapping ``--notify``
    ticks BOTH parsed the store concurrently even though the push at the end
    was serialized. A spy on the PUSH cannot catch that (the push is skipped
    either way once the lock is held); only a spy on the STORE PARSE proves the
    expensive work did not run. Wraps the real ``_stats.load_tasks`` (the name
    ``_rollup`` calls) — no mock, real parse still happens when it is invoked.
    """
    loads: list = []
    real = _stats.load_tasks

    def spy(path):
        loads.append(str(path))
        return real(path)

    monkeypatch.setattr(_stats, "load_tasks", spy)
    return loads


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
def notify_run_while_lock_held(monkeypatch):
    """Run the cron path while a prior run's flock is still held."""
    _seed_store()
    calls = _deliver_spy(monkeypatch)
    loads = _load_tasks_spy(monkeypatch)
    with single_instance(notify_lock_path(None)) as acquired:
        result = CliRunner().invoke(main, ["print-stats", "--by", "agent", "--notify"])
    return {"acquired": acquired, "result": result, "calls": calls, "loads": loads}


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


def test_notify_skip_never_calls_deliver(notify_run_while_lock_held):
    # Arrange
    scenario = notify_run_while_lock_held
    # Act
    calls = scenario["calls"]
    # Assert — the spy proves deliver() was never called.
    assert calls == []


def test_notify_skip_never_parses_the_store(notify_run_while_lock_held):
    # Arrange
    scenario = notify_run_while_lock_held
    # Act
    loads = scenario["loads"]
    # Assert — CRITICAL regression (0.7.48): the EXPENSIVE store parse /
    # rollup must NOT run when the lock is held. The 0.7.47 bug computed the
    # rollup ABOVE the guard, so this would have been >= 1. The guard now
    # wraps the parse → ZERO.
    assert loads == []


# --------------------------------------------------------------------------- #
# lock FREE -> --notify runs the notify path                                   #
# --------------------------------------------------------------------------- #
#: WHY the four `lock_is_free` tests below are split but share this rationale:
#: the guard must not be a mute button. With no prior holder the run has to do
#: all the work it skipped above — exit 0, print the push section, actually
#: push for the agent, and actually parse the store. A guard that always skips
#: would pass the whole lock-held group and fail only here.
@pytest.fixture()
def notify_run_with_lock_free(monkeypatch):
    """Run the cron path with no prior holder."""
    _seed_store()
    calls = _deliver_spy(monkeypatch)
    loads = _load_tasks_spy(monkeypatch)

    result = CliRunner().invoke(main, ["print-stats", "--by", "agent", "--notify"])
    return {"result": result, "calls": calls, "loads": loads}


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
    calls = scenario["calls"]
    # Assert
    assert "proj-x" in calls


def test_notify_run_parses_the_store(notify_run_with_lock_free):
    # Arrange
    scenario = notify_run_with_lock_free
    # Act
    loads = scenario["loads"]
    # Assert — the rollup DID parse the store (the lock was free).
    assert loads != []


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
def plain_read_while_lock_held(monkeypatch):
    """Hold the notify lock, then run a PLAIN print-stats (no --notify)."""
    _seed_store()
    calls = _deliver_spy(monkeypatch)
    loads = _load_tasks_spy(monkeypatch)

    with single_instance(notify_lock_path(None)) as acquired:
        result = CliRunner().invoke(main, ["print-stats", "--by", "agent"])
    return {"acquired": acquired, "result": result, "calls": calls, "loads": loads}


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


def test_plain_read_never_calls_deliver(plain_read_while_lock_held):
    # Arrange
    scenario = plain_read_while_lock_held
    # Act
    calls = scenario["calls"]
    # Assert
    assert calls == []


def test_plain_read_still_parses_the_store(plain_read_while_lock_held):
    # Arrange
    scenario = plain_read_while_lock_held
    # Act
    loads = scenario["loads"]
    # Assert — the plain read is UNGUARDED: it parses the store even while the
    # notify lock is held (interactive reads must never be blocked/skipped).
    assert loads != []


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
def two_notify_runs_then_a_manual_lock(monkeypatch):
    """Run --notify twice, then try to take the flock from the test itself."""
    _seed_store()
    _deliver_spy(monkeypatch)

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
