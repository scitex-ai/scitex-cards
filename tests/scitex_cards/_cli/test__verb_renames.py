#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slice-6b verb-rename pilot — canonical names, warn-phase aliases, help.

Covers the CLI-standardization doctrine (scitex-dev general/03_interface/
02_cli):

* §1d grammar — renamed verbs work under their canonical, VERB-FIRST
  kebab names (`list-stale`, `find-card`, `watch-ci`).
* §5 deprecation ladder Phase W — the old names (`stale-list`,
  `resolve-card`, `ci-watch`) forward to the canonical command, exit 0,
  print the doctrine-format warning to stderr once per shell session,
  and are hidden from `--help`.
* §4a categories — the root help renders the fixed ordered headers and
  the `Other` catch-all stays empty.
* §1d terminal verbs — `done` (success) and `close --reason`
  (non-success) stay canonical.

No mocks; CliRunner only. The live shared store is NEVER touched:
``tests/conftest.py`` bootstraps an empty per-test store and pins every
store-selecting env var at it, so the CLI resolves the throwaway store
without any test having to name it (the alias/CI tests additionally pin a
tmp HOME / XDG_RUNTIME_DIR).
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main

_OLD_TO_NEW = {
    "stale-list": "list-stale",
    "resolve-card": "find-card",
    "ci-watch": "watch-ci",
}


@pytest.fixture
def runner():
    """Fresh CliRunner per test."""
    return CliRunner()


@pytest.fixture
def store():
    """Seed two small cards into the harness store — never the live one.

    Seed RAW cards (no ``created_at`` / ``last_activity``) straight into the
    canonical DB, exactly as the old raw-YAML fixture did. The ``list-stale``
    criteria flag a deferred card with no timestamps, so the seed MUST leave
    the stamps unset; ``add_task`` would auto-stamp both (ADR-0008 D11) and
    defeat that premise, so seed via the doc instead.
    """
    from conftest import seed_db_from_doc

    doc = {
        "tasks": [
            {
                "id": "design",
                "title": "Design the thing",
                "status": "deferred",
                "repo": "owner/repo",
            },
            {"id": "build", "title": "Build the thing", "status": "deferred"},
        ]
    }
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])


@pytest.fixture
def warn_markers_isolated(env, tmp_path):
    """Point the once-per-shell warn-marker dir at tmp_path.

    The Phase-W warning is keyed by the parent shell PID via a marker
    file under ${XDG_RUNTIME_DIR:-/tmp}; without isolation a previous
    pytest run from the same shell would swallow the warning.
    """
    marker_dir = tmp_path / "xdg-runtime"
    marker_dir.mkdir()
    env.set("XDG_RUNTIME_DIR", str(marker_dir))


@pytest.fixture
def ci_env(env, tmp_path):
    """Empty fleet config under a tmp HOME; CI state cache in tmp."""
    env.set("SCITEX_TODO_CI_STATE", str(tmp_path / "ci-state.json"))
    env.set("HOME", str(tmp_path))
    env.delete("SCITEX_TODO_FLEET_CI_REPOS")


# === canonical names work ==================================================


@pytest.fixture
def list_stale_result(runner, store):
    return runner.invoke(main, ["list-stale"])


def test_list_stale_canonical_exits_zero(list_stale_result):
    # Arrange
    # Act
    result = list_stale_result
    # Assert
    assert result.exit_code == 0


def test_list_stale_canonical_reports_stale_deferred_card(list_stale_result):
    # Arrange
    # Act
    result = list_stale_result
    # Assert — the deferred card has no timestamps -> flagged stale.
    assert "design" in result.output


@pytest.fixture
def find_card_result(runner, store):
    return runner.invoke(main, ["find-card", "--repo", "owner/repo"])


def test_find_card_canonical_exits_zero(find_card_result):
    # Arrange
    # Act
    result = find_card_result
    # Assert
    assert result.exit_code == 0


def test_find_card_canonical_prints_only_matching_card_id(find_card_result):
    # Arrange
    # Act
    result = find_card_result
    # Assert — only the card with repo=owner/repo matches.
    assert result.stdout.split() == ["design"]


@pytest.fixture
def watch_ci_result(runner, ci_env):
    return runner.invoke(main, ["watch-ci", "--once", "--dry-run"])


def test_watch_ci_canonical_exits_zero(watch_ci_result):
    # Arrange
    # Act
    result = watch_ci_result
    # Assert
    assert result.exit_code == 0


def test_watch_ci_canonical_reports_sweep_summary(watch_ci_result):
    # Arrange
    # Act
    result = watch_ci_result
    # Assert — the bottom summary line uses the canonical verb.
    assert "watch-ci: repos=0" in result.output


# === old names: Phase-W warn + forward (doctrine §5) =======================


@pytest.fixture
def stale_list_alias_result(runner, store, warn_markers_isolated):
    return runner.invoke(main, ["stale-list"])


def test_stale_list_alias_exits_zero(stale_list_alias_result):
    # Arrange
    # Act
    result = stale_list_alias_result
    # Assert
    assert result.exit_code == 0


def test_stale_list_alias_forwards_to_canonical_output(stale_list_alias_result):
    # Arrange
    # Act
    result = stale_list_alias_result
    # Assert — same rows the canonical command prints.
    assert "design" in result.output


def test_stale_list_alias_warns_deprecated_on_stderr(stale_list_alias_result):
    # Arrange
    # Act
    result = stale_list_alias_result
    # Assert — doctrine §5 message shape, incl. the removal version.
    assert (
        "'stale-list' is deprecated — use 'list-stale' (removed in v0.9)"
        in result.stderr
    )


@pytest.fixture
def resolve_card_alias_result(runner, store, warn_markers_isolated):
    return runner.invoke(main, ["resolve-card", "--repo", "owner/repo"])


def test_resolve_card_alias_exits_zero(resolve_card_alias_result):
    # Arrange
    # Act
    result = resolve_card_alias_result
    # Assert
    assert result.exit_code == 0


def test_resolve_card_alias_forwards_options_to_find_card(
    resolve_card_alias_result,
):
    # Arrange
    # Act
    result = resolve_card_alias_result
    # Assert — --repo re-parsed through the canonical command (stdout
    # only; the deprecation warning goes to stderr).
    assert result.stdout.split() == ["design"]


def test_resolve_card_alias_warns_deprecated_on_stderr(resolve_card_alias_result):
    # Arrange
    # Act
    result = resolve_card_alias_result
    # Assert
    assert "'resolve-card' is deprecated — use 'find-card'" in result.stderr


@pytest.fixture
def ci_watch_alias_result(runner, ci_env, warn_markers_isolated):
    return runner.invoke(main, ["ci-watch", "--once", "--dry-run"])


def test_ci_watch_alias_exits_zero(ci_watch_alias_result):
    # Arrange
    # Act
    result = ci_watch_alias_result
    # Assert
    assert result.exit_code == 0


def test_ci_watch_alias_forwards_sweep_to_watch_ci(ci_watch_alias_result):
    # Arrange
    # Act
    result = ci_watch_alias_result
    # Assert
    assert "watch-ci: repos=0" in result.output


def test_ci_watch_alias_warns_deprecated_on_stderr(ci_watch_alias_result):
    # Arrange
    # Act
    result = ci_watch_alias_result
    # Assert
    assert "'ci-watch' is deprecated — use 'watch-ci'" in result.stderr


@pytest.fixture
def second_alias_result(runner, store, warn_markers_isolated):
    """Invoke the same alias twice within one (PPID-keyed) session."""
    runner.invoke(main, ["stale-list"])
    return runner.invoke(main, ["stale-list"])


def test_alias_warns_only_once_per_shell_session(second_alias_result):
    # Arrange
    # Act
    result = second_alias_result
    # Assert — the marker file swallows the second warning.
    assert "deprecated" not in result.stderr


def test_alias_second_invocation_still_forwards(second_alias_result):
    # Arrange
    # Act
    result = second_alias_result
    # Assert
    assert result.exit_code == 0


def test_aliases_carry_static_audit_metadata():
    # Arrange — slice-4 auditor contract: aliases are statically verifiable.
    expected = {
        old: {"target": new, "remove_in": "0.9", "phase": "warn"}
        for old, new in _OLD_TO_NEW.items()
    }
    # Act
    actual = {old: dict(main.commands[old]._deprecated_alias) for old in _OLD_TO_NEW}
    # Assert
    assert actual == expected


# === root help: §4a categories + hidden aliases ============================


@pytest.fixture
def root_help_result(runner):
    return runner.invoke(main, ["--help"])


def test_root_help_renders_category_headers_in_canonical_order(root_help_result):
    # Arrange
    headers = (
        "Core:",
        "Data & Sync:",
        "Service:",
        "Diagnostics:",
        "Introspection:",
        "Shell:",
    )
    # Act
    positions = [root_help_result.output.index(h) for h in headers]
    # Assert — every header present (index raises otherwise), in §4a order.
    assert positions == sorted(positions)


def test_root_help_has_empty_other_catch_all(root_help_result):
    # Arrange
    # Act
    output = root_help_result.output
    # Assert — a non-empty `Other` is an audit finding (doctrine §4a).
    assert "Other:" not in output


def test_root_help_lists_canonical_names(root_help_result):
    # Arrange
    # Act
    output = root_help_result.output
    # Assert
    assert all(new in output for new in _OLD_TO_NEW.values())


def test_root_help_hides_deprecated_aliases(root_help_result):
    # Arrange
    # Act
    output = root_help_result.output
    # Assert — Phase-W aliases are hidden=True.
    assert all(old not in output for old in _OLD_TO_NEW)


# === terminal verbs stay canonical (doctrine §1d) ==========================


def test_done_marks_task_done_in_tmp_store(runner, store):
    # Arrange
    args = ["done", "build"]
    # Act
    result = runner.invoke(main, args)
    # Assert
    assert "done build" in result.output


def test_close_with_reason_defers_task(runner, store):
    # Arrange
    args = ["close", "build", "--reason", "superseded"]
    # Act
    result = runner.invoke(main, args)
    # Assert
    assert "closed build" in result.output


def test_close_without_reason_is_usage_error(runner, store):
    # Arrange
    args = ["close", "design"]
    # Act
    result = runner.invoke(main, args)
    # Assert
    assert result.exit_code != 0


# EOF
