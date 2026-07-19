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


def _store_path(tmp_path) -> str:
    """Path string to a fresh empty store under tmp_path/.scitex/todo/."""
    return str(tmp_path / "tasks.yaml")


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #
def test_add_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    # Act
    result = runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "design",
            "Design phase",
            "--tasks",
            store,
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
    store = _store_path(tmp_path)
    # Act
    result = runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "design",
            "Design phase",
            "--tasks",
            store,
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
            "--tasks",
            store,
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
            "--tasks",
            store,
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
            "--tasks",
            store,
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
    store = _store_path(tmp_path)
    # Act
    result = runner.invoke(
        main,
        ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store, "--json"],
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_add_json_emits_id(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    result = runner.invoke(
        main,
        ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store, "--json"],
    )
    # Act
    payload = json.loads(result.output.strip())
    # Assert
    assert payload["id"] == "a"


def test_add_json_emits_status(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    result = runner.invoke(
        main,
        ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store, "--json"],
    )
    # Act
    payload = json.loads(result.output.strip())
    # Assert — add's default status is `deferred` since pending was abolished.
    assert payload["status"] == "deferred"


def test_add_duplicate_id_exits_nonzero(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    # Act
    result = runner.invoke(
        main,
        ["add", "--assignee", "agent:test-suite", "a", "A again", "--tasks", store],
    )
    # Assert
    assert result.exit_code != 0


def test_add_duplicate_id_mentions_duplicate(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    # Act
    result = runner.invoke(
        main,
        ["add", "--assignee", "agent:test-suite", "a", "A again", "--tasks", store],
    )
    # Assert
    assert "duplicate" in result.output.lower()


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #
def test_update_exits_zero(tmp_path):
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
            "--tasks",
            store,
            "--priority",
            "10",
        ],
    )
    # Act
    result = runner.invoke(
        main,
        ["update", "a", "--tasks", store, "--status", "in_progress", "--priority", "1"],
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
            "--tasks",
            store,
            "--priority",
            "10",
        ],
    )
    runner.invoke(
        main,
        ["update", "a", "--tasks", store, "--status", "in_progress", "--priority", "1"],
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
            "--tasks",
            store,
            "--priority",
            "10",
        ],
    )
    runner.invoke(
        main,
        ["update", "a", "--tasks", store, "--status", "in_progress", "--priority", "1"],
    )
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["priority"] == 1


def test_update_empty_scope_clears_field_exits_zero(tmp_path):
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
            "--tasks",
            store,
            "--scope",
            "agent:initial",
        ],
    )
    # Act
    result = runner.invoke(main, ["update", "a", "--tasks", store, "--scope", ""])
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
            "--tasks",
            store,
            "--scope",
            "agent:initial",
        ],
    )
    runner.invoke(main, ["update", "a", "--tasks", store, "--scope", ""])
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert "scope" not in on_disk


def test_update_no_fields_exits_nonzero(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    # Act
    result = runner.invoke(main, ["update", "a", "--tasks", store])
    # Assert
    assert result.exit_code != 0


def test_update_no_fields_mentions_no_fields(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    # Act
    result = runner.invoke(main, ["update", "a", "--tasks", store])
    # Assert
    assert "no fields" in result.output.lower()


def test_update_missing_id_exits_nonzero(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    # Act
    result = runner.invoke(
        main, ["update", "nope", "--tasks", store, "--status", "done"]
    )
    # Assert
    assert result.exit_code != 0


def test_update_missing_id_mentions_not_found(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    # Act
    result = runner.invoke(
        main, ["update", "nope", "--tasks", store, "--status", "done"]
    )
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
    runner.invoke(
        main, ["add", "a", "A", "--tasks", store, "--agent", "proj-scitex-todo"]
    )
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["agent"] == "proj-scitex-todo"


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
            "--tasks",
            store,
            "--project",
            "scitex-todo",
        ],
    )
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["project"] == "scitex-todo"


def test_add_pr_url_flag_persists(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    url = "https://github.com/ywatanabe1989/scitex-todo/pull/65"
    # Act
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--tasks",
            store,
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
            "--tasks",
            store,
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
    store = _store_path(tmp_path)
    # Act
    result = runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--tasks",
            store,
            "--status",
            "bogus",
        ],
    )
    # Assert
    assert result.exit_code != 0


def test_add_invalid_kind_rejected_by_click(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    # Act
    result = runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--tasks",
            store,
            "--kind",
            "bogus",
        ],
    )
    # Assert
    assert result.exit_code != 0


def test_add_invalid_blocker_rejected_by_click(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    # Act
    result = runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--tasks",
            store,
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
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    # Act
    runner.invoke(
        main, ["update", "a", "--tasks", store, "--agent", "proj-scitex-todo"]
    )
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["agent"] == "proj-scitex-todo"


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
            "--tasks",
            store,
            "--depends-on",
            "x",
        ],
    )
    # Act — repeat --depends-on per id
    runner.invoke(
        main,
        ["update", "a", "--tasks", store, "--depends-on", "y", "--depends-on", "z"],
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
            "--tasks",
            store,
            "--depends-on",
            "x",
        ],
    )
    # Act — single --depends-on '' clears
    runner.invoke(main, ["update", "a", "--tasks", store, "--depends-on", ""])
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert "depends_on" not in on_disk


def test_update_invalid_blocker_rejected_by_click(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    # Act
    result = runner.invoke(
        main, ["update", "a", "--tasks", store, "--blocker", "bogus"]
    )
    # Assert
    assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# done                                                                        #
# --------------------------------------------------------------------------- #
def test_done_exits_zero(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(main, ["done", "a", "--tasks", store])
    # Assert
    assert result.exit_code == 0, result.output


def test_done_output_mentions_id(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(main, ["done", "a", "--tasks", store])
    # Assert
    assert "done a" in result.output


def test_done_persists_status(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    runner.invoke(main, ["done", "a", "--tasks", store])
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["status"] == "done"


def test_done_persists_completed_by(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    runner.invoke(main, ["done", "a", "--tasks", store])
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["_log_meta"]["completed_by"] == "agent:cli-test"


def test_done_persists_completed_at_z_suffix(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    runner.invoke(main, ["done", "a", "--tasks", store])
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["_log_meta"]["completed_at"].endswith("Z")


def test_done_by_overrides_env_exits_zero(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:env")
    # Act
    result = runner.invoke(
        main, ["done", "a", "--tasks", store, "--by", "agent:explicit"]
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_done_by_overrides_env_on_disk(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:env")
    runner.invoke(main, ["done", "a", "--tasks", store, "--by", "agent:explicit"])
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
    store = _store_path(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "a",
            "A",
            "--tasks",
            store,
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
            "--tasks",
            store,
            "--scope",
            "agent:proj-scitex-todo",
        ],
    )
    # Act
    result = runner.invoke(
        main,
        ["list-tasks", "--tasks", store, "--scope", "agent:proj-scitex-todo", "--json"],
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_list_filters_by_scope_returns_matching(tmp_path):
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
            "--tasks",
            store,
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
            "--tasks",
            store,
            "--scope",
            "agent:proj-scitex-todo",
        ],
    )
    result = runner.invoke(
        main,
        ["list-tasks", "--tasks", store, "--scope", "agent:proj-scitex-todo", "--json"],
    )
    # Act
    rows = json.loads(result.output.strip())
    # Assert
    assert {r["id"] for r in rows} == {"b"}


def test_list_env_scope_default(tmp_path, env):
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
            "--tasks",
            store,
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
            "--tasks",
            store,
            "--scope",
            "agent:other",
        ],
    )
    env.set("SCITEX_TODO_SCOPE", "agent:lead")
    # Act — no --scope here so $SCITEX_TODO_SCOPE='agent:lead' applies via the filter path.
    result = runner.invoke(
        main, ["list-tasks", "--tasks", store, "--json", "--status", "deferred"]
    )
    rows = json.loads(result.output.strip())
    # Assert
    assert {r["id"] for r in rows} == {"a"}


# --------------------------------------------------------------------------- #
# list-tasks — PR #66 filter expansion (agent / project / host / blocker /    #
# kind / id-prefix / blocking-me + multi-status)                              #
# --------------------------------------------------------------------------- #
def _seed_for_pr66(runner, store):
    """Seed the extended-filter test store."""
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "px1", "X1", "--tasks", store]
    )
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "px2",
            "X2",
            "--tasks",
            store,
            "--status",
            "in_progress",
        ],
    )
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "py1", "Y1", "--tasks", store]
    )
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "py2", "Y2", "--tasks", store]
    )


def test_list_filter_by_id_prefix(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    _seed_for_pr66(runner, store)
    # Act
    result = runner.invoke(
        main,
        ["list-tasks", "--tasks", store, "--json", "--id-prefix", "py"],
    )
    rows = json.loads(result.output.strip())
    # Assert
    assert {r["id"] for r in rows} == {"py1", "py2"}


def test_list_filter_by_blocker_none_token(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    _seed_for_pr66(runner, store)
    # Act — all four seeded rows have NO blocker field
    result = runner.invoke(
        main, ["list-tasks", "--tasks", store, "--json", "--blocker", "__none"]
    )
    rows = json.loads(result.output.strip())
    # Assert
    assert {r["id"] for r in rows} == {"px1", "px2", "py1", "py2"}


def test_list_filter_multi_status_unions(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    _seed_for_pr66(runner, store)
    # Act — deferred (px1, py1, py2) + in_progress (px2) = all 4
    result = runner.invoke(
        main,
        [
            "list-tasks",
            "--tasks",
            store,
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
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--tasks",
            store,
            "--status",
            "blocked",
        ],
    )
    _store.update_task(store, "b", blocker="operator-decision")
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "c",
            "C",
            "--tasks",
            store,
            "--status",
            "blocked",
        ],
    )
    _store.update_task(store, "c", blocker="dependency")
    # Act
    result = runner.invoke(
        main,
        ["list-tasks", "--tasks", store, "--json", "--blocking-me"],
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
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--tasks",
            store,
            "--status",
            "done",
        ],
    )
    # Act
    result = runner.invoke(main, ["summary", "--tasks", store, "--json"])
    # Assert
    assert result.exit_code == 0, result.output


def test_summary_emits_total(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--tasks",
            store,
            "--status",
            "done",
        ],
    )
    result = runner.invoke(main, ["summary", "--tasks", store, "--json"])
    # Act
    info = json.loads(result.output.strip())
    # Assert
    assert info["total"] == 2


def test_summary_emits_done_count(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--tasks",
            store,
            "--status",
            "done",
        ],
    )
    result = runner.invoke(main, ["summary", "--tasks", store, "--json"])
    # Act
    info = json.loads(result.output.strip())
    # Assert
    assert info["by_status"]["done"] == 1


def test_summary_emits_deferred_count(tmp_path):
    # Arrange — add's default status is `deferred` since pending was abolished.
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "b",
            "B",
            "--tasks",
            store,
            "--status",
            "done",
        ],
    )
    result = runner.invoke(main, ["summary", "--tasks", store, "--json"])
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
    env.delete("SCITEX_TODO_TASKS_YAML_SHARED")
    # Act
    result = runner.invoke(main, ["resolve-store", "--tasks", store, "--json"])
    # Assert
    assert result.exit_code == 0, result.output


def test_where_resolved_path(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    Path(store).write_text("tasks: []\n", encoding="utf-8")
    env.delete("SCITEX_TODO_TASKS_YAML_SHARED")
    result = runner.invoke(main, ["resolve-store", "--tasks", store, "--json"])
    # Act
    info = json.loads(result.output.strip())
    # Assert
    assert info["resolved"] == store


def test_where_exists_true(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    Path(store).write_text("tasks: []\n", encoding="utf-8")
    env.delete("SCITEX_TODO_TASKS_YAML_SHARED")
    result = runner.invoke(main, ["resolve-store", "--tasks", store, "--json"])
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


def test_init_shared_output_mentions_created(tmp_path, env):
    # Arrange
    runner = CliRunner()
    env.set("SCITEX_DIR", str(tmp_path / "fake-home"))
    # Act
    result = runner.invoke(main, ["init-store", "--shared"])
    # Assert
    assert "created" in result.output


def test_init_shared_creates_file(tmp_path, env):
    # Arrange
    runner = CliRunner()
    env.set("SCITEX_DIR", str(tmp_path / "fake-home"))
    runner.invoke(main, ["init-store", "--shared"])
    # Act
    expected = tmp_path / "fake-home" / "todo" / "tasks.yaml"
    # Assert
    assert expected.exists()


def test_init_shared_is_idempotent(tmp_path, env):
    # Arrange
    runner = CliRunner()
    env.set("SCITEX_DIR", str(tmp_path / "fake-home"))
    runner.invoke(main, ["init-store", "--shared"])
    # Act
    again = runner.invoke(main, ["init-store", "--shared"])
    # Assert
    assert "no-op" in again.output


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


def test_init_project_outside_git_mentions_git_repo(tmp_path, env):
    """`--project` outside a git repo must error rather than silently picking
    a wrong directory."""
    # Arrange
    runner = CliRunner()
    env.chdir(tmp_path)
    # Act
    result = runner.invoke(main, ["init-store", "--project"])
    # Assert
    assert "git repo" in result.output.lower()


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
    assert "scitex-todo" in payload["mcpServers"]


def _mcp_doctor_info():
    """Run ``scitex-todo mcp doctor --json`` and return the parsed payload.

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
# kind=status — board card scitex-todo-relocate-q-status-tracking + lead a2a  #
# 60a1a93d. Per option (b): the CLI surface accepts the new kind and the     #
# list-tasks --kind filter selects it. Default list behavior UNCHANGED — the #
# board's default-hide is a separate frontend PR.                             #
# --------------------------------------------------------------------------- #
def test_update_kind_status_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "q-gen",
            "q-gen quality status",
            "--tasks",
            store,
        ],
    )
    # Act
    result = runner.invoke(
        main, ["update", "q-gen", "--tasks", store, "--kind", "status"]
    )
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
            "--tasks",
            store,
        ],
    )
    runner.invoke(main, ["update", "q-io", "--tasks", store, "--kind", "status"])
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["kind"] == "status"


def test_list_filter_by_kind_status_returns_only_status_rows(tmp_path):
    # Arrange — two rows, only one tagged kind=status.
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            "real-task",
            "Real work",
            "--tasks",
            store,
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
            "--tasks",
            store,
            "--kind",
            "status",
        ],
    )
    # Act
    result = runner.invoke(
        main, ["list-tasks", "--tasks", store, "--json", "--kind", "status"]
    )
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
