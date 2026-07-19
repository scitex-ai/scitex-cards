#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the notifyd sweeps (``scitex_cards._delivery._sweeps``).

The liveness sweep is the one signal that must NOT be ignorable, so it now has
a scheduled home inside notifyd. Two properties matter and are covered here
against REAL objects (no mocks): the sweep runs on its OWN low cadence (never
in the 60 s delivery path), and a raising sweep NEVER kills the delivery loop.

The raising case is driven with a REAL failure (a malformed store makes
``load_tasks`` raise inside the guard) plus a raising fake substituted for the
sweep function the loop calls — the loop, not the sweep, is under test there.

One assertion per test (STX-TQ007): the loop drivers below run the scenario
once per test and each test checks a single property of the result.
"""

from __future__ import annotations

import datetime as _dt

import yaml

from scitex_cards._delivery import _daemon
from scitex_cards._delivery._sweeps import (
    DEFAULT_NUDGE_SWEEP_MINUTES,
    ENV_NUDGE_SWEEP_MINUTES,
    _nudge_sweep_due,
    _nudge_sweep_minutes,
    _run_stale_nudge_sweep,
)
from scitex_cards._inbox import enqueue

from ._fakes import RecorderChannel

T0 = _dt.datetime(2026, 7, 11, 10, 0, 0, tzinfo=_dt.timezone.utc)


def _write_recipients(tmp_path, mapping: dict) -> None:
    (tmp_path / "recipients.yaml").write_text(
        yaml.safe_dump({"users": mapping}), encoding="utf-8"
    )


def _sweep_minutes_for_env_value(monkeypatch, value: str) -> float:
    """Resolve the cadence with ``ENV_NUDGE_SWEEP_MINUTES`` set to ``value``."""
    monkeypatch.setenv(ENV_NUDGE_SWEEP_MINUTES, value)
    return _nudge_sweep_minutes()


def _run_loop_with_raising_sweep(tmp_path, monkeypatch) -> dict:
    """Run notifyd for two ticks with a sweep that raises OUT of the guard.

    The LOOP is under test, not the sweep: the real sweep swallows its own
    errors, so a raising fake is substituted to prove the loop survives one.
    Returns the recorded sweep calls, the loop result and the channel.
    """
    store = tmp_path / "tasks.yaml"
    rec = enqueue(
        "u_alice",
        event_type="reassigned",
        card_id="c1",
        body="Card c1 reassigned to you",
        actor="bob",
        ts="2026-07-11T09:00:00Z",
        store=store,
    )
    if rec is None:
        raise AssertionError("enqueue returned no notification record")
    _write_recipients(tmp_path, {"u_alice": {"channels": [{"kind": "log"}]}})

    calls: list[int] = []

    def _boom(*, store, now):
        calls.append(1)
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(_daemon, "_run_stale_nudge_sweep", _boom)

    recorder = RecorderChannel(name="log")
    ticks = {"n": 0}

    def _now():
        ticks["n"] += 1
        return T0 + _dt.timedelta(hours=ticks["n"])

    result = _daemon.run_notifyd(
        store=store,
        interval=60.0,
        channels={"log": recorder},
        sleep=lambda _s: None,
        now_fn=_now,
        max_iterations=2,
        terminal_report_every=0,
        nudge_sweep_minutes=1.0,  # due every tick (the clock jumps 1 h)
    )
    return {"calls": calls, "result": result, "recorder": recorder}


def _run_loop_recording_sweep_calls(
    tmp_path, monkeypatch, *, now_fn, max_iterations, nudge_sweep_minutes
) -> list:
    """Run notifyd with a counting sweep; return the ``now`` of every call."""
    store = tmp_path / "tasks.yaml"
    _write_recipients(tmp_path, {})
    calls: list = []

    def _count(*, store, now):
        calls.append(now)

    monkeypatch.setattr(_daemon, "_run_stale_nudge_sweep", _count)

    _daemon.run_notifyd(
        store=store,
        interval=60.0,
        channels={},
        sleep=lambda _s: None,
        now_fn=now_fn,
        max_iterations=max_iterations,
        terminal_report_every=0,
        nudge_sweep_minutes=nudge_sweep_minutes,
    )
    return calls


class TestCadence:
    def test_never_run_is_due(self):
        # Arrange
        last_run = None
        # Act
        due = _nudge_sweep_due(last_run, T0, minutes=30.0)
        # Assert
        assert due

    def test_not_due_before_the_cadence_elapses(self):
        # Arrange
        now = T0 + _dt.timedelta(minutes=29)
        # Act
        due = _nudge_sweep_due(T0, now, minutes=30.0)
        # Assert
        assert not due

    def test_due_once_the_cadence_elapses(self):
        # Arrange
        now = T0 + _dt.timedelta(minutes=30)
        # Act
        due = _nudge_sweep_due(T0, now, minutes=30.0)
        # Assert
        assert due

    def test_non_positive_cadence_disables_the_sweep(self):
        # Arrange
        last_run = None
        # Act
        due = _nudge_sweep_due(last_run, T0, minutes=0.0)
        # Assert
        assert not due

    def test_numeric_env_value_overrides_the_cadence(self, monkeypatch):
        # Arrange
        # Act
        minutes = _sweep_minutes_for_env_value(monkeypatch, "45")
        # Assert
        assert minutes == 45.0

    def test_unparseable_env_value_falls_back_to_the_default(self, monkeypatch):
        # Arrange
        # Act
        minutes = _sweep_minutes_for_env_value(monkeypatch, "not-a-number")
        # Assert
        assert minutes == DEFAULT_NUDGE_SWEEP_MINUTES


class TestSweepIsFailSoft:
    def test_malformed_store_is_swallowed_by_the_guard(self, tmp_path):
        # Arrange
        # REAL failure: the store is not parseable, so load_tasks
        # raises inside the sweep. The guard must swallow it (the delivery
        # pass follows), returning normally instead of propagating.
        store = tmp_path / "tasks.yaml"
        store.write_text("{{{ not yaml", encoding="utf-8")
        # Act
        outcome = _run_stale_nudge_sweep(store=store, now=T0)
        # Assert
        # it returned rather than raising out of the loop's tick.
        assert outcome is None


class TestNotifydLoop:
    def test_raising_sweep_really_ran_during_the_loop(self, tmp_path, monkeypatch):
        # Arrange
        # Act
        run = _run_loop_with_raising_sweep(tmp_path, monkeypatch)
        # Assert
        assert run["calls"]

    def test_raising_sweep_does_not_cut_the_iteration_count(
        self, tmp_path, monkeypatch
    ):
        # Arrange
        # Act
        run = _run_loop_with_raising_sweep(tmp_path, monkeypatch)
        # Assert
        # both ticks completed despite the raise.
        assert run["result"]["iterations"] == 2

    def test_raising_sweep_does_not_stop_notifications_being_sent(
        self, tmp_path, monkeypatch
    ):
        # Arrange
        # Act
        run = _run_loop_with_raising_sweep(tmp_path, monkeypatch)
        # Assert
        assert run["result"]["totals"]["sent"] >= 1

    def test_raising_sweep_does_not_stop_the_channel_delivery(
        self, tmp_path, monkeypatch
    ):
        # Arrange
        # Act
        run = _run_loop_with_raising_sweep(tmp_path, monkeypatch)
        # Assert
        # detection dying must never stop delivery.
        assert [c["recipient"] for c in run["recorder"].calls] == ["u_alice"]

    def test_sweep_disabled_by_zero_cadence(self, tmp_path, monkeypatch):
        # Arrange
        # Act
        calls = _run_loop_recording_sweep_calls(
            tmp_path,
            monkeypatch,
            now_fn=lambda: T0,
            max_iterations=3,
            nudge_sweep_minutes=0.0,
        )
        # Assert
        assert calls == []

    def test_sweep_runs_once_per_cadence_not_every_tick(self, tmp_path, monkeypatch):
        # Arrange
        # each TICK advances 10 min (now_fn is called several times
        # a tick; the sweep sees a monotonically rising clock either way).
        ticks = {"n": 0}

        def _now():
            ticks["n"] += 1
            return T0 + _dt.timedelta(minutes=10 * ticks["n"])

        # Act
        calls = _run_loop_recording_sweep_calls(
            tmp_path,
            monkeypatch,
            now_fn=_now,
            max_iterations=4,
            nudge_sweep_minutes=600.0,
        )
        # Assert
        # the injected clock never advances past the 600 min cadence,
        # so the sweep fires ONCE across 4 delivery ticks: not in the hot path.
        assert len(calls) == 1


# EOF
