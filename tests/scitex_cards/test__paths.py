#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for task-store path resolution (no mocks; real env + tmp files)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scitex_cards._paths import (
    ENV_TASKS,
    ENV_TASKS_DEPRECATED,
    _find_git_root,
    bundled_example,
    resolve_tasks_path,
)


@pytest.fixture
def clean_tasks_env():
    """Save and restore the task-store env vars around a test.

    Clears BOTH the current ``$SCITEX_TODO_TASKS_YAML_SHARED`` and the
    deprecated ``$SCITEX_TODO_TASKS`` so a stale export in the ambient
    environment can't leak in and trip the fail-loud guard mid-test.
    """
    saved = {v: os.environ.get(v) for v in (ENV_TASKS, ENV_TASKS_DEPRECATED)}
    for v in (ENV_TASKS, ENV_TASKS_DEPRECATED):
        os.environ.pop(v, None)
    try:
        yield
    finally:
        for v, val in saved.items():
            if val is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = val


@pytest.fixture
def isolated_cwd(tmp_path):
    """Run from a fresh cwd with no .git and no reachable user-scope store.

    Real isolation (no mocks): chdir into a fresh tmp dir and point
    $SCITEX_DIR at an empty dir so neither project nor user scope exists.
    Restores both on teardown.
    """
    saved_cwd = Path.cwd()
    saved_scitex_dir = os.environ.get("SCITEX_DIR")
    work = tmp_path / "isolated"
    work.mkdir()
    os.chdir(work)
    os.environ["SCITEX_DIR"] = str(tmp_path / "empty-scitex")
    try:
        yield work
    finally:
        os.chdir(saved_cwd)
        if saved_scitex_dir is None:
            os.environ.pop("SCITEX_DIR", None)
        else:
            os.environ["SCITEX_DIR"] = saved_scitex_dir


def test_explicit_existing_path_wins_resolution(tmp_path, clean_tasks_env):
    # Arrange
    explicit = tmp_path / "mine.yaml"
    explicit.write_text("tasks: []\n", encoding="utf-8")
    # Act
    resolved = resolve_tasks_path(explicit)
    # Assert
    assert resolved == explicit


def test_env_var_path_resolves_when_no_explicit(tmp_path, clean_tasks_env):
    # Arrange
    target = tmp_path / "fromenv.yaml"
    target.write_text("tasks: []\n", encoding="utf-8")
    os.environ[ENV_TASKS] = str(target)
    # Act
    resolved = resolve_tasks_path(None)
    # Assert
    assert resolved == target


#: WHY the two `unresolvable_store` tests below are split but share this
#: rationale: there is no last resort, and that absence is the safety
#: property.
#:
#: These previously asserted the OPPOSITE — that resolution falls back to the
#: bundled example. That fallback made a packaged demo file eligible to become
#: the fleet's board: on 2026-07-19, with the canonical store archived by the
#: SQLite cutover, resolution walked past every real candidate, settled on a
#: file inside site-packages, and the live database's provenance stamp was
#: rewritten to name it.
#:
#: An unresolvable store now returns the canonical path that does not exist, so
#: the loader raises FileNotFoundError on it — a stated configuration error
#: rather than a blank board to start writing into. That is two claims: where
#: resolution must NOT land, and what it must return instead. Landing nowhere
#: useful still satisfies the first on its own.
def test_unresolvable_store_does_NOT_fall_back_to_a_packaged_fixture(
    clean_tasks_env, isolated_cwd
):
    # Arrange
    packaged_marker = "examples"
    # Act
    resolved = resolve_tasks_path(None)
    # Assert
    assert packaged_marker not in resolved.parts, (
        "resolution must never land on a packaged fixture — that is how demo "
        "data becomes production data"
    )


def test_unresolvable_store_returns_the_canonical_store_filename(
    clean_tasks_env, isolated_cwd
):
    # Arrange
    canonical_name = "tasks.yaml"
    # Act
    resolved = resolve_tasks_path(None)
    # Assert — the canonical path that does not exist, so the loader raises.
    assert resolved.name == canonical_name


def test_deprecated_env_var_fails_loud(monkeypatch, clean_tasks_env):
    """The renamed-away $SCITEX_TODO_TASKS must never be silently honoured when
    the current var is absent: with ONLY the old name set, resolution fails LOUD
    pointing at the new name so a stale export can't quietly pin the wrong
    store."""
    # Arrange: only the deprecated old name is set.
    monkeypatch.setenv(ENV_TASKS_DEPRECATED, "/some/legacy/tasks.yaml")
    # Act
    ctx = pytest.raises(RuntimeError, match=ENV_TASKS)
    # Assert — the raise points at the CURRENT name, not the stale one.
    with ctx:
        resolve_tasks_path(None)


def test_current_tasks_var_wins_over_stale_deprecated(
    monkeypatch, tmp_path, clean_tasks_env
):
    """The CURRENT $SCITEX_TODO_TASKS_YAML_SHARED wins: when it is set, a stale
    leftover $SCITEX_TODO_TASKS is IGNORED (warn, no raise) so a correctly
    configured store is not disabled by a leftover old-name export."""
    # Arrange: the current var points at a real store AND the stale old name is
    # also exported.
    target = tmp_path / "current.yaml"
    target.write_text("tasks: []\n", encoding="utf-8")
    monkeypatch.setenv(ENV_TASKS, str(target))
    monkeypatch.setenv(ENV_TASKS_DEPRECATED, "/some/legacy/tasks.yaml")
    # Act
    resolved = resolve_tasks_path(None)
    # Assert: the current var wins, no raise.
    assert resolved == target


def test_bundled_example_raises_because_no_yaml_store_ships_in_the_wheel():
    """Calling it is a stated error, not an AttributeError.

    This test previously asserted the fixture existed and parsed. The fixture
    and its fallback were removed on 2026-07-19; `bundled_example()` survives
    only as a raising stub so an external caller gets a reason rather than an
    import failure.
    """
    # Arrange
    expected_reason = "no bundled example"
    # Act
    ctx = pytest.raises(RuntimeError, match=expected_reason)
    # Assert — a stated reason, not an AttributeError.
    with ctx:
        bundled_example()


# === Additional gap-fill coverage (proj-scitex-todo overnight) ==============


def test_explicit_missing_path_returned_as_is(tmp_path, clean_tasks_env):
    """Explicit-but-missing path: function returns the Path verbatim so the
    LOADER raises the FNF with the user-supplied filename in the message —
    the docstring contract."""
    # Arrange
    explicit = tmp_path / "nope.yaml"  # never created
    # Act
    resolved = resolve_tasks_path(explicit)
    # Assert
    assert resolved == explicit


def test_explicit_path_string_is_expanded(tmp_path, clean_tasks_env):
    """Explicit path accepts a str, not just Path."""
    # Arrange
    target = tmp_path / "mine.yaml"
    target.write_text("tasks: []\n", encoding="utf-8")
    # Act
    resolved = resolve_tasks_path(str(target))
    # Assert
    assert resolved == target


#: WHY the two `user_scope_wins` tests below are split but share this
#: rationale: DATA store = USER-CANONICAL. Regression guard for the 2026-07-06
#: stale-store incident: even when cwd is inside a repo that HAS a
#: ``<git-root>/.scitex/todo/tasks.yaml``, resolution must reach the canonical
#: USER store — never the per-repo copy. The data store has DELIBERATELY no
#: project-scope layer (only the CONFIG in _config.py keeps its project
#: override). Reaching the right store and avoiding the shadow are asserted
#: separately because the incident was not "it picked nothing" — it was "it
#: picked the wrong real file", which only the second claim names.
@pytest.fixture()
def resolution_with_a_project_shadow_store(tmp_path, clean_tasks_env, env):
    """cwd inside a repo that HAS a shadow store, with a canonical user store."""
    # A fake project root with a real .git + a would-be shadow store.
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".git").mkdir()
    proj_store_dir = project / ".scitex" / "todo"
    proj_store_dir.mkdir(parents=True)
    proj_store = proj_store_dir / "tasks.yaml"
    proj_store.write_text("tasks: []\n", encoding="utf-8")
    # The canonical user-scope store the resolver MUST prefer.
    user_root = tmp_path / "user-scitex"
    user_dir = user_root / "todo"
    user_dir.mkdir(parents=True)
    user_store = user_dir / "tasks.yaml"
    user_store.write_text("tasks: []\n", encoding="utf-8")
    env.set("SCITEX_DIR", str(user_root))
    env.chdir(project)
    return {
        "resolved": resolve_tasks_path(None),
        "user_store": user_store,
        "proj_store": proj_store,
    }


def test_user_scope_wins_over_project_store(resolution_with_a_project_shadow_store):
    # Arrange
    scenario = resolution_with_a_project_shadow_store
    # Act
    resolved = scenario["resolved"]
    # Assert — the canonical user store wins.
    assert resolved == scenario["user_store"]


def test_resolution_ignores_the_in_repo_shadow_store(
    resolution_with_a_project_shadow_store,
):
    # Arrange
    scenario = resolution_with_a_project_shadow_store
    # Act
    resolved = scenario["resolved"]
    # Assert — the per-repo copy is never the answer.
    assert resolved != scenario["proj_store"]


def test_user_scope_used_when_no_project_store(tmp_path, clean_tasks_env, env):
    """Resolution precedence 4 — user scope when no project scope."""
    # Arrange — no .git ancestor at cwd; SCITEX_DIR has the store.
    work = tmp_path / "work"
    work.mkdir()
    user_root = tmp_path / "user-scitex"
    user_dir = user_root / "todo"
    user_dir.mkdir(parents=True)
    user_store = user_dir / "tasks.yaml"
    user_store.write_text("tasks: []\n", encoding="utf-8")
    env.set("SCITEX_DIR", str(user_root))
    env.chdir(work)
    # Act
    resolved = resolve_tasks_path(None)
    # Assert
    assert resolved == user_store


#: WHY the two `_find_git_root` tests below are split but share this
#: rationale: the helper is retained (the CONFIG layer in _config.py still
#: uses it for the reminders config's project override) even though the DATA
#: store no longer consults it, so it is tested directly rather than via
#: resolve_tasks_path. Ascending FROM a deep subdir and NOT claiming a repo
#: from outside it are opposite failure directions — a helper that returns the
#: repo for every path satisfies the first claim perfectly.
@pytest.fixture()
def repo_with_a_deep_subdir(tmp_path):
    """A repo at tmp_path/repo with a subdir at tmp_path/repo/a/b/c."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    deep = repo / "a" / "b" / "c"
    deep.mkdir(parents=True)
    return {"repo": repo, "deep": deep, "outside": tmp_path.parent}


def test_find_git_root_walks_up(repo_with_a_deep_subdir):
    # Arrange
    scenario = repo_with_a_deep_subdir
    # Act
    found = _find_git_root(scenario["deep"])
    # Assert — ascends from the deep subdir to the repo root.
    assert found == scenario["repo"].resolve()


def test_find_git_root_does_not_claim_a_repo_from_outside(repo_with_a_deep_subdir):
    # Arrange
    scenario = repo_with_a_deep_subdir
    # Act
    found = _find_git_root(scenario["outside"])
    # Assert — a dir with no .git ancestor never yields this repo.
    assert found != scenario["repo"].resolve()


# EOF
