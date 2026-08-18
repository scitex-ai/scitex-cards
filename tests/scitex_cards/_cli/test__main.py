#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the scitex-cards root CLI group + core verbs (no mocks; CliRunner)."""

from __future__ import annotations

import json
import logging

import click
import pytest
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
@pytest.fixture()
def gate_bypassed(env):
    """The REAL gate, told to stand down through its own documented switch.

    `SCITEX_DEV_NO_CURRENCY_GATE=1` is scitex-dev's published bypass, and it
    is a better arrangement than a stubbed no-op for a reason worth stating:
    a stub proves the CLI survives a gate that does nothing, while this proves
    the CLI survives THE gate, running, taking its own no-op path.
    """
    env.set("SCITEX_DEV_NO_CURRENCY_GATE", "1")
    yield


#: What scitex-dev's refusal looks like: a message carrying the remedy.
_REMEDY = "pip install -U scitex-cards"


def _refusing_gate():
    """A gate with `check_currency`'s refusing contract, for the translation.

    NOT a stand-in for the real gate's DECISION — `test__currency.py` owns
    that, through `check_currency`'s own `staleness_module` / `over_overlay`
    parameters. What is exercised here is the CLI's half: whatever comes out
    of the gate as an exception must reach the user as a clean error line.
    """
    raise RuntimeError(f"scitex-cards is stale — run: {_REMEDY}")


def test_main_passes_through_when_the_currency_check_is_a_no_op(gate_bypassed):
    # Arrange
    runner = CliRunner()
    _seed()

    # Act
    result = runner.invoke(main, ["list-tasks"])

    # Assert
    assert result.exit_code == 0


def test_the_passing_gate_does_not_swallow_the_subcommands_output(gate_bypassed):
    # Arrange
    runner = CliRunner()
    _seed()

    # Act
    result = runner.invoke(main, ["list-tasks"])

    # Assert — the gate ran and got out of the way, rather than the command
    # exiting 0 without having done anything.
    assert "design" in result.output


def test_a_refused_install_becomes_a_click_exception_not_a_raw_error():
    # Arrange
    gate = _refusing_gate

    # Act
    # Assert — a RuntimeError reaching the user is a traceback; a
    # ClickException is the error line Click prints and exits 1 on.
    with pytest.raises(click.ClickException):
        _main_module._run_currency_gate(gate=gate)


def test_a_refused_install_keeps_the_remedy_in_the_message():
    # Arrange
    # Act
    try:
        _main_module._run_currency_gate(gate=_refusing_gate)
    except click.ClickException as exc:
        message = str(exc)

    # Assert — the remedy IS the payload; a translation that drops it leaves
    # the user refused and uninstructed.
    assert _REMEDY in message


def test_a_click_exception_from_the_gate_passes_through_unwrapped():
    """A gate that already speaks Click must not be re-wrapped.

    Double-wrapping would nest the message inside another exception's str()
    and is the reason the `except click.ClickException: raise` arm exists.
    """
    # Arrange
    def _already_click():
        raise click.ClickException("already formatted")

    # Act
    try:
        _main_module._run_currency_gate(gate=_already_click)
    except click.ClickException as exc:
        message = str(exc)

    # Assert
    assert message == "already formatted"


def test_a_passing_gate_lets_the_callback_continue():
    # Arrange
    # Act
    result = _main_module._run_currency_gate(gate=lambda: None)

    # Assert — no exception, nothing returned; the callback proceeds.
    assert result is None


def test_the_gate_actually_runs_for_an_unrelated_subcommand(gate_bypassed, caplog):
    """The gate fires in the group callback, not on one command's code path.

    Proven with the REAL gate rather than a stub: scitex-dev's documented
    bypass logs a loud "CURRENCY GATE BYPASSED" precisely so an exercised
    bypass is never silent, and that log line is only emitted if the gate was
    entered. So the record is evidence the callback ran it for THIS command.

    (`--version` is NOT usable here: click's `version_option` is an eager flag
    that exits during option parsing, before the group callback body runs.)
    """
    # Arrange
    runner = CliRunner()

    # Act
    with caplog.at_level(logging.WARNING):
        runner.invoke(main, ["render-graph", "--print-mermaid"])

    # Assert
    assert "CURRENCY GATE BYPASSED" in caplog.text
