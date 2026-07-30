#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the scitex-todo root CLI group + core verbs (no mocks; CliRunner)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from scitex_cards._cli import _main as _main_module
from scitex_cards._cli import main
from scitex_cards._store import add_task


def _seed():
    """Seed the harness-provided store with a small dependency pair.

    ``tests/conftest.py`` bootstraps an empty per-test store and pins every
    store-selecting env var at it, so neither this seeder nor the CLI needs
    to be told where the store is.
    """
    add_task(id="design", title="Design", status="done", assignee="agent:test")
    add_task(
        id="build",
        title="Build",
        status="deferred",
        assignee="agent:test",
        depends_on=["design"],
    )


def test_list_tasks_command_prints_resolved_task_ids():
    # Arrange
    runner = CliRunner()
    _seed()
    # Act
    result = runner.invoke(main, ["list-tasks"])
    # Assert
    assert "design" in result.output


def test_list_tasks_json_emits_parseable_array():
    # Arrange
    runner = CliRunner()
    _seed()
    # Act
    result = runner.invoke(main, ["list-tasks", "--json"])
    ids = [task["id"] for task in json.loads(result.output)]
    # Assert
    assert ids == ["design", "build"]


def test_render_graph_print_mermaid_emits_flowchart_source():
    # Arrange
    runner = CliRunner()
    _seed()
    # Act
    result = runner.invoke(main, ["render-graph", "--print-mermaid"])
    # Assert
    assert result.output.startswith("flowchart TB")


def test_render_graph_print_mermaid_includes_dependency_edge():
    # Arrange
    runner = CliRunner()
    _seed()
    # Act
    result = runner.invoke(main, ["render-graph", "--print-mermaid"])
    # Assert
    assert "design --> build" in result.output


def test_version_flag_exits_zero():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["--version"])
    # Assert
    assert result.exit_code == 0


def test_help_recursive_json_emits_command_tree():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["--help-recursive", "--json"])
    tree = json.loads(result.output)
    # Assert
    assert "render-graph" in tree["commands"]


# --------------------------------------------------------------------------- #
# CURRENCY gate — every CLI invocation is gated via `main`'s group callback   #
# (see `scitex_cards._currency.check_currency`, `tests/scitex_cards/          #
# test__currency.py` for the gate's own no-op/pass/raise behavior).           #
# --------------------------------------------------------------------------- #
def test_main_passes_through_when_the_currency_check_is_a_no_op(monkeypatch):
    # Arrange — the real `check_currency()` is already a no-op whenever
    # scitex-dev is absent or lacks the staleness module; pin that
    # explicitly here so this test does not depend on what happens to be
    # installed in the environment.
    monkeypatch.setattr(_main_module, "check_currency", lambda: None)
    runner = CliRunner()
    _seed()

    # Act
    result = runner.invoke(main, ["list-tasks"])

    # Assert
    assert result.exit_code == 0
    assert "design" in result.output


def test_main_surfaces_a_stale_install_as_a_clean_click_exception(monkeypatch):
    # Arrange — fake a stale/broken-install verdict the way scitex-dev's
    # `ensure_current` would raise it, remedy command included.
    remedy = "pip install -U scitex-cards"

    def _boom():
        raise RuntimeError(f"scitex-cards is stale — run: {remedy}")

    monkeypatch.setattr(_main_module, "check_currency", _boom)
    runner = CliRunner()

    # Act
    result = runner.invoke(main, ["list-tasks"])

    # Assert — a clean CLI error (not a raw traceback), remedy preserved.
    assert result.exit_code != 0
    assert remedy in result.output


def test_main_does_not_swallow_the_currency_check_for_an_unrelated_subcommand(
    monkeypatch,
):
    """The gate must fire for a subcommand unrelated to the one exercised
    above — proving it runs unconditionally in the group callback, not only
    on some code path one particular command happens to hit. (`--version` is
    NOT usable here: click's `version_option` is an eager flag that exits
    during option parsing, before the group callback body ever runs.)"""

    # Arrange
    def _boom():
        raise RuntimeError("scitex-cards is stale")

    monkeypatch.setattr(_main_module, "check_currency", _boom)
    runner = CliRunner()

    # Act
    result = runner.invoke(main, ["render-graph", "--print-mermaid"])

    # Assert
    assert result.exit_code != 0
    assert "scitex-cards is stale" in result.output
