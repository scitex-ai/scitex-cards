#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the forgetting sweep's pure decision core (no store, no mocks).

The sweep closes stale blocked/deferred/goal cards whose ``last_activity``
is older than the configured horizon — because triaging them by hand costs
more than recreating them on demand. The OFF switch is the horizon itself:
``None`` (config ``null``, env ``none``/empty) disables the sweep entirely.

AAA pattern + one assertion per test (TQ002 / TQ007).
"""

from __future__ import annotations

import datetime

from scitex_cards._forget import (
    FORGET_SECTION,
    ForgetConfig,
    decide_forget_action,
    load_forget_config,
    select_forget_candidates,
)


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _task(task_id, status, age_days=None, last_activity=None):
    task = {"id": task_id, "status": status}
    if last_activity is not None:
        task["last_activity"] = last_activity
    elif age_days is not None:
        task["last_activity"] = _iso(_now() - datetime.timedelta(days=age_days))
    return task


# === horizon resolution =====================================================


def test_default_horizon_is_three_days():
    # Arrange
    # Act
    cfg = ForgetConfig()
    # Assert
    assert cfg.days == 3


def test_config_null_disables_the_sweep():
    # Arrange
    raw = {FORGET_SECTION: {"days": None}}
    # Act
    cfg = load_forget_config(cfg=raw)
    # Assert
    assert cfg.enabled is False


def test_config_none_string_disables_the_sweep():
    # Arrange
    raw = {FORGET_SECTION: {"days": "none"}}
    # Act
    cfg = load_forget_config(cfg=raw)
    # Assert
    assert cfg.enabled is False


def test_config_numeric_days_set_the_horizon():
    # Arrange
    raw = {FORGET_SECTION: {"days": 7}}
    # Act
    cfg = load_forget_config(cfg=raw)
    # Assert
    assert cfg.days == 7


def test_explicit_days_beat_the_config_file():
    # Arrange
    raw = {FORGET_SECTION: {"days": 7}}
    # Act
    cfg = load_forget_config(explicit_days=14, cfg=raw)
    # Assert
    assert cfg.days == 14


def test_explicit_none_disables_despite_config_file():
    # Arrange
    raw = {FORGET_SECTION: {"days": 7}}
    # Act
    cfg = load_forget_config(explicit_days=None, cfg=raw)
    # Assert
    assert cfg.enabled is False


def test_env_none_disables_the_sweep(env):
    # Arrange
    env.set("SCITEX_CARDS_FORGET_DAYS", "none")
    # Act
    cfg = load_forget_config(cfg={})
    # Assert
    assert cfg.enabled is False


def test_env_numeric_sets_the_horizon(env):
    # Arrange
    env.set("SCITEX_CARDS_FORGET_DAYS", "10")
    # Act
    cfg = load_forget_config(cfg={})
    # Assert
    assert cfg.days == 10


def test_env_beats_the_config_file(env):
    # Arrange
    env.set("SCITEX_CARDS_FORGET_DAYS", "10")
    raw = {FORGET_SECTION: {"days": 7}}
    # Act
    cfg = load_forget_config(cfg=raw)
    # Assert
    assert cfg.days == 10


# === candidate selection ====================================================


def test_stale_deferred_card_is_a_candidate():
    # Arrange
    tasks = [_task("old-deferred", "deferred", age_days=30)]
    # Act
    selected = select_forget_candidates(tasks, ForgetConfig(days=3))
    # Assert
    assert [t["id"] for t in selected] == ["old-deferred"]


def test_fresh_deferred_card_is_not_a_candidate():
    # Arrange
    tasks = [_task("recent-deferred", "deferred", age_days=1)]
    # Act
    selected = select_forget_candidates(tasks, ForgetConfig(days=3))
    # Assert
    assert selected == []


def test_stale_blocked_card_is_a_candidate():
    # Arrange
    tasks = [_task("old-blocked", "blocked", age_days=30)]
    # Act
    selected = select_forget_candidates(tasks, ForgetConfig(days=3))
    # Assert
    assert [t["id"] for t in selected] == ["old-blocked"]


def test_in_progress_card_is_never_a_candidate():
    # Arrange — untouched for a year, still somebodies live work.
    tasks = [_task("ancient-active", "in_progress", age_days=365)]
    # Act
    selected = select_forget_candidates(tasks, ForgetConfig(days=3))
    # Assert
    assert selected == []


def test_done_card_is_never_a_candidate():
    # Arrange
    tasks = [_task("old-done", "done", age_days=365)]
    # Act
    selected = select_forget_candidates(tasks, ForgetConfig(days=3))
    # Assert
    assert selected == []


def test_cancelled_card_is_never_a_candidate():
    # Arrange
    tasks = [_task("old-cancelled", "cancelled", age_days=365)]
    # Act
    selected = select_forget_candidates(tasks, ForgetConfig(days=3))
    # Assert
    assert selected == []


def test_card_at_the_boundary_stays():
    # Arrange — exactly on the horizon: strictly-older wins, no fencepost kill.
    tasks = [_task("edge", "deferred", age_days=3)]
    # Act
    selected = select_forget_candidates(tasks, ForgetConfig(days=3))
    # Assert
    assert selected == []


def test_card_without_timestamps_is_not_a_candidate():
    # Arrange — no created_at, no last_activity: unknown age, do not guess.
    tasks = [{"id": "undated", "status": "deferred"}]
    # Act
    selected = select_forget_candidates(tasks, ForgetConfig(days=3))
    # Assert
    assert selected == []


def test_disabled_config_selects_nothing():
    # Arrange
    tasks = [_task("old-deferred", "deferred", age_days=365)]
    # Act
    selected = select_forget_candidates(tasks, ForgetConfig(days=None))
    # Assert
    assert selected == []


# === per-card decision ======================================================


def test_decide_forget_action_names_the_close():
    # Arrange
    task = _task("old-deferred", "deferred", age_days=30)
    # Act
    action = decide_forget_action(task, ForgetConfig(days=3))
    # Assert
    assert action == "close"


def test_decide_forget_action_keeps_fresh_cards():
    # Arrange
    task = _task("recent-deferred", "deferred", age_days=1)
    # Act
    action = decide_forget_action(task, ForgetConfig(days=3))
    # Assert
    assert action == "keep"


def test_decide_forget_action_keeps_live_work():
    # Arrange
    task = _task("ancient-active", "in_progress", age_days=365)
    # Act
    action = decide_forget_action(task, ForgetConfig(days=3))
    # Assert
    assert action == "keep"


def test_decide_forget_action_is_off_when_disabled():
    # Arrange
    task = _task("old-deferred", "deferred", age_days=365)
    # Act
    action = decide_forget_action(task, ForgetConfig(days=None))
    # Assert
    assert action == "keep"


# EOF
