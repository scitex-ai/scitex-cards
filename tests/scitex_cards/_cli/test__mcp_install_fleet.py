#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards mcp install-fleet`` — fleet-wide MCP wire-up.

Lead a2a `1ab212f3`, 2026-06-14 — closes the missing-MCP gap that
ripple-wm hit (had to a2a-relay through me for card creation because
their container's `.mcp.json` doesn't have the scitex-cards entry).
One invocation bakes the entry into every agent's `to_home/.mcp.json`.

No mocks (STX-NM/PA-306). Click CliRunner against a tmp directory
shaped like agent-container's fleet specs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_agents(root: Path, *names: str) -> None:
    """Create an agents-dir layout with each agent's to_home/ folder."""
    for n in names:
        (root / n / "to_home").mkdir(parents=True, exist_ok=True)


def test_install_fleet_requires_agents_dir(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "install-fleet"])
    # Assert — missing required option = click usage error (exit 2).
    assert result.exit_code == 2


def test_install_fleet_errors_on_nonexistent_agents_dir(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["mcp", "install-fleet", "--agents-dir", str(tmp_path / "missing"), "-y"],
    )
    # Assert
    assert result.exit_code != 0


def test_install_fleet_creates_mcp_json_when_absent(tmp_path):
    # Arrange — two agents with empty to_home dirs.
    agents = tmp_path / "agents"
    _make_agents(agents, "agent-a", "agent-b")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["mcp", "install-fleet", "--agents-dir", str(agents), "-y"],
    )
    # Assert
    assert result.exit_code == 0


def test_install_fleet_writes_scitex_cards_entry_per_agent(tmp_path):
    # Arrange
    agents = tmp_path / "agents"
    _make_agents(agents, "agent-a", "agent-b")
    runner = CliRunner()
    # Act
    runner.invoke(
        main, ["mcp", "install-fleet", "--agents-dir", str(agents), "-y"],
    )
    # Assert — both agents have the entry.
    a = _read_json(agents / "agent-a" / "to_home" / ".mcp.json")
    b = _read_json(agents / "agent-b" / "to_home" / ".mcp.json")
    assert (
        "scitex-cards" in a.get("mcpServers", {})
        and "scitex-cards" in b.get("mcpServers", {})
    )


def test_install_fleet_pins_env_tasks_path(tmp_path):
    # Arrange
    agents = tmp_path / "agents"
    _make_agents(agents, "agent-a")
    runner = CliRunner()
    pinned = "/home/agent/.scitex/cards/cards.db"
    # Act
    runner.invoke(
        main,
        ["mcp", "install-fleet", "--agents-dir", str(agents),
         "--env-tasks-path", pinned, "-y"],
    )
    # Assert
    entry = _read_json(agents / "agent-a" / "to_home" / ".mcp.json")["mcpServers"]["scitex-cards"]
    assert entry.get("env") == {"SCITEX_CARDS_DB": pinned}


def test_install_fleet_preserves_sibling_mcp_servers(tmp_path):
    # Arrange — pre-seed one agent's file with an unrelated MCP entry.
    agents = tmp_path / "agents"
    _make_agents(agents, "agent-a")
    seed = agents / "agent-a" / "to_home" / ".mcp.json"
    seed.write_text(json.dumps({
        "mcpServers": {"other-server": {"command": "other-bin"}}
    }), encoding="utf-8")
    runner = CliRunner()
    # Act
    runner.invoke(
        main, ["mcp", "install-fleet", "--agents-dir", str(agents), "-y"],
    )
    # Assert — sibling preserved.
    servers = _read_json(seed)["mcpServers"]
    assert "other-server" in servers and "scitex-cards" in servers


def test_install_fleet_idempotent_on_second_run(tmp_path):
    # Arrange
    agents = tmp_path / "agents"
    _make_agents(agents, "agent-a")
    runner = CliRunner()
    runner.invoke(
        main, ["mcp", "install-fleet", "--agents-dir", str(agents), "-y"],
    )
    # Act — second run.
    result = runner.invoke(
        main, ["mcp", "install-fleet", "--agents-dir", str(agents), "-y"],
    )
    # Assert — exit 0; the per-agent line marks the noop.
    assert "noop" in result.output


def test_install_fleet_dry_run_does_not_write(tmp_path):
    # Arrange
    agents = tmp_path / "agents"
    _make_agents(agents, "agent-a")
    target = agents / "agent-a" / "to_home" / ".mcp.json"
    runner = CliRunner()
    # Act
    runner.invoke(
        main,
        ["mcp", "install-fleet", "--agents-dir", str(agents), "--dry-run", "-y"],
    )
    # Assert
    assert not target.exists()


def test_install_fleet_dry_run_prints_planned_action(tmp_path):
    # Arrange
    agents = tmp_path / "agents"
    _make_agents(agents, "agent-a")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["mcp", "install-fleet", "--agents-dir", str(agents), "--dry-run", "-y"],
    )
    # Assert
    assert "dry-run" in result.output


def test_install_fleet_ignores_non_directory_entries(tmp_path):
    # Arrange — a stray file at the agents-dir root must not be
    # treated as an agent.
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "agent-a" / "to_home").mkdir(parents=True)
    (agents / "stray-file.txt").write_text("not an agent", encoding="utf-8")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["mcp", "install-fleet", "--agents-dir", str(agents), "-y"],
    )
    # Assert — exit 0 and stray didn't get .mcp.json.
    assert result.exit_code == 0


def test_install_fleet_summary_line_present(tmp_path):
    # Arrange
    agents = tmp_path / "agents"
    _make_agents(agents, "agent-a", "agent-b")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["mcp", "install-fleet", "--agents-dir", str(agents), "-y"],
    )
    # Assert
    assert "fleet sweep:" in result.output


def test_install_fleet_handles_corrupt_mcp_json_per_agent(tmp_path):
    # Arrange — agent-a has corrupt JSON; the sweep should skip and
    # still process agent-b cleanly.
    agents = tmp_path / "agents"
    _make_agents(agents, "agent-a", "agent-b")
    (agents / "agent-a" / "to_home" / ".mcp.json").write_text(
        "{ not valid json", encoding="utf-8",
    )
    runner = CliRunner()
    # Act
    runner.invoke(
        main, ["mcp", "install-fleet", "--agents-dir", str(agents), "-y"],
    )
    # Assert — agent-b still got the entry.
    assert "scitex-cards" in _read_json(agents / "agent-b" / "to_home" / ".mcp.json").get("mcpServers", {})
