#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The MCP/CLI surface must ANNOUNCE the current name (`scitex-cards`).

The package was renamed `scitex-todo` -> `scitex-cards`, but the surface kept
introducing itself by the old name: the emitted `.mcp.json` key (which is the
namespace agents see their tools under -- `mcp__<key>__add_task`), the
`command` it execs, the install hint, and the `mcp doctor` payload.

These tests pin the DISTINCTION that makes the rename safe:

  * what the surface CALLS ITSELF must say `scitex-cards`;
  * what the surface PUBLISHES -- the `scitex-todo` console script, the
    `SCITEX_TODO_*` env vars, the systemd unit -- must keep working. Those are
    migrations, not renames, and are asserted NOT to have moved.

CliRunner against a tmp `.mcp.json`; no mocks (STX-NM / PA-306).
One assertion per test (TQ002 / TQ007).
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._mcp_install import (
    LEGACY_CLI_NAME,
    _is_our_entry,
    _retire_legacy_entry,
)


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _install_to(target):
    CliRunner().invoke(main, ["mcp", "install", "--apply", "--to", str(target), "-y"])
    return _read_json(target)


# === the emitted entry announces the current name ===========================


def test_install_snippet_key_is_scitex_cards():
    # Arrange
    runner = CliRunner()
    # Act — the KEY is the namespace agents see: mcp__<key>__add_task.
    payload = json.loads(runner.invoke(main, ["mcp", "install"]).output)
    # Assert
    assert "scitex-cards" in payload["mcpServers"]


def test_install_snippet_no_longer_emits_the_legacy_key():
    # Arrange
    runner = CliRunner()
    # Act
    payload = json.loads(runner.invoke(main, ["mcp", "install"]).output)
    # Assert
    assert LEGACY_CLI_NAME not in payload["mcpServers"]


def test_install_snippet_command_is_scitex_cards():
    # Arrange
    runner = CliRunner()
    # Act
    payload = json.loads(runner.invoke(main, ["mcp", "install"]).output)
    # Assert
    assert payload["mcpServers"]["scitex-cards"]["command"] == "scitex-cards"


def test_mcp_doctor_payload_names_scitex_cards():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "doctor", "--json"])
    # Assert
    assert json.loads(result.output)["package"] == "scitex-cards"


# === applying RETIRES our stale entry (no duplicate tool namespaces) ========
#
# Renaming the key we write is only half a migration. A config that already had
# `scitex-todo` would otherwise end up with BOTH keys pointing at the same
# server, and the agent would load two copies of every tool -- both writing the
# same store.


def test_apply_retires_our_legacy_entry(tmp_path):
    # Arrange — a config as an already-installed agent has it today.
    target = tmp_path / ".mcp.json"
    target.write_text(
        json.dumps(
            {
                "mcpServers": {
                    LEGACY_CLI_NAME: {
                        "command": "scitex-todo",
                        "args": ["mcp", "start"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    # Act
    servers = _install_to(target)["mcpServers"]
    # Assert
    assert LEGACY_CLI_NAME not in servers


def test_apply_leaves_exactly_one_of_our_entries(tmp_path):
    # Arrange
    target = tmp_path / ".mcp.json"
    target.write_text(
        json.dumps(
            {
                "mcpServers": {
                    LEGACY_CLI_NAME: {
                        "command": "scitex-todo",
                        "args": ["mcp", "start"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    # Act
    servers = _install_to(target)["mcpServers"]
    # Assert — one server, not two serving identical tools.
    assert list(servers) == ["scitex-cards"]


def test_apply_preserves_third_party_servers(tmp_path):
    # Arrange
    target = tmp_path / ".mcp.json"
    target.write_text(
        json.dumps({"mcpServers": {"other-server": {"command": "other", "args": []}}}),
        encoding="utf-8",
    )
    # Act
    servers = _install_to(target)["mcpServers"]
    # Assert
    assert servers["other-server"] == {"command": "other", "args": []}


def test_a_foreign_server_under_the_legacy_key_is_not_retired():
    # Arrange — someone else's server that merely shares the old key.
    servers = {LEGACY_CLI_NAME: {"command": "/usr/bin/unrelated", "args": ["serve"]}}
    # Act
    retired = _retire_legacy_entry(servers)
    # Assert — not ours, so we must not delete it.
    assert retired is False


def test_a_legacy_entry_execing_the_alias_is_recognised_as_ours():
    # Arrange — the `scitex-todo` console script stays installed forever, so a
    # legacy entry legitimately execs it.
    entry = {"command": "/opt/venv/bin/scitex-todo", "args": ["mcp", "start"]}
    # Act
    # Assert — matched by basename, so an absolute path still counts.
    assert _is_our_entry(entry) is True


# === the published contracts did NOT move ===================================


def test_legacy_console_script_is_still_declared():
    # Arrange — breaking this would kill the operator's running units, one of
    # which serves the board via the `scitex-todo` console script.
    from pathlib import Path

    import tomllib

    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    # Act
    scripts = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]
    # Assert
    assert "scitex-todo" in scripts


def test_both_console_scripts_resolve_to_the_same_entry_point():
    # Arrange
    from pathlib import Path

    import tomllib

    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    # Act
    scripts = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]
    # Assert
    assert scripts["scitex-todo"] == scripts["scitex-cards"]


# EOF
