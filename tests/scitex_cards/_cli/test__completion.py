#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the §1a shell-completion commands (no mocks; CliRunner)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main


@pytest.fixture
def runner() -> CliRunner:
    """Fresh CliRunner per test."""
    return CliRunner()


def test_print_shell_completion_emits_bash_function(runner: CliRunner):
    # Arrange
    # Act
    result = runner.invoke(main, ["print-shell-completion", "--shell", "bash"])
    # Assert
    assert "_scitex_cards_completion" in result.output


def test_install_shell_completion_dry_run_changes_nothing(runner: CliRunner):
    # Arrange
    # Act
    result = runner.invoke(main, ["install-shell-completion", "--dry-run"])
    # Assert
    assert "[dry-run]" in result.output


def test_install_shell_completion_dry_run_exits_zero(runner: CliRunner):
    # Arrange
    # Act
    result = runner.invoke(main, ["install-shell-completion", "--dry-run"])
    # Assert
    assert result.exit_code == 0


def test_install_writes_drop_in_file(runner: CliRunner, tmp_path):
    # Arrange
    env = {"SCITEX_DIR": str(tmp_path)}
    # Act
    result = runner.invoke(
        main, ["install-shell-completion", "--shell", "bash"], env=env
    )
    # Assert
    assert result.exit_code == 0


def test_install_drop_in_file_has_completion_content(runner: CliRunner, tmp_path):
    # Arrange
    env = {"SCITEX_DIR": str(tmp_path)}
    runner.invoke(main, ["install-shell-completion", "--shell", "bash"], env=env)
    target = tmp_path / "cards" / "runtime" / "completion" / "scitex-cards"
    # Act
    content = target.read_text(encoding="utf-8")
    # Assert
    assert "_scitex_cards_completion" in content


def test_install_prints_drop_in_path(runner: CliRunner, tmp_path):
    # Arrange
    env = {"SCITEX_DIR": str(tmp_path)}
    # Act
    result = runner.invoke(
        main, ["install-shell-completion", "--shell", "bash"], env=env
    )
    # Assert
    target = tmp_path / "cards" / "runtime" / "completion" / "scitex-cards"
    assert str(target) in result.output


def test_install_is_idempotent_second_run_exits_zero(runner: CliRunner, tmp_path):
    # Arrange
    env = {"SCITEX_DIR": str(tmp_path)}
    first = runner.invoke(
        main, ["install-shell-completion", "--shell", "bash"], env=env
    )
    assert first.exit_code == 0
    # Act
    second = runner.invoke(
        main, ["install-shell-completion", "--shell", "bash"], env=env
    )
    # Assert
    assert second.exit_code == 0


def test_install_idempotent_second_run_keeps_bytes(runner: CliRunner, tmp_path):
    # Arrange
    env = {"SCITEX_DIR": str(tmp_path)}
    first = runner.invoke(
        main, ["install-shell-completion", "--shell", "bash"], env=env
    )
    assert first.exit_code == 0
    target = tmp_path / "cards" / "runtime" / "completion" / "scitex-cards"
    before = target.read_bytes()
    # Act
    runner.invoke(main, ["install-shell-completion", "--shell", "bash"], env=env)
    # Assert
    assert target.read_bytes() == before


def test_install_never_creates_bashrc(runner: CliRunner, tmp_path):
    # Arrange
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = {"HOME": str(fake_home), "SCITEX_DIR": str(tmp_path / "scitex")}
    # Act
    result = runner.invoke(
        main, ["install-shell-completion", "--shell", "bash"], env=env
    )
    assert result.exit_code == 0
    # Assert
    assert not (fake_home / ".bashrc").exists()


def test_install_never_creates_zshrc(runner: CliRunner, tmp_path):
    # Arrange
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = {"HOME": str(fake_home), "SCITEX_DIR": str(tmp_path / "scitex")}
    # Act
    result = runner.invoke(
        main, ["install-shell-completion", "--shell", "bash"], env=env
    )
    assert result.exit_code == 0
    # Assert
    assert not (fake_home / ".zshrc").exists()


def test_install_never_creates_fish_config(runner: CliRunner, tmp_path):
    # Arrange
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = {"HOME": str(fake_home), "SCITEX_DIR": str(tmp_path / "scitex")}
    # Act
    result = runner.invoke(
        main, ["install-shell-completion", "--shell", "bash"], env=env
    )
    assert result.exit_code == 0
    # Assert
    assert not (fake_home / ".config" / "fish" / "config.fish").exists()
