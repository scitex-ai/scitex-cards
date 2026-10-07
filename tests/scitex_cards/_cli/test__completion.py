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


def _install(runner: CliRunner, tmp_path, extra_env=None):
    """Run install-shell-completion with an isolated SCITEX_DIR."""
    env = {"SCITEX_DIR": str(tmp_path)}
    if extra_env:
        env.update(extra_env)
    return runner.invoke(
        main, ["install-shell-completion", "--shell", "bash"], env=env
    )


def test_print_shell_completion_emits_bash_function(runner: CliRunner):
    result = runner.invoke(main, ["print-shell-completion", "--shell", "bash"])
    assert "_scitex_cards_completion" in result.output


def test_install_shell_completion_dry_run_changes_nothing(runner: CliRunner):
    result = runner.invoke(main, ["install-shell-completion", "--dry-run"])
    assert "[dry-run]" in result.output


def test_install_shell_completion_dry_run_exits_zero(runner: CliRunner):
    result = runner.invoke(main, ["install-shell-completion", "--dry-run"])
    assert result.exit_code == 0


def test_install_writes_drop_in_file(runner: CliRunner, tmp_path):
    _install(runner, tmp_path)
    target = tmp_path / "cards" / "runtime" / "completion" / "scitex-cards"
    assert target.is_file()


def test_install_drop_in_file_has_completion_content(runner: CliRunner, tmp_path):
    _install(runner, tmp_path)
    target = tmp_path / "cards" / "runtime" / "completion" / "scitex-cards"
    assert "_scitex_cards_completion" in target.read_text(encoding="utf-8")


def test_install_prints_drop_in_path(runner: CliRunner, tmp_path):
    result = _install(runner, tmp_path)
    target = tmp_path / "cards" / "runtime" / "completion" / "scitex-cards"
    assert str(target) in result.output


def test_install_exits_zero(runner: CliRunner, tmp_path):
    assert _install(runner, tmp_path).exit_code == 0


def test_install_second_run_exits_zero(runner: CliRunner, tmp_path):
    _install(runner, tmp_path)
    assert _install(runner, tmp_path).exit_code == 0


def test_install_second_run_keeps_bytes(runner: CliRunner, tmp_path):
    _install(runner, tmp_path)
    target = tmp_path / "cards" / "runtime" / "completion" / "scitex-cards"
    before = target.read_bytes()
    _install(runner, tmp_path)
    assert target.read_bytes() == before


def test_install_runs_against_fake_home(runner: CliRunner, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    result = runner.invoke(
        main,
        ["install-shell-completion", "--shell", "bash"],
        env={
            "HOME": str(fake_home),
            "SCITEX_DIR": str(tmp_path / "scitex"),
        },
    )
    assert result.exit_code == 0


def test_install_never_creates_bashrc(runner: CliRunner, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    runner.invoke(
        main,
        ["install-shell-completion", "--shell", "bash"],
        env={
            "HOME": str(fake_home),
            "SCITEX_DIR": str(tmp_path / "scitex"),
        },
    )
    assert not (fake_home / ".bashrc").exists()


def test_install_never_creates_zshrc(runner: CliRunner, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    runner.invoke(
        main,
        ["install-shell-completion", "--shell", "bash"],
        env={
            "HOME": str(fake_home),
            "SCITEX_DIR": str(tmp_path / "scitex"),
        },
    )
    assert not (fake_home / ".zshrc").exists()


def test_install_never_creates_fish_config(runner: CliRunner, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    runner.invoke(
        main,
        ["install-shell-completion", "--shell", "bash"],
        env={
            "HOME": str(fake_home),
            "SCITEX_DIR": str(tmp_path / "scitex"),
        },
    )
    assert not (fake_home / ".config" / "fish" / "config.fish").exists()
