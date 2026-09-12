#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The stdio tools-only mode disables polling without erasing identity."""

import os

from click.testing import CliRunner

from scitex_cards._cli import main


def test_start_help_exposes_stdio_tools_only_mode():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "start", "--help"])
    # Assert
    assert result.exit_code == 0 and "--tools-only" in result.output


def test_tools_only_dry_run_preserves_agent_identity(env):
    # Arrange
    identity = "agent:hermes"
    env.set("SCITEX_CARDS_AGENT_ID", identity)
    # Act
    result = CliRunner().invoke(
        main, ["mcp", "start", "--tools-only", "--dry-run"]
    )
    # Assert
    assert (
        result.exit_code,
        "tools only" in result.output,
        os.environ["SCITEX_CARDS_AGENT_ID"],
    ) == (0, True, identity)


# EOF
