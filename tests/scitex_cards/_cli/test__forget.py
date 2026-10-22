#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `forget` CLI verb (CliRunner; no mocks).

The forgetting sweep's terminal twin: dry-run REPORT by default, `--apply`
performs the mutation (mirrors scitex-dev's board-mutation pattern and the
`reconcile-merged-prs` verb — no silent auto-close).

CliRunner against the ambient per-test store. Store-touching tests request
the ``postgres_dsn`` fail-LOUD fixture (bare-param pattern): every seed
below targets the ambient per-test store, which is refused without a
writable cluster — so these run in CI, not on a host pointing at the live
board.

AAA pattern + one assertion per test (TQ002 / TQ007).
"""

from __future__ import annotations

import datetime
import json

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._store import add_task


def _store_path() -> str:
    # Same rule as test__close.py: the store is the database; load/save
    # read+write it and IGNORE any path except as the provenance stamp.
    import os

    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def _seed_old(task_id="old-deferred", status="deferred", days=30):
    now = datetime.datetime.now(datetime.timezone.utc)
    stamped = (now - datetime.timedelta(days=days)).replace(microsecond=0)
    add_task(
        id=task_id,
        title="a long stale card for the forgetting sweep",
        status=status,
        assignee="agent:test-suite",
        last_activity=stamped.isoformat(),
    )


def _seed_fresh(task_id="recent-deferred"):
    _seed_old(task_id=task_id, status="deferred", days=1)


# === dry-run is the default =================================================


def test_verb_is_registered():
    # Arrange
    group = main
    # Act
    names = group.commands
    # Assert
    assert "forget" in names


def test_forget_dry_run_by_default_exits_zero(postgres_dsn):
    # Arrange
    _seed_old()
    runner = CliRunner()
    # Act
    res = runner.invoke(main, ["forget", "--days", "3"])
    # Assert
    assert res.exit_code == 0


def test_forget_dry_run_names_the_candidate(postgres_dsn):
    # Arrange
    _seed_old()
    runner = CliRunner()
    # Act
    res = runner.invoke(main, ["forget", "--days", "3"])
    # Assert
    assert "old-deferred" in res.output


def test_forget_dry_run_leaves_the_card_open(postgres_dsn):
    # Arrange
    from scitex_cards import _model

    _seed_old()
    runner = CliRunner()
    # Act
    runner.invoke(main, ["forget", "--days", "3"])
    # Assert
    assert _model.load_tasks(_store_path())[0]["status"] == "deferred"


def test_forget_dry_run_reports_empty_json(postgres_dsn):
    # Arrange
    _seed_fresh()
    runner = CliRunner()
    # Act
    res = runner.invoke(main, ["forget", "--days", "3", "--json"])
    # Assert
    assert json.loads(res.output)["would_close"] == []


# === --apply mutates ========================================================


def test_forget_apply_closes_the_stale_card(postgres_dsn):
    # Arrange
    from scitex_cards import _model

    _seed_old()
    runner = CliRunner()
    # Act
    runner.invoke(main, ["forget", "--days", "3", "--apply", "--yes"])
    # Assert
    assert _model.load_tasks(_store_path())[0]["status"] == "cancelled"


def test_forget_apply_stamps_the_close_reason(postgres_dsn):
    # Arrange
    from scitex_cards import _model

    _seed_old()
    runner = CliRunner()
    # Act
    runner.invoke(main, ["forget", "--days", "3", "--apply", "--yes"])
    # Assert
    texts = [c["text"] for c in _model.load_tasks(_store_path())[0]["comments"]]
    assert any(t.startswith("[CLOSED]") for t in texts)


def test_forget_apply_leaves_live_work_open(postgres_dsn):
    # Arrange
    from scitex_cards import _model

    _seed_old(task_id="ancient-active", status="in_progress", days=365)
    runner = CliRunner()
    # Act
    runner.invoke(main, ["forget", "--days", "3", "--apply", "--yes"])
    # Assert
    assert _model.load_tasks(_store_path())[0]["status"] == "in_progress"


# === the OFF switch =========================================================


def test_forget_none_days_reports_disabled(postgres_dsn):
    # Arrange
    _seed_old()
    runner = CliRunner()
    # Act
    res = runner.invoke(main, ["forget", "--days", "none"])
    # Assert
    assert "disabled" in res.output.lower()


def test_forget_env_none_disables_the_sweep(postgres_dsn, monkeypatch):
    # Arrange
    _seed_old()
    monkeypatch.setenv("SCITEX_CARDS_FORGET_DAYS", "none")
    runner = CliRunner()
    # Act
    res = runner.invoke(main, ["forget"])
    # Assert
    assert "disabled" in res.output.lower()


# EOF
