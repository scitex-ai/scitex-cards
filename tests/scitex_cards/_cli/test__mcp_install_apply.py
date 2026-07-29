#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for `scitex-cards mcp install --apply` (fleet P3a).

The print-only path is already covered elsewhere; this module pins
the new --apply behavior. CliRunner against a tmp ``.mcp.json`` (no
mocks — STX-NM / PA-306). One assertion per test (TQ002 / TQ007).
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


# === apply creates target when absent =======================================


def test_apply_creates_file_when_absent(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    # Act
    result = runner.invoke(
        main, ["mcp", "install", "--apply", "--to", str(target), "-y"]
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_apply_writes_scitex_cards_entry(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    runner.invoke(main, ["mcp", "install", "--apply", "--to", str(target), "-y"])
    # Act
    data = _read_json(target)
    # Assert
    assert "scitex-cards" in data.get("mcpServers", {})


def test_apply_writes_correct_command_args(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    runner.invoke(main, ["mcp", "install", "--apply", "--to", str(target), "-y"])
    # Act
    entry = _read_json(target)["mcpServers"]["scitex-cards"]
    # Assert
    assert entry == {"command": "scitex-cards", "args": ["mcp", "start"]}


# === --env-tasks-path pins the store path (P3a host-store wire-up) ==========
#
# When the fleet operator (typically agent-container at to_home/.mcp.json
# generation time) passes --env-tasks-path, the MCP entry gets an `env`
# block with SCITEX_CARDS_DB pinned to that absolute path. This makes the
# wire-up self-documenting in the generated config AND immune to $HOME /
# symlink drift in any container that loads the .mcp.json.


def test_apply_env_tasks_path_pins_env_block(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    pinned = "/home/agent/.scitex/cards/cards.db"
    # Act
    runner.invoke(
        main,
        [
            "mcp",
            "install",
            "--apply",
            "--to",
            str(target),
            "--env-tasks-path",
            pinned,
            "-y",
        ],
    )
    # Assert
    entry = _read_json(target)["mcpServers"]["scitex-cards"]
    assert entry.get("env") == {"SCITEX_CARDS_DB": pinned}


def test_apply_env_tasks_path_preserves_command_args(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    pinned = "/home/agent/.scitex/cards/cards.db"
    # Act
    runner.invoke(
        main,
        [
            "mcp",
            "install",
            "--apply",
            "--to",
            str(target),
            "--env-tasks-path",
            pinned,
            "-y",
        ],
    )
    # Assert — command + args still present alongside the new env block.
    entry = _read_json(target)["mcpServers"]["scitex-cards"]
    assert entry["command"] == "scitex-cards" and entry["args"] == ["mcp", "start"]


def test_apply_without_env_tasks_path_omits_env_block(tmp_path):
    # Arrange — back-compat default: no env block when the flag is absent.
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    # Act
    runner.invoke(main, ["mcp", "install", "--apply", "--to", str(target), "-y"])
    # Assert
    entry = _read_json(target)["mcpServers"]["scitex-cards"]
    assert "env" not in entry


def test_apply_env_tasks_path_idempotent_when_repeated(tmp_path):
    # Arrange — applying twice with the same pin is a noop the second time.
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    pinned = "/home/agent/.scitex/cards/cards.db"
    args = [
        "mcp",
        "install",
        "--apply",
        "--to",
        str(target),
        "--env-tasks-path",
        pinned,
        "-y",
    ]
    runner.invoke(main, args)
    # Act
    result = runner.invoke(main, args)
    # Assert
    assert "noop" in result.output


def test_apply_env_tasks_path_updates_when_pin_changes(tmp_path):
    # Arrange — repinning to a new path overwrites the env block in place
    # (not "noop"); idempotency is keyed on the full entry shape.
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    runner.invoke(
        main,
        [
            "mcp",
            "install",
            "--apply",
            "--to",
            str(target),
            "--env-tasks-path",
            "/old/tasks.yaml",
            "-y",
        ],
    )
    # Act
    runner.invoke(
        main,
        [
            "mcp",
            "install",
            "--apply",
            "--to",
            str(target),
            "--env-tasks-path",
            "/new/cards.db",
            "-y",
        ],
    )
    # Assert
    entry = _read_json(target)["mcpServers"]["scitex-cards"]
    assert entry["env"]["SCITEX_CARDS_DB"] == "/new/cards.db"


def test_print_only_env_tasks_path_emits_env_block(tmp_path):
    # Arrange — the print path (no --apply) honours the same flag so a
    # user can preview the pinned snippet before writing.
    runner = CliRunner()
    pinned = "/home/agent/.scitex/cards/cards.db"
    # Act
    result = runner.invoke(
        main, ["mcp", "install", "--env-tasks-path", pinned]
    )
    # Assert
    assert pinned in result.output


# === apply preserves sibling entries (fleet-friendly) =======================


def test_apply_preserves_sibling_server_entry(tmp_path):
    # Arrange
    target = tmp_path / ".mcp.json"
    target.write_text(
        json.dumps({"mcpServers": {"other": {"command": "other-bin"}}}),
        encoding="utf-8",
    )
    runner = CliRunner()
    # Act
    runner.invoke(main, ["mcp", "install", "--apply", "--to", str(target), "-y"])
    # Assert
    assert "other" in _read_json(target)["mcpServers"]


# === idempotence ============================================================


def test_apply_twice_is_idempotent(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    runner.invoke(main, ["mcp", "install", "--apply", "--to", str(target), "-y"])
    # Act
    result = runner.invoke(
        main, ["mcp", "install", "--apply", "--to", str(target), "-y"]
    )
    # Assert
    assert "noop" in result.output


# === dry-run does NOT write =================================================


def test_apply_dry_run_does_not_create_file(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    # Act
    runner.invoke(
        main,
        ["mcp", "install", "--apply", "--to", str(target), "--dry-run"],
    )
    # Assert
    assert not target.exists()


def test_apply_dry_run_prints_action_marker(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    # Act
    result = runner.invoke(
        main,
        ["mcp", "install", "--apply", "--to", str(target), "--dry-run"],
    )
    # Assert
    assert "dry-run" in result.output


# === backup on overwrite ====================================================


def test_apply_backs_up_existing_file(tmp_path):
    # Arrange
    target = tmp_path / ".mcp.json"
    target.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8"
    )
    runner = CliRunner()
    # Act
    runner.invoke(main, ["mcp", "install", "--apply", "--to", str(target), "-y"])
    # Assert
    assert (tmp_path / ".mcp.json.bak").exists()


# === bad target file =======================================================


def test_apply_fails_on_invalid_json_target(tmp_path):
    # Arrange
    target = tmp_path / ".mcp.json"
    target.write_text("{ not valid json", encoding="utf-8")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["mcp", "install", "--apply", "--to", str(target), "-y"]
    )
    # Assert
    assert result.exit_code != 0


def test_apply_fails_on_non_object_root(tmp_path):
    # Arrange
    target = tmp_path / ".mcp.json"
    target.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["mcp", "install", "--apply", "--to", str(target), "-y"]
    )
    # Assert
    assert result.exit_code != 0


# === print-only path back-compat ===========================================


def test_print_only_still_emits_snippet(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "install"])
    # Assert
    assert "scitex-cards" in result.output


# EOF
