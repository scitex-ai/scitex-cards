#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the §1a shell-completion commands (no mocks; CliRunner)."""

from __future__ import annotations

from click.testing import CliRunner

from scitex_cards._cli import main


def test_print_shell_completion_emits_bash_function():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["print-shell-completion", "--shell", "bash"])
    # Assert
    assert "_scitex_cards_completion" in result.output


def test_install_shell_completion_dry_run_changes_nothing():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["install-shell-completion", "--dry-run"])
    # Assert
    assert "[dry-run]" in result.output


def test_install_shell_completion_dry_run_exits_zero():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["install-shell-completion", "--dry-run"])
    # Assert
    assert result.exit_code == 0


def test_install_writes_drop_in_file_and_prints_path(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setenv("SCITEX_DIR", str(tmp_path))
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["install-shell-completion", "--shell", "bash"])
    # Assert
    assert result.exit_code == 0
    target = tmp_path / "cards" / "runtime" / "completion" / "scitex-cards"
    assert target.is_file()
    assert "_scitex_cards_completion" in target.read_text(encoding="utf-8")
    assert str(target) in result.output


def test_install_is_idempotent_second_run_changes_nothing(
    tmp_path, monkeypatch
):
    # Arrange
    monkeypatch.setenv("SCITEX_DIR", str(tmp_path))
    runner = CliRunner()
    first = runner.invoke(main, ["install-shell-completion", "--shell", "bash"])
    assert first.exit_code == 0
    target = tmp_path / "cards" / "runtime" / "completion" / "scitex-cards"
    before = target.read_bytes()
    # Act
    second = runner.invoke(main, ["install-shell-completion", "--shell", "bash"])
    # Assert
    assert second.exit_code == 0
    assert target.read_bytes() == before
    assert str(target) in second.output


def test_install_never_touches_shell_startup_files(tmp_path, monkeypatch):
    # Arrange
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("SCITEX_DIR", str(tmp_path / "scitex"))
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["install-shell-completion", "--shell", "bash"])
    # Assert
    assert result.exit_code == 0
    assert not (fake_home / ".bashrc").exists()
    assert not (fake_home / ".zshrc").exists()
    assert not (fake_home / ".config" / "fish" / "config.fish").exists()
