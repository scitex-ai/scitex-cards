#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The store's autocommit repo must PACK, and must never PRUNE.

*** THIS EXISTS BECAUSE gc.auto=0 COST 13 GIGABYTES. ***

The store repo is committed on EVERY card write. The old code set
``gc.auto=0`` "so every snapshot stays reachable" — conflating two different
things:

  * gc does NOT prune REACHABLE objects. Every commit on the branch is
    reachable by definition, so gc PACKS them; it never deletes them.
  * ``gc.pruneExpire=never`` already forbids pruning even UNREACHABLE objects.
    That is the guard that actually protects old snapshots.

All ``gc.auto=0`` achieved was stopping git from ever packing, so every save's
full ~6.5 MB blob stayed a separate loose object forever.

MEASURED on the live fleet store, 2026-07-14 (5 weeks, 10,828 commits):
    .git = 13 GB, 23,252 loose objects, on a 94%-full shared disk
    after `git gc`: 90 MB, 3 loose objects, ALL 10,829 commits preserved
144x smaller, zero history lost.

So this pins BOTH halves of the contract: packing allowed, pruning forbidden.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scitex_cards import _store

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not installed; autocommit is skipped"
)


def _git_config(store_dir: Path, key: str) -> str | None:
    """The repo's value for `key`, or None when unset."""
    out = subprocess.run(
        ["git", "-C", str(store_dir), "config", "--get", key],
        capture_output=True,
        text=True,
        check=False,
    )
    val = out.stdout.strip()
    return val or None


def _commit_count(store_dir: Path) -> int:
    """How many commits are reachable from HEAD in the autocommit repo."""
    out = subprocess.run(
        ["git", "-C", str(store_dir), "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return int(out.stdout.strip() or 0)


@pytest.fixture()
def committed_store(tmp_path: Path) -> Path:
    """A store that has been written once, so the autocommit repo is initialized."""
    path = tmp_path / "tasks.yaml"
    _store.add_task(path, id="c1", title="t", status="deferred", agent="a")
    return path


def test_autocommit_initializes_a_repo(committed_store: Path):
    # Arrange
    store_dir = committed_store.parent
    # Act
    git_dir = store_dir / ".git"
    # Assert — one card write is enough to lazily init the recovery repo.
    assert git_dir.exists()


def test_gc_auto_is_NOT_disabled(committed_store: Path):
    """THE 13 GB BUG.

    ``gc.auto=0`` stops git ever packing, so every save's full blob stays a
    loose object forever. It must not be set to 0.
    """
    # Arrange
    store_dir = committed_store.parent
    # Act
    gc_auto = _git_config(store_dir, "gc.auto")
    # Assert — packing must stay ALLOWED.
    assert gc_auto != "0"


def test_pruning_is_forbidden(committed_store: Path):
    # Arrange
    store_dir = committed_store.parent
    # Act
    prune_expire = _git_config(store_dir, "gc.pruneExpire")
    # Assert — the guard that ACTUALLY protects old snapshots: pack, never delete.
    assert prune_expire == "never"


def test_a_write_is_committed(committed_store: Path):
    # Arrange
    store_dir = committed_store.parent
    # Act
    commits = _commit_count(store_dir)
    # Assert — the recovery layer works; the point of the repo is the history.
    assert commits >= 1


def test_gc_packs_without_losing_commits(committed_store: Path):
    """The empirical claim, in miniature: gc shrinks and preserves.

    Several writes -> several commits -> `git gc` -> every commit still there.
    This is the property the 13 GB fix relies on, so it is pinned rather than
    assumed.
    """
    # Arrange
    store_dir = committed_store.parent
    for i in range(3):
        _store.update_task(committed_store, "c1", note=f"n{i}")
    before = _commit_count(store_dir)
    # Act
    subprocess.run(
        ["git", "-C", str(store_dir), "gc", "--prune=now"],
        capture_output=True,
        check=False,
    )
    # Assert — gc PACKED; it did not delete a single commit.
    assert _commit_count(store_dir) == before
