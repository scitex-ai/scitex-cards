#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the Phase-1 mutation/admin CLI verbs (CliRunner; no mocks).

Verbs covered:
    add / update / done / list / summary / where / init / sync (stub)
    mcp doctor / mcp install / mcp list-tools (fallback fastmcp-missing path)
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_cards import _model, _store
from scitex_cards._cli import main
from scitex_cards._paths import PKG_SHORT


def _store_path(tmp_path) -> str:
    """Path string to a fresh empty store under tmp_path/.scitex/cards/."""
    return str(tmp_path / "tasks.yaml")


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #
def test_add_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "design",
            "Design phase",
            "--scope",
            "agent:test",
            "--priority",
            "1",
        ],
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_add_output_mentions_id(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "design",
            "Design phase",
            "--scope",
            "agent:test",
            "--priority",
            "1",
        ],
    )
    # Assert
    assert "added design" in result.output


def test_add_persists_id(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "design",
            "Design phase",
            "--scope",
            "agent:test",
            "--priority",
            "1",
        ],
    )
    # Act
    tasks = _model.load_tasks(store)
    # Assert
    assert tasks[0]["id"] == "design"


def test_add_persists_scope(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "design",
            "Design phase",
            "--scope",
            "agent:test",
            "--priority",
            "1",
        ],
    )
    # Act
    tasks = _model.load_tasks(store)
    # Assert
    assert tasks[0]["scope"] == "agent:test"


def test_add_persists_priority(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "design",
            "Design phase",
            "--scope",
            "agent:test",
            "--priority",
            "1",
        ],
    )
    # Act
    tasks = _model.load_tasks(store)
    # Assert
    assert tasks[0]["priority"] == 1


def test_add_json_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["add", "--assignee", "agent:test-suite", "a", "A", "--json"],
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_add_json_emits_id(tmp_path):
    # Arrange
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["add", "--assignee", "agent:test-suite", "a", "A", "--json"],
    )
    # Act
    payload = json.loads(result.output.strip())
    # Assert
    assert payload["id"] == "a"


def test_add_json_emits_status(tmp_path):
    # Arrange
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["add", "--assignee", "agent:test-suite", "a", "A", "--json"],
    )
    # Act
    payload = json.loads(result.output.strip())
    # Assert — add's default status is `deferred` since pending was abolished.
    assert payload["status"] == "deferred"


def test_add_duplicate_id_exits_nonzero(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    # Act
    result = runner.invoke(
        main,
        ["add", "--assignee", "agent:test-suite", "a", "A again"],
    )
    # Assert
    assert result.exit_code != 0


def test_add_duplicate_id_mentions_duplicate(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    # Act
    result = runner.invoke(
        main,
        ["add", "--assignee", "agent:test-suite", "a", "A again"],
    )
    # Assert
    assert "duplicate" in result.output.lower()


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #
def test_update_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--priority",
            "10",
        ],
    )
    # Act
    result = runner.invoke(
        main,
        ["update", "a", "--status", "in_progress", "--priority", "1"],
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_update_persists_status(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--priority",
            "10",
        ],
    )
    runner.invoke(
        main,
        ["update", "a", "--status", "in_progress", "--priority", "1"],
    )
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["status"] == "in_progress"


def test_update_persists_priority(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--priority",
            "10",
        ],
    )
    runner.invoke(
        main,
        ["update", "a", "--status", "in_progress", "--priority", "1"],
    )
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["priority"] == 1


def test_update_empty_scope_clears_field_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--scope",
            "agent:initial",
        ],
    )
    # Act
    result = runner.invoke(main, ["update", "a", "--scope", ""])
    # Assert
    assert result.exit_code == 0, result.output


def test_update_empty_scope_clears_field_on_disk(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--scope",
            "agent:initial",
        ],
    )
    runner.invoke(main, ["update", "a", "--scope", ""])
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert "scope" not in on_disk


def test_update_no_fields_exits_nonzero(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    # Act
    result = runner.invoke(main, ["update", "a"])
    # Assert
    assert result.exit_code != 0


def test_update_no_fields_mentions_no_fields(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    # Act
    result = runner.invoke(main, ["update", "a"])
    # Assert
    assert "no fields" in result.output.lower()


def test_update_missing_id_exits_nonzero(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    # Act
    result = runner.invoke(main, ["update", "nope", "--status", "done"])
    # Assert
    assert result.exit_code != 0


def test_update_missing_id_mentions_not_found(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    # Act
    result = runner.invoke(main, ["update", "nope", "--status", "done"])
    # Assert
    assert "not found" in result.output.lower()


# --------------------------------------------------------------------------- #
# add — operator-co-designed flags + closed-enum validation (PR #65)          #
# --------------------------------------------------------------------------- #
def test_add_agent_flag_persists(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    # Act
    runner.invoke(main, ["add", "a", "A", "--agent", "proj-scitex-cards"])
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["agent"] == "proj-scitex-cards"


def test_add_project_flag_persists(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    # Act
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--project",
            "scitex-cards",
        ],
    )
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["project"] == "scitex-cards"


def test_add_pr_url_flag_persists(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    url = "https://github.com/ywatanabe1989/scitex-cards/pull/65"
    # Act
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--pr-url",
            url,
        ],
    )
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["pr_url"] == url


def test_add_kind_compute_persists(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    # Act
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--kind",
            "compute",
            "--job-id",
            "25754194",
            "--command",
            "srun -p gpu my.py",
        ],
    )
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["kind"] == "compute"


def test_add_invalid_status_rejected_by_click(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--status",
            "bogus",
        ],
    )
    # Assert
    assert result.exit_code != 0


def test_add_invalid_kind_rejected_by_click(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--kind",
            "bogus",
        ],
    )
    # Assert
    assert result.exit_code != 0


def test_add_invalid_blocker_rejected_by_click(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--status",
            "blocked",
            "--blocker",
            "bogus",
        ],
    )
    # Assert
    assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# update — new field flags + depends_on/blocks REPLACE semantics (PR #65)     #
# --------------------------------------------------------------------------- #
def test_update_agent_persists(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    # Act
    runner.invoke(main, ["update", "a", "--agent", "proj-scitex-cards"])
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["agent"] == "proj-scitex-cards"


def test_update_depends_on_replaces_list(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--depends-on",
            "x",
        ],
    )
    # Act — repeat --depends-on per id
    runner.invoke(
        main,
        ["update", "a", "--depends-on", "y", "--depends-on", "z"],
    )
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["depends_on"] == ["y", "z"]


def test_update_depends_on_empty_clears_list(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--depends-on",
            "x",
        ],
    )
    # Act — single --depends-on '' clears
    runner.invoke(main, ["update", "a", "--depends-on", ""])
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert "depends_on" not in on_disk


def test_update_invalid_blocker_rejected_by_click(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    # Act
    result = runner.invoke(main, ["update", "a", "--blocker", "bogus"])
    # Assert
    assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# done                                                                        #
# --------------------------------------------------------------------------- #
def test_done_exits_zero(tmp_path, env):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(main, ["done", "a"])
    # Assert
    assert result.exit_code == 0, result.output


def test_done_output_mentions_id(tmp_path, env):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(main, ["done", "a"])
    # Assert
    assert "done a" in result.output


def test_done_persists_status(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    runner.invoke(main, ["done", "a"])
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["status"] == "done"


def test_done_persists_completed_by(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    runner.invoke(main, ["done", "a"])
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["_log_meta"]["completed_by"] == "agent:cli-test"


def test_done_persists_completed_at_z_suffix(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    runner.invoke(main, ["done", "a"])
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["_log_meta"]["completed_at"].endswith("Z")


def test_done_by_overrides_env_exits_zero(tmp_path, env):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:env")
    # Act
    result = runner.invoke(main, ["done", "a", "--by", "agent:explicit"])
    # Assert
    assert result.exit_code == 0, result.output


def test_done_by_overrides_env_on_disk(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:env")
    runner.invoke(main, ["done", "a", "--by", "agent:explicit"])
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["_log_meta"]["completed_by"] == "agent:explicit"


# --------------------------------------------------------------------------- #
# list (extended w/ filters)                                                  #
# --------------------------------------------------------------------------- #
def test_list_filters_by_scope_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--scope",
            "agent:lead",
        ],
    )
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--scope",
            "agent:proj-scitex-cards",
        ],
    )
    # Act
    result = runner.invoke(
        main,
        ["list-tasks", "--scope", "agent:proj-scitex-cards", "--json"],
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_list_filters_by_scope_returns_matching(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--scope",
            "agent:lead",
        ],
    )
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--scope",
            "agent:proj-scitex-cards",
        ],
    )
    result = runner.invoke(
        main,
        ["list-tasks", "--scope", "agent:proj-scitex-cards", "--json"],
    )
    # Act
    rows = json.loads(result.output.strip())
    # Assert
    assert {r["id"] for r in rows} == {"b"}


def test_list_env_scope_default(tmp_path, env):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--scope",
            "agent:lead",
        ],
    )
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--scope",
            "agent:other",
        ],
    )
    env.set("SCITEX_CARDS_SCOPE", "agent:lead")
    # Act — no --scope here so $SCITEX_CARDS_SCOPE='agent:lead' applies via the filter path.
    result = runner.invoke(main, ["list-tasks", "--json", "--status", "deferred"])
    rows = json.loads(result.output.strip())
    # Assert
    assert {r["id"] for r in rows} == {"a"}


# --------------------------------------------------------------------------- #
# list-tasks — PR #66 filter expansion (agent / project / host / blocker /    #
# kind / id-prefix / blocking-me + multi-status)                              #
# --------------------------------------------------------------------------- #
def _seed_for_pr66(runner):
    """Seed the extended-filter test store."""
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "px1", "X1"])
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "px2",
            "X2",
            "--status",
            "in_progress",
        ],
    )
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "py1", "Y1"])
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "py2", "Y2"])


def test_list_filter_by_id_prefix(tmp_path):
    # Arrange
    runner = CliRunner()
    _seed_for_pr66(runner)
    # Act
    result = runner.invoke(
        main,
        ["list-tasks", "--json", "--id-prefix", "py"],
    )
    rows = json.loads(result.output.strip())
    # Assert
    assert {r["id"] for r in rows} == {"py1", "py2"}


def test_list_filter_by_blocker_none_token(tmp_path):
    # Arrange
    runner = CliRunner()
    _seed_for_pr66(runner)
    # Act — all four seeded rows have NO blocker field
    result = runner.invoke(main, ["list-tasks", "--json", "--blocker", "__none"])
    rows = json.loads(result.output.strip())
    # Assert
    assert {r["id"] for r in rows} == {"px1", "px2", "py1", "py2"}


def test_list_filter_multi_status_unions(tmp_path):
    # Arrange
    runner = CliRunner()
    _seed_for_pr66(runner)
    # Act — deferred (px1, py1, py2) + in_progress (px2) = all 4
    result = runner.invoke(
        main,
        [
            "list-tasks",
            "--json",
            "--status",
            "deferred",
            "--status",
            "in_progress",
        ],
    )
    rows = json.loads(result.output.strip())
    # Assert
    assert {r["id"] for r in rows} == {"px1", "px2", "py1", "py2"}


def test_list_filter_blocking_me_flag(tmp_path):
    # Arrange — seed via CLI for shape + Python API for the blocker
    # field (the CLI --blocker flag lands in a sibling PR; this PR's
    # filter logic doesn't need the CLI surface to test the predicate).
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--status",
            "blocked",
        ],
    )
    _store.update_task(None, "b", blocker="operator-decision")
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "c",
            "C",
            "--status",
            "blocked",
        ],
    )
    _store.update_task(None, "c", blocker="dependency")
    # Act
    result = runner.invoke(
        main,
        ["list-tasks", "--json", "--blocking-me"],
    )
    rows = json.loads(result.output.strip())
    # Assert
    assert {r["id"] for r in rows} == {"b"}


# --------------------------------------------------------------------------- #
# summary                                                                     #
# --------------------------------------------------------------------------- #
def test_summary_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--status",
            "done",
        ],
    )
    # Act
    result = runner.invoke(main, ["summary", "--json"])
    # Assert
    assert result.exit_code == 0, result.output


def test_summary_emits_total(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--status",
            "done",
        ],
    )
    result = runner.invoke(main, ["summary", "--json"])
    # Act
    info = json.loads(result.output.strip())
    # Assert
    assert info["total"] == 2


def test_summary_emits_done_count(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--status",
            "done",
        ],
    )
    result = runner.invoke(main, ["summary", "--json"])
    # Act
    info = json.loads(result.output.strip())
    # Assert
    assert info["by_status"]["done"] == 1


def test_summary_emits_deferred_count(tmp_path):
    # Arrange — add's default status is `deferred` since pending was abolished.
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--status",
            "done",
        ],
    )
    result = runner.invoke(main, ["summary", "--json"])
    # Act
    info = json.loads(result.output.strip())
    # Assert
    assert info["by_status"]["deferred"] == 1


# --------------------------------------------------------------------------- #
# where                                                                       #
# --------------------------------------------------------------------------- #
def test_where_exits_zero(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    Path(store).write_text("tasks: []\n", encoding="utf-8")
    env.set("SCITEX_CARDS_TASKS_YAML_SHARED", store)
    # Act
    result = runner.invoke(main, ["resolve-store", "--json"])
    # Assert
    assert result.exit_code == 0, result.output


def test_where_resolved_path(tmp_path, env):
    # Arrange — the store identity is the database path ($SCITEX_CARDS_DB).
    runner = CliRunner()
    db = str(tmp_path / "cards.db")
    Path(db).write_text("", encoding="utf-8")
    env.set("SCITEX_CARDS_DB", db)
    result = runner.invoke(main, ["resolve-store", "--json"])
    # Act
    info = json.loads(result.output.strip())
    # Assert
    assert info["resolved"] == db


def test_where_exists_true(tmp_path, env):
    # Arrange
    runner = CliRunner()
    db = str(tmp_path / "cards.db")
    Path(db).write_text("", encoding="utf-8")
    env.set("SCITEX_CARDS_DB", db)
    result = runner.invoke(main, ["resolve-store", "--json"])
    # Act
    info = json.loads(result.output.strip())
    # Assert
    assert info["exists"] is True


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #
def test_init_shared_exits_zero(tmp_path, env):
    # Arrange
    runner = CliRunner()
    env.set("SCITEX_DIR", str(tmp_path / "fake-home"))
    # Act
    result = runner.invoke(main, ["init-store", "--shared"])
    # Assert
    assert result.exit_code == 0, result.output


# THE STORE IS A BARE SCHEMA, NOT A NAMED FILE, in the three tests below.
#
# They used to point $SCITEX_CARDS_DB at `fake-home/cards/cards.db` and assert
# the verb CREATED that file -- "created" in the output, the path on disk, a
# second run reporting "no-op". Every one of those is a statement about a
# store that is a FILE, and the comment here even called it "the workflow that
# survives the abolition". It did not survive: `init-store` provisions the
# RESOLVED STORE, a filename resolves to no store, and the door refuses it
# before anything is written.
#
# What the verb does now is install the schema into whatever the store target
# resolves to, idempotently, reporting `provisioned: <target>` either way --
# there is no create/no-op distinction left to assert, because there is no
# file whose absence could make one. So the arrangement is a schema with NO
# TABLES, which is the only thing "has something to provision" can still mean.
@pytest.fixture
def bare_store(postgres_cluster_dsn):
    """A real, EMPTY PostgreSQL schema — no tables, so init-store has work.

    Deliberately NOT the per-test store the harness pins: that one is already
    schema-complete (the root conftest provisions it), so `init-store` against
    it would be a no-op and these tests would pass without the verb doing
    anything.
    """
    from scitex_dev.store.testing import ephemeral_schema

    with ephemeral_schema(postgres_cluster_dsn, prefix="cards_init") as dsn:
        yield dsn


def _tasks_table_exists(dsn: str) -> bool:
    """Read the catalogue through the package's own probe."""
    from scitex_cards._db import connect
    from scitex_cards._schema_probe import has_table

    conn = connect(dsn)
    try:
        return has_table(conn, "tasks")
    finally:
        conn.close()


def test_init_shared_reports_the_store_it_provisioned(bare_store, env):
    # Arrange — an empty schema, so the verb has a schema to install.
    runner = CliRunner()
    env.set("SCITEX_CARDS_DB", bare_store)
    # Act
    result = runner.invoke(main, ["init-store", "--shared"])
    # Assert — naming the target is what lets a reader check WHICH store was
    # provisioned, which matters more than whether it said "created".
    assert "provisioned" in result.output


def test_init_shared_installs_the_schema(bare_store, env):
    # Arrange
    runner = CliRunner()
    env.set("SCITEX_CARDS_DB", bare_store)
    before = _tasks_table_exists(bare_store)
    # Act
    runner.invoke(main, ["init-store", "--shared"])
    # Assert — asserted as a TRANSITION, so a schema that already had the
    # table could not make this pass without the verb doing anything.
    assert (before, _tasks_table_exists(bare_store)) == (False, True)


def test_init_shared_is_idempotent(bare_store, env):
    # Arrange
    runner = CliRunner()
    env.set("SCITEX_CARDS_DB", bare_store)
    runner.invoke(main, ["init-store", "--shared"])
    # Act
    again = runner.invoke(main, ["init-store", "--shared"])
    # Assert — a second run is additive-only and still succeeds. It no longer
    # says "no-op": `init_schema` creates what is missing and touches no row,
    # so both runs report the same thing and neither is a special case.
    assert again.exit_code == 0, again.output


def _init_shared_with_no_store_configured(tmp_path, env):
    """Invoke ``init-store --shared`` with nothing naming a store.

    ``$SCITEX_DIR`` steers only local state, so setting it leaves the store
    axis genuinely unconfigured -- which is the state under test.
    """
    runner = CliRunner()
    env.set("SCITEX_DIR", str(tmp_path / "fake-home"))
    env.delete("SCITEX_CARDS_DB")
    return runner.invoke(main, ["init-store", "--shared"])


def test_init_shared_refuses_when_no_store_is_configured(tmp_path, env):
    """The tier those three tests used to ride on is GONE, and says so.

    Without this, the edits above would read as "the fixture changed" rather
    than "the behaviour changed", and nothing would notice if the zero-config
    default came back: the three tests above would simply pass again.
    """
    # Arrange
    # Act
    result = _init_shared_with_no_store_configured(tmp_path, env)
    # Assert
    assert result.exit_code != 0


def test_init_shared_refusal_names_the_variable_to_set(tmp_path, env):
    """Refusing is half the job; the reader needs the variable to export."""
    # Arrange
    # Act
    result = _init_shared_with_no_store_configured(tmp_path, env)
    # Assert
    assert "SCITEX_CARDS_DB" in result.output


def test_init_project_outside_git_errors(tmp_path, env):
    """`--project` outside a git repo must error rather than silently picking
    a wrong directory."""
    # Arrange
    runner = CliRunner()
    env.chdir(tmp_path)
    # Act
    result = runner.invoke(main, ["init-store", "--project"])
    # Assert
    assert result.exit_code != 0


def test_init_project_says_the_flag_is_removed(tmp_path, env):
    """The refusal changed REASON, and the reason is the whole point.

    This asserted the error mentions "git repo" -- `--project` used to resolve
    a per-repository store and had to refuse when it could not find the repo.
    A project-scoped store is not a thing that can be resolved badly any more;
    it is not expressible. The store is one board for the fleet, and a
    per-repository copy of a shared board is the shape that took it from 2,138
    cards to 3.

    So the refusal no longer depends on WHERE it is run -- outside a git repo
    or inside one, the answer is the same -- and asserting the old wording
    would pin a diagnostic that sends the reader looking for a missing repo
    instead of telling them the flag is gone.
    """
    # Arrange
    runner = CliRunner()
    env.chdir(tmp_path)
    # Act
    result = runner.invoke(main, ["init-store", "--project"])
    # Assert
    assert "no project-scoped store" in result.output.lower()


# --------------------------------------------------------------------------- #
# sync (Phase-1 stub)                                                         #
# --------------------------------------------------------------------------- #
def test_sync_dry_run_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sync-store", "--dry-run"])
    # Assert
    assert result.exit_code == 0, result.output


def test_sync_dry_run_mentions_stub(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sync-store", "--dry-run"])
    # Assert
    assert "PHASE-1 STUB" in result.output


def test_sync_dry_run_mentions_git(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sync-store", "--dry-run"])
    # Assert
    assert "git" in result.output


def test_sync_apply_exits_nonzero(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sync-store", "--apply"])
    # Assert
    assert result.exit_code != 0


def test_sync_apply_mentions_phase(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sync-store", "--apply"])
    # Assert
    assert "Phase 1" in result.output or "Phase 2" in result.output


# --------------------------------------------------------------------------- #
# mcp subgroup — env-dependent (graceful in both [mcp] installed / missing)   #
# --------------------------------------------------------------------------- #
def test_mcp_install_exits_zero():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "install"])
    # Assert
    assert result.exit_code == 0, result.output


def test_mcp_install_payload_has_mcp_servers():
    # Arrange
    runner = CliRunner()
    result = runner.invoke(main, ["mcp", "install"])
    # Act
    payload = json.loads(result.output)
    # Assert
    assert "mcpServers" in payload


def test_mcp_install_payload_has_scitex_cards():
    # Arrange
    runner = CliRunner()
    result = runner.invoke(main, ["mcp", "install"])
    # Act
    payload = json.loads(result.output)
    # Assert
    assert "scitex-cards" in payload["mcpServers"]


def _mcp_doctor_info():
    """Run ``scitex-cards mcp doctor --json`` and return the parsed payload.

    Tests that branch on fastmcp's presence call this once and then check a
    single field — keeps each test at one assertion (STX-TQ007).
    """
    runner = CliRunner()
    result = runner.invoke(main, ["mcp", "doctor", "--json"])
    return result, json.loads(result.output.splitlines()[-1])


_FASTMCP_AVAILABLE = importlib.util.find_spec("fastmcp") is not None


@pytest.mark.skipif(
    _FASTMCP_AVAILABLE, reason="fastmcp installed — critical-path test not applicable"
)
def test_mcp_doctor_critical_when_fastmcp_missing():
    """Without fastmcp, doctor reports `critical`."""
    # Arrange
    _, info = _mcp_doctor_info()
    # Act
    status = info["status"]
    # Assert
    assert status == "critical"


@pytest.mark.skipif(
    _FASTMCP_AVAILABLE, reason="fastmcp installed — critical-path test not applicable"
)
def test_mcp_doctor_hint_when_fastmcp_missing():
    """Without fastmcp, doctor hint mentions the mcp extra."""
    # Arrange
    _, info = _mcp_doctor_info()
    # Act
    hint = (info["hint"] or "").lower()
    # Assert
    assert "mcp" in hint


@pytest.mark.skipif(
    _FASTMCP_AVAILABLE, reason="fastmcp installed — critical-path test not applicable"
)
def test_mcp_doctor_exit_code_when_fastmcp_missing():
    """Without fastmcp, doctor exits with code 2."""
    # Arrange
    result, _ = _mcp_doctor_info()
    # Act
    code = result.exit_code
    # Assert
    assert code == 2


@pytest.mark.skipif(
    not _FASTMCP_AVAILABLE, reason="fastmcp not installed — ok-path test not applicable"
)
def test_mcp_doctor_status_ok_when_fastmcp_installed():
    """With fastmcp, doctor reports ok (or degraded if 0 tools)."""
    # Arrange
    _, info = _mcp_doctor_info()
    # Act
    status = info["status"]
    # Assert
    assert status in ("ok", "degraded")


@pytest.mark.skipif(
    not _FASTMCP_AVAILABLE,
    reason="fastmcp not installed — tool-count test not applicable",
)
def test_mcp_doctor_tool_count_when_fastmcp_installed():
    """With fastmcp, doctor tool count matches TOOL_NAMES."""
    # Arrange
    from scitex_cards._mcp_server import TOOL_NAMES  # noqa: PLC0415

    _, info = _mcp_doctor_info()
    # Act
    count = info["tools"]
    # Assert
    assert count == len(TOOL_NAMES)


# --------------------------------------------------------------------------- #
# kind=status — board card scitex-cards-relocate-q-status-tracking + lead a2a  #
# 60a1a93d. Per option (b): the CLI surface accepts the new kind and the     #
# list-tasks --kind filter selects it. Default list behavior UNCHANGED — the #
# board's default-hide is a separate frontend PR.                             #
# --------------------------------------------------------------------------- #
def test_update_kind_status_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "q-gen",
            "q-gen quality status",
        ],
    )
    # Act
    result = runner.invoke(main, ["update", "q-gen", "--kind", "status"])
    # Assert
    assert result.exit_code == 0, result.output


def test_update_kind_status_persists(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "q-io",
            "q-io quality status",
        ],
    )
    runner.invoke(main, ["update", "q-io", "--kind", "status"])
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["kind"] == "status"


def test_list_filter_by_kind_status_returns_only_status_rows(tmp_path):
    # Arrange — two rows, only one tagged kind=status.
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "real-task",
            "Real work",
        ],
    )
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "q-ml",
            "q-ml status",
            "--kind",
            "status",
        ],
    )
    # Act
    result = runner.invoke(main, ["list-tasks", "--json", "--kind", "status"])
    rows = json.loads(result.output.strip())
    # Assert
    assert {r["id"] for r in rows} == {"q-ml"}


# EOF


def test_update_help_renders(tmp_path):
    """`update --help` must render on every supported click.

    Regression (neurovista, 2026-07-11): _BlockerOrClearParamType.get_metavar
    lacked the ctx kwarg click >= 8.2 passes, so --help crashed with a
    TypeError inside get_help_record and the update syntax was
    undiscoverable.
    """
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["update", "--help"])
    # Assert
    assert result.exit_code == 0, result.output


def test_update_help_documents_the_blocker_option(tmp_path):
    """The update syntax must stay discoverable from `--help`."""
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["update", "--help"])
    # Assert
    assert "--blocker" in result.output
