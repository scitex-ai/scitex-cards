#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the scitex-cards root CLI group + core verbs (no mocks; CliRunner)."""

from __future__ import annotations

import ast
import inspect
import json
import textwrap

import click
import pytest
from click.testing import CliRunner

from scitex_cards._cli import _main as _main_module
from scitex_cards._cli import main
from scitex_cards._cli._main import _run_currency_gate
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
def test_the_gate_passes_through_when_the_check_is_a_no_op():
    # Arrange — the real `check_currency()` is a no-op whenever scitex-dev is
    # absent, so a no-op gate is a REAL arrangement, not a stand-in. Supplied
    # as an argument, so the result does not depend on what is installed here.
    # Act
    returned = _run_currency_gate(check=lambda: None)

    # Assert — reaching this at all is the did-not-raise evidence.
    assert returned is None


#: The remedy command scitex-dev puts in its refusal. It has to survive the
#: translation to a ClickException, or the operator is told what broke without
#: being told what to do about it.
_REMEDY = "pip install -U scitex-cards"


@pytest.fixture
def translated_refusal() -> str:
    """The message the CLI shows when the gate refuses.

    The `pytest.raises` block lives HERE: it counts as an assertion, so inline
    it would make the test below two assertions in one (STX-TQ007).
    """

    def _boom():
        raise RuntimeError(f"scitex-cards is stale — run: {_REMEDY}")

    with pytest.raises(click.ClickException) as exc_info:
        _run_currency_gate(check=_boom)
    return exc_info.value.format_message()


def test_the_gate_surfaces_a_refusal_as_a_clean_click_exception(
    translated_refusal: str,
):
    # Arrange (fixture)
    # Act
    message = translated_refusal
    # Assert — a clean CLI error rather than a raw traceback, remedy intact.
    assert _REMEDY in message


def test_the_group_callback_runs_the_gate_so_every_subcommand_is_covered():
    """WHERE the gate is called is the property, and it is not observable from
    any single subcommand's behaviour.

    The old form invoked one subcommand with a raising gate and inferred the
    rest. That inference is exactly what could rot: moving the call into a
    single command's body would keep that test green while silently ungating
    every OTHER command. Reading the group callback's own body answers the
    question directly — if the call is not there, no subcommand is gated.
    """
    # Arrange
    source = inspect.getsource(_main_module.main.callback)

    # Act
    called = [
        node
        for node in ast.walk(ast.parse(textwrap.dedent(source)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_run_currency_gate"
    ]

    # Assert
    assert len(called) == 1
