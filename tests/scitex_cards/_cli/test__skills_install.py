#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `scitex-cards skills install` verb.

The existing `test__skills.py` covers `list` and `get`; `install`
was untested. This file fills the gap (lead a2a `1397f103` —
quality work between PR done-record re-sweeps).

Real `tmp_path` destination + `os.symlink` (POSIX-only — Linux
ci-runner is the target). No mocks (STX-NM / PA-306). Each test
points `--dest` at a tmp dir so the install never touches the
operator's real `~/.scitex/dev/skills/`.
"""

from __future__ import annotations

import os
import sys

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._skills import _SKILLS_PKG

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="install relies on POSIX symlinks",
)


class TestInstallDryRun:
    """`--dry-run` previews the planned action without touching disk."""

    def test_dry_run_exits_zero(self, tmp_path):
        # Arrange
        runner = CliRunner()
        # Act
        result = runner.invoke(
            main,
            [
                "skills",
                "install",
                "--dest",
                str(tmp_path),
                "--dry-run",
            ],
        )
        # Assert
        assert result.exit_code == 0

    def test_dry_run_mentions_symlink_action(self, tmp_path):
        # Arrange
        runner = CliRunner()
        # Act
        result = runner.invoke(
            main,
            [
                "skills",
                "install",
                "--dest",
                str(tmp_path),
                "--dry-run",
            ],
        )
        # Assert — default mode is symlink.
        assert "symlink" in result.output

    def test_dry_run_with_no_link_mentions_copy(self, tmp_path):
        # Arrange
        runner = CliRunner()
        # Act
        result = runner.invoke(
            main,
            [
                "skills",
                "install",
                "--dest",
                str(tmp_path),
                "--no-link",
                "--dry-run",
            ],
        )
        # Assert
        assert "copy" in result.output

    def test_dry_run_does_not_create_target(self, tmp_path):
        # Arrange
        runner = CliRunner()
        # Act
        runner.invoke(
            main,
            [
                "skills",
                "install",
                "--dest",
                str(tmp_path),
                "--dry-run",
            ],
        )
        # Assert — destination dir is unchanged.
        assert not (tmp_path / _SKILLS_PKG).exists()


class TestInstallSymlink:
    """Default mode: symlink the bundled skills root into <dest>/<skills-pkg>."""

    def test_symlink_target_exists_after_install(self, tmp_path):
        # Arrange
        runner = CliRunner()
        # Act
        runner.invoke(
            main,
            ["skills", "install", "--dest", str(tmp_path)],
        )
        # Assert
        assert (tmp_path / _SKILLS_PKG).is_symlink()

    def test_symlink_resolves_to_bundled_skills(self, tmp_path):
        # Arrange
        runner = CliRunner()
        # Act
        runner.invoke(
            main,
            ["skills", "install", "--dest", str(tmp_path)],
        )
        target = tmp_path / _SKILLS_PKG
        # Assert — the symlink points at the package's own `_skills/<pkg>/`.
        assert target.resolve().name == _SKILLS_PKG

    def test_install_is_idempotent(self, tmp_path):
        # Arrange — install twice. The second run must replace the
        # existing symlink, not error.
        runner = CliRunner()
        runner.invoke(
            main,
            ["skills", "install", "--dest", str(tmp_path)],
        )
        # Act
        result = runner.invoke(
            main,
            ["skills", "install", "--dest", str(tmp_path)],
        )
        # Assert
        assert result.exit_code == 0


class TestInstallNoLink:
    """`--no-link` copies the tree instead of symlinking."""

    def test_no_link_target_is_real_dir(self, tmp_path):
        # Arrange
        runner = CliRunner()
        # Act
        runner.invoke(
            main,
            [
                "skills",
                "install",
                "--dest",
                str(tmp_path),
                "--no-link",
            ],
        )
        target = tmp_path / _SKILLS_PKG
        # Assert
        assert target.is_dir() and not target.is_symlink()

    def test_no_link_contains_quick_start(self, tmp_path):
        # Arrange
        runner = CliRunner()
        # Act
        runner.invoke(
            main,
            [
                "skills",
                "install",
                "--dest",
                str(tmp_path),
                "--no-link",
            ],
        )
        # Assert — the copied tree carries the canonical 02_quick-start.md.
        assert any(
            p.name == "02_quick-start.md"
            for p in (tmp_path / _SKILLS_PKG).rglob("*.md")
        )


def test_the_bundled_skills_directory_is_named_for_this_package():
    """The ONE place the skills directory name is spelled out.

    Every other assertion derives it from ``_SKILLS_PKG``, so the name lives in
    exactly one test instead of a dozen string literals. When it moved from
    ``scitex-todo`` to ``scitex-cards`` (2026-07-20, operator directive: stop
    using the todo paths), the hardcoded copies all failed at once and said
    nothing about which was the contract and which was an echo of it.

    Pinned because it IS a contract — other packages resolve this path on disk.
    """
    from scitex_cards._cli._skills import _skills_root

    assert _SKILLS_PKG == "scitex-cards"
    assert _skills_root().is_dir(), f"bundled skills missing at {_skills_root()}"
