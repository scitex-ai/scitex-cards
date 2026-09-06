#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the §1a introspection commands and the live `mcp` subgroup.

After Phase 1, the `mcp` subgroup is owned by `_cli/_mcp.py` (real MCP
server); the obsolete "no MCP yet" stub that used to live in
`_introspect.py` is gone. This module covers both `list-python-apis`
and a sanity check on the live `mcp` subgroup's required-verb shape.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main


def test_list_python_apis_lists_public_surface():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["list-python-apis"])
    # Assert
    # After audit §6 narrowing, the public surface is the 6 task-store
    # functions (each matching a Convention A MCP tool name 1:1).
    assert "add_task" in result.output


def test_list_python_apis_json_is_parseable():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["list-python-apis", "--json"])
    names = {entry["name"] for entry in json.loads(result.output)}
    # Assert
    assert "list_tasks" in names


def test_list_python_apis_verbose_ladder_is_monotonic():
    # Arrange
    runner = CliRunner()
    # Act
    counts = [
        len([ln for ln in runner.invoke(main, ["list-python-apis", *flag]).output.splitlines() if ln.strip()])
        for flag in ([], ["-v"], ["-vv"], ["-vvv"])
    ]
    # Assert
    assert counts == sorted(counts)


def _invoke_mcp_help():
    """Helper: run ``scitex-cards mcp --help`` and return the CliRunner result."""
    return CliRunner().invoke(main, ["mcp", "--help"])


def test_mcp_subgroup_help_exits_zero():
    """The new `mcp` subgroup (`_cli/_mcp.py`, replacing the obsolete
    "no MCP yet" stub from `_introspect.py`) must be importable + runnable."""
    # Arrange
    # (none — the helper is the whole arrange step)
    # Act
    result = _invoke_mcp_help()
    # Assert
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("verb", ("start", "doctor", "list-tools", "install"))
def test_mcp_subgroup_exposes_required_verb(verb):
    """§3 mandates all four verbs on every package's MCP subgroup."""
    # Arrange
    result = _invoke_mcp_help()
    # Act
    has_verb = verb in result.output
    # Assert
    assert has_verb, f"`mcp {verb}` missing from --help: {result.output!r}"
