#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `scitex-todo mcp channel` verb registration.

CliRunner against the real click group (no mocks — STX-NM / PA-306). Verifies
the verb is wired onto the `mcp` group and that its `--help` advertises the
standalone channel server. Also pins the fail-loud agent-id behavior via the
CLI surface.
"""

from __future__ import annotations

from click.testing import CliRunner

from scitex_cards._cli import main


def test_mcp_group_help_exits_zero():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "--help"])
    # Assert
    assert result.exit_code == 0, result.output


def test_mcp_group_help_lists_channel_verb():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "--help"])
    # Assert
    assert "channel" in result.output


def test_mcp_channel_help_exits_zero():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "channel", "--help"])
    # Assert
    assert result.exit_code == 0, result.output


def test_mcp_channel_help_advertises_name_option():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "channel", "--help"])
    # Assert
    assert "--name" in result.output


def test_mcp_channel_help_advertises_interval_option():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "channel", "--help"])
    # Assert
    assert "--interval" in result.output


def test_mcp_channel_unresolved_agent_exits_nonzero(env):
    # Arrange — no agent id in the env and none passed → fail loud rather than
    # draining an "unknown" inbox.
    env.delete("SCITEX_TODO_AGENT_ID")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "channel", "--interval", "0.01"])
    # Assert
    assert result.exit_code != 0


def test_mcp_channel_unresolved_agent_names_the_env_var(env):
    # Arrange — the failure must carry an actionable hint.
    env.delete("SCITEX_TODO_AGENT_ID")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "channel", "--interval", "0.01"])
    # Assert
    assert "SCITEX_TODO_AGENT_ID" in result.output


# EOF
