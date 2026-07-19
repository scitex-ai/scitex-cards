#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards install-stop-hook`` — the wiring step, made not-manual.

Registration was the last hand step between a merged Stop hook and one that
runs. The operator asked whether an agent holding cards could still stop; it
could, because the hook was registered in no settings.json at all. A manual
step is a step that silently does not happen.

Pinned here, against real files and no mocks:
* dry-run is the DEFAULT and writes nothing;
* --apply writes, backs up first, and VERIFIES by read-back;
* idempotent — a second run adds no duplicate;
* unrelated settings (other hooks, permissions, env) survive untouched;
* a corrupt settings.json is REFUSED rather than overwritten.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli._install_stop_hook import install_stop_hook_cmd

_CMD = "scitex-cards stop-hook"


@pytest.fixture()
def settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "env": {"KEEP_ME": "1"},
                "permissions": {"allow": ["Bash(ls:*)"]},
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "other-thing.sh"}]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _run(settings, *args):
    return CliRunner().invoke(
        install_stop_hook_cmd, ["--settings", str(settings), *args]
    )


def _commands(settings):
    data = json.loads(settings.read_text(encoding="utf-8"))
    return [
        h.get("command")
        for g in data.get("hooks", {}).get("Stop", [])
        for h in g.get("hooks", [])
    ]


def _run_on_missing_settings(tmp_path):
    """Apply against a settings path whose parent dir does not exist yet."""
    path = tmp_path / "nested" / "settings.json"
    result = CliRunner().invoke(
        install_stop_hook_cmd, ["--settings", str(path), "--apply"]
    )
    return path, result


def _run_on_corrupt_settings(tmp_path):
    """Apply against an unparseable settings.json."""
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    result = CliRunner().invoke(
        install_stop_hook_cmd, ["--settings", str(path), "--apply"]
    )
    return path, result


def test_dry_run_is_the_default_and_exits_zero(settings):
    # Arrange
    target = settings
    # Act
    result = _run(target)
    # Assert
    assert result.exit_code == 0


def test_dry_run_announces_itself_on_stdout(settings):
    # Arrange
    target = settings
    # Act
    result = _run(target)
    # Assert — it REPORTS; a mutation must be asked for.
    assert "DRY RUN" in result.stdout


def test_dry_run_is_the_default_and_writes_nothing(settings):
    # Arrange
    before = settings.read_text(encoding="utf-8")
    # Act
    _run(settings)
    # Assert
    assert settings.read_text(encoding="utf-8") == before


def test_apply_exits_zero(settings):
    # Arrange
    target = settings
    # Act
    result = _run(target, "--apply")
    # Assert
    assert result.exit_code == 0


def test_apply_registers_the_hook(settings):
    # Arrange
    target = settings
    # Act
    _run(target, "--apply")
    # Assert
    assert _CMD in _commands(target)


def test_apply_reports_the_read_back_verification(settings):
    # Arrange
    target = settings
    # Act
    result = _run(target, "--apply")
    # Assert
    assert "verified by read-back" in result.stdout


def test_apply_keeps_the_env_block_intact(settings):
    """The file belongs to the user; we add one entry and touch nothing else."""
    # Arrange
    target = settings
    # Act
    _run(target, "--apply")
    data = json.loads(target.read_text(encoding="utf-8"))
    # Assert
    assert data["env"] == {"KEEP_ME": "1"}


def test_apply_keeps_the_permissions_block_intact(settings):
    """The file belongs to the user; we add one entry and touch nothing else."""
    # Arrange
    target = settings
    # Act
    _run(target, "--apply")
    data = json.loads(target.read_text(encoding="utf-8"))
    # Assert
    assert data["permissions"] == {"allow": ["Bash(ls:*)"]}


def test_apply_keeps_the_pre_existing_stop_hook(settings):
    """The file belongs to the user; we add one entry and touch nothing else."""
    # Arrange
    target = settings
    # Act
    _run(target, "--apply")
    # Assert
    assert "other-thing.sh" in _commands(target)


def test_a_second_apply_exits_zero(settings):
    """Re-running must not fail — this gets run by provisioning."""
    # Arrange
    _run(settings, "--apply")
    # Act
    second = _run(settings, "--apply")
    # Assert
    assert second.exit_code == 0


def test_a_second_apply_reports_already_registered(settings):
    """Re-running says so rather than pretending it did new work."""
    # Arrange
    _run(settings, "--apply")
    # Act
    second = _run(settings, "--apply")
    # Assert
    assert "already registered" in second.stdout


def test_a_second_apply_adds_no_duplicate_hook(settings):
    """Re-running must not stack duplicate hooks."""
    # Arrange
    _run(settings, "--apply")
    # Act
    _run(settings, "--apply")
    # Assert
    assert _commands(settings).count(_CMD) == 1


def test_apply_leaves_exactly_one_backup(settings, tmp_path):
    # Arrange
    target = settings
    # Act
    _run(target, "--apply")
    backups = list(tmp_path.glob("settings.json.bak-*"))
    # Assert
    assert len(backups) == 1


def test_the_backup_holds_the_previous_content(settings, tmp_path):
    # Arrange
    target = settings
    # Act
    _run(target, "--apply")
    backups = list(tmp_path.glob("settings.json.bak-*"))
    # Assert — the previous file survives a bad edit.
    assert "other-thing.sh" in backups[0].read_text(encoding="utf-8")


def test_a_missing_settings_file_apply_exits_zero(tmp_path):
    # Arrange
    root = tmp_path
    # Act
    _path, result = _run_on_missing_settings(root)
    # Assert
    assert result.exit_code == 0


def test_a_missing_settings_file_is_created(tmp_path):
    # Arrange
    root = tmp_path
    # Act
    path, _result = _run_on_missing_settings(root)
    # Assert
    assert _CMD in _commands(path)


def test_corrupt_settings_apply_exits_nonzero(tmp_path):
    """FAIL LOUD rather than silently replacing an unparseable config."""
    # Arrange
    root = tmp_path
    # Act
    _path, result = _run_on_corrupt_settings(root)
    # Assert
    assert result.exit_code != 0


def test_corrupt_settings_is_refused_not_overwritten(tmp_path):
    """Silently replacing an unparseable config would destroy it."""
    # Arrange
    root = tmp_path
    # Act
    path, _result = _run_on_corrupt_settings(root)
    # Assert — the original bytes are still there.
    assert path.read_text(encoding="utf-8") == "{not json"


def test_a_custom_command_can_be_pinned(settings):
    """Hosts that need an absolute venv path must not have to hand-edit."""
    # Arrange
    target = settings
    # Act
    _run(target, "--command", "/opt/venv/bin/scitex-cards stop-hook", "--apply")
    # Assert
    assert "/opt/venv/bin/scitex-cards stop-hook" in _commands(target)


# EOF
