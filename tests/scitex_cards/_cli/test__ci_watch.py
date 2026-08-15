#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-todo watch-ci` — record-only CI poller (renamed from `ci-watch`).

Lead a2a (operator decoupled-pollers override, dev msg `96afacc7`,
2026-06-15). Tests the pure-function transition classifier, the
state-cache load/save round-trip, and the CLI plumbing (env override,
exit codes, dry-run).

No mocks (STX-NM/PA-306) on the production logic — the transition
classifier is a pure function over two dicts; state load/save uses
real tmp_path files. The CLI-level sweep tests inject the fake entry
points via `env` on the iterator (same fault-injection pattern
as PR #196 ordering tests), NOT mocks of the code under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._ci_watch import (
    classify_transition,
    load_state,
    save_state,
    state_path,
)


# === classify_transition (pure function) ==================================


def test_classify_first_seen_when_no_prior():
    # Arrange
    # Act
    label = classify_transition(None, {"head_sha": "abc", "overall": "success"})
    # Assert
    assert label == "first-seen"


def test_classify_unchanged_when_head_and_overall_match():
    # Arrange
    prior = {"head_sha": "abc", "overall": "success"}
    current = {"head_sha": "abc", "overall": "success"}
    # Act
    label = classify_transition(prior, current)
    # Assert
    assert label == "unchanged"


def test_classify_newly_green_failure_to_success():
    # Arrange
    prior = {"head_sha": "old", "overall": "failure"}
    current = {"head_sha": "new", "overall": "success"}
    # Act
    label = classify_transition(prior, current)
    # Assert
    assert label == "newly-green"


def test_classify_newly_red_success_to_failure():
    # Arrange
    prior = {"head_sha": "old", "overall": "success"}
    current = {"head_sha": "new", "overall": "failure"}
    # Act
    label = classify_transition(prior, current)
    # Assert
    assert label == "newly-red"


def test_classify_newly_green_from_pending():
    # Arrange — pending → green is a verdict landing.
    prior = {"head_sha": "old", "overall": "pending"}
    current = {"head_sha": "new", "overall": "success"}
    # Act
    label = classify_transition(prior, current)
    # Assert
    assert label == "newly-green"


def test_classify_still_pending_when_neither_definitive():
    # Arrange — both unknown/pending.
    prior = {"head_sha": "a", "overall": "pending"}
    current = {"head_sha": "b", "overall": "unknown"}
    # Act
    label = classify_transition(prior, current)
    # Assert
    assert label == "still-pending"


def test_classify_missing_overall_treated_as_unknown():
    # Arrange
    # Act
    label = classify_transition({}, {})
    # Assert
    assert label == "still-pending"


# === load_state / save_state round-trip ====================================


def test_load_state_empty_when_path_absent(tmp_path: Path):
    # Arrange
    p = tmp_path / "missing.json"
    # Act
    out = load_state(p)
    # Assert
    assert out == {}


def test_load_state_empty_on_corrupt_json(tmp_path: Path):
    # Arrange — operator-friendly: corrupt cache is treated as missing
    # so the cron doesn't crash; the next sweep rebuilds it.
    p = tmp_path / "ci-state.json"
    p.write_text("{ not valid", encoding="utf-8")
    # Act
    out = load_state(p)
    # Assert
    assert out == {}


def test_save_state_round_trip(tmp_path: Path):
    # Arrange
    p = tmp_path / "ci-state.json"
    state = {"owner/repo": {"head_sha": "abc", "overall": "success"}}
    # Act
    save_state(state, p)
    loaded = load_state(p)
    # Assert
    assert loaded == state


def test_save_state_is_atomic_tmp_then_replace(tmp_path: Path):
    # Arrange — after save, the .tmp sidecar must NOT exist (rename
    # removed it). The atomic write pattern means a SIGKILL mid-save
    # leaves either the old file OR the new file, never a half-written
    # canonical file.
    p = tmp_path / "ci-state.json"
    save_state({"owner/repo": {"head_sha": "x"}}, p)
    # Act
    tmp_sidecar = p.with_suffix(p.suffix + ".tmp")
    # Assert
    assert not tmp_sidecar.exists()


def test_state_path_honors_env_override(env, tmp_path: Path):
    # Arrange
    override = tmp_path / "custom-ci-state.json"
    env.set("SCITEX_TODO_CI_STATE", str(override))
    # Act
    p = state_path()
    # Assert
    assert p == override


# === CLI: --dry-run path ===================================================


def test_ci_watch_dry_run_with_no_repos_configured(tmp_path: Path, env):
    # Arrange — point the state cache at tmp, leave the FE config
    # empty (no SCITEX_TODO_FLEET_CI_REPOS, no dashboard.yaml under HOME).
    env.set("SCITEX_TODO_CI_STATE", str(tmp_path / "ci-state.json"))
    # Force a hermetic HOME so the test doesn't pick up the operator's
    # actual ~/.scitex/todo/dashboard.yaml.
    env.set("HOME", str(tmp_path))
    env.delete("SCITEX_TODO_FLEET_CI_REPOS")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["watch-ci", "--once", "--dry-run"])
    # Assert — empty config + dry-run → exit 0, summary line carries
    # `repos=0`.
    assert result.exit_code == 0


def test_ci_watch_dry_run_summary_line_present(tmp_path: Path, env):
    # Arrange
    env.set("SCITEX_TODO_CI_STATE", str(tmp_path / "ci-state.json"))
    env.set("HOME", str(tmp_path))
    env.delete("SCITEX_TODO_FLEET_CI_REPOS")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["watch-ci", "--once", "--dry-run"])
    # Assert — the bottom summary line is the operator's at-a-glance
    # health check.
    assert "watch-ci: repos=0" in result.output


def test_ci_watch_dry_run_does_not_write_state(tmp_path: Path, env):
    # Arrange
    state_file = tmp_path / "ci-state.json"
    env.set("SCITEX_TODO_CI_STATE", str(state_file))
    env.set("HOME", str(tmp_path))
    env.delete("SCITEX_TODO_FLEET_CI_REPOS")
    runner = CliRunner()
    # Act
    runner.invoke(main, ["watch-ci", "--once", "--dry-run"])
    # Assert
    assert not state_file.exists()


# === JobSpec — registered for ecosystem cron ==============================


def test_jobspec_provider_includes_ci_watch():
    # Arrange
    from scitex_cards._jobs_provider import provide_jobs

    jobs = provide_jobs()
    # Act
    names = [j.name for j in jobs]
    # Assert
    assert "scitex-cards-ci-watch" in names


def test_ci_watch_jobspec_runs_record_only_command():
    # the cron tick exits and the next one starts cleanly. NOT a loop
    # (the cron is the loop).
    # Arrange
    from scitex_cards._jobs_provider import provide_jobs

    jobs = provide_jobs()
    # Act
    spec = next(j for j in jobs if j.name == "scitex-cards-ci-watch")
    # Assert
    assert spec.command == "scitex-cards watch-ci --once"


def test_ci_watch_jobspec_is_5_min_cron():
    # Arrange
    from scitex_cards._jobs_provider import provide_jobs

    jobs = provide_jobs()
    # Act
    spec = next(j for j in jobs if j.name == "scitex-cards-ci-watch")
    # Assert
    assert spec.schedule == "*/5 * * * *"
