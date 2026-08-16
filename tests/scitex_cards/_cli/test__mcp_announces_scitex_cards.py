#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The MCP/CLI surface must ANNOUNCE the current name (`scitex-cards`).

Every surface an agent reads -- the emitted `.mcp.json` key (the namespace
tools appear under, `mcp__<key>__add_task`), the `command` it execs, the
install hint and the `mcp doctor` payload -- must say `scitex-cards`.

THIS FILE USED TO PIN A DISTINCTION THAT NO LONGER EXISTS: what the surface
CALLS itself versus what it PUBLISHES, because the retired name stayed a
working alias. The operator retired that name outright on 2026-08-16, so the
second half is gone and the tests that asserted it are deleted rather than
rewritten to compare the current name against itself.

CliRunner against a tmp `.mcp.json`; no mocks (STX-NM / PA-306).
One assertion per test (TQ002 / TQ007).
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._mcp_install import _is_our_entry


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


# === existing config is merged, not clobbered ==============================




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



def test_an_entry_execing_an_absolute_path_is_recognised_as_ours():
    # Arrange — an installed entry names the binary by absolute path.
    entry = {"command": "/opt/venv/bin/scitex-cards", "args": ["mcp", "start"]}
    # Act
    # Assert — matched by basename, so an absolute path still counts.
    assert _is_our_entry(entry) is True


# === the console-script surface =============================================


def test_the_canonical_console_script_is_declared():
    # Arrange
    from pathlib import Path

    import tomllib

    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    # Act
    scripts = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]
    # Assert
    assert "scitex-cards" in scripts



# EOF
