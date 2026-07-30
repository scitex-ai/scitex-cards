#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the directory-card migrator (PR-D Stage 2).

Lead-approved guardrails (operator OK via lead a2a `3cf31901`):

  1. The `note` field migrates VERBATIM into ``tasks/<id>/README.md``
     (bytes-equal head check; no transform, no truncation).
  2. Atomic per-card: if the README write or verify fails, the
     row stays untouched.
  3. Idempotent: re-running the migrator on an already-migrated
     lane is a no-op (every row classifies as canonical).
  4. Per-lane git commit at end: when the lane's parent is a git
     repo, the post-migration state is committed under a clear
     ``[scitex-todo migrate]`` message.

SQLite is the store (YAML→SQLite cutover): the migrator reads its rows
from the canonical DB — NOT from a ``tasks.yaml`` file — classifies them,
writes each card's ``tasks/<id>/README.md`` under the store path's parent,
and writes the stripped rows back to the DB. So these tests SEED the
canonical DB (``seed_db_from_doc``) instead of writing a lane file, and
pass the pinned STORE identity path (``SCITEX_CARDS_TASKS_YAML_SHARED``)
to ``apply_lane`` so the DB's write-stamp equals what the next read
resolves. Tests that only re-check the README may anchor on any lane
whose ``.parent`` hosts the ``tasks/<id>/`` dirs (the git test uses a
real repo-internal lane for exactly that reason).

Real ``tmp_path`` lanes + real ``git init``; no mocks (STX-NM / PA-306).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from scitex_cards import _migration as _mig
from scitex_cards._model import load_tasks

# === Helpers ================================================================


def _seed_db(body: str) -> None:
    """Seed the canonical SQLite store from a YAML-text fixture (Pattern 1).

    The migrator reads its rows from the DB now, not from a ``tasks.yaml``
    file, so the fixtures are still authored as readable YAML text: parse it
    and seed the canonical DB. Nothing is written to any lane file.
    """
    from conftest import seed_db_from_doc

    doc = yaml.safe_load(body) or {}
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])


def _store_lane() -> Path:
    """The pinned STORE identity path (``resolve_tasks_path(None)``).

    Its ``.parent`` hosts the ``tasks/<id>/`` dirs the migrator writes.
    Passing IT (not a scratch ``tasks.yaml``) to ``apply_lane`` keeps the
    DB's write-stamp equal to what a subsequent read resolves, so the
    read-after-write round-trips.
    """
    return Path(os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"])


def _git_init(root: Path) -> None:
    """Initialize a git repo with a deterministic user/email so
    `git commit` succeeds inside the test runner sandbox."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"],
        check=True,
    )


# === Bytes-equal note round-trip (guardrail #1) ============================


class TestNoteByteEquality:
    """The `note` field's bytes must round-trip through README.md."""

    def test_note_appears_verbatim_in_readme_head(self, tmp_path):
        # Arrange — short note, no title overflow, no long comments.
        original_note = "First line.\nSecond line.\n\nThird with 日本語."
        _seed_db(
            "tasks:\n"
            "  - id: a\n"
            "    title: A\n"
            "    status: pending\n"
            f"    note: {yaml.safe_dump(original_note).strip()}\n",
        )
        lane = _store_lane()
        # Act
        _mig.apply_lane(lane)
        # Assert — written, then re-read README's leading bytes match.
        readme = (lane.parent / "tasks" / "a" / "README.md").read_text(
            encoding="utf-8",
        )
        assert readme.startswith(original_note)

    def test_note_is_stripped_from_persisted_row(self, tmp_path):
        # Arrange
        _seed_db(
            "tasks:\n  - {id: a, title: A, status: pending, note: 'body here'}\n",
        )
        lane = _store_lane()
        # Act
        _mig.apply_lane(lane)
        # Assert — the migrated row no longer carries the note (DB is the
        # store; the card round-trips as a JSON blob so a popped key stays
        # absent, exactly as it did in the old on-disk YAML).
        rows = load_tasks(lane)
        assert "note" not in rows[0]


# === Title / comment trimming ===============================================


class TestTrimming:
    """Title >120 gets trimmed in the row; full title goes into README."""

    def test_long_title_trimmed_in_persisted_row(self, tmp_path):
        # Arrange
        long_title = "T" * (_mig.MAX_TITLE_CHARS + 50)
        _seed_db(
            f"tasks:\n  - {{id: a, title: '{long_title}', status: pending}}\n",
        )
        lane = _store_lane()
        # Act
        _mig.apply_lane(lane)
        # Assert
        rows = load_tasks(lane)
        assert len(rows[0]["title"]) == _mig.MAX_TITLE_CHARS

    def test_long_title_full_preserved_in_readme(self, tmp_path):
        # Arrange
        long_title = "U" * (_mig.MAX_TITLE_CHARS + 50)
        _seed_db(
            f"tasks:\n  - {{id: a, title: '{long_title}', status: pending}}\n",
        )
        lane = _store_lane()
        # Act
        _mig.apply_lane(lane)
        # Assert
        readme = (lane.parent / "tasks" / "a" / "README.md").read_text(
            encoding="utf-8",
        )
        assert long_title in readme


# === Idempotency ============================================================


class TestIdempotent:
    """Re-running the migrator on an already-migrated lane is a no-op."""

    def test_second_apply_writes_nothing(self, tmp_path):
        # Arrange — initial migration.
        _seed_db(
            "tasks:\n  - {id: a, title: A, status: pending, note: 'body'}\n",
        )
        lane = _store_lane()
        _mig.apply_lane(lane)
        # Act — second pass.
        result = _mig.apply_lane(lane)
        # Assert — every row is already-canonical.
        assert all(r.skipped_reason == "already-canonical" for r in result.rows)


# === Dry run ================================================================


class TestDryRun:
    """`apply_lane(dry_run=True)` does NOT write to disk."""

    def test_dry_run_leaves_no_readme(self, tmp_path):
        # Arrange
        _seed_db(
            "tasks:\n  - {id: a, title: A, status: pending, note: 'body'}\n",
        )
        lane = _store_lane()
        # Act
        _mig.apply_lane(lane, dry_run=True)
        # Assert
        assert not (lane.parent / "tasks" / "a" / "README.md").exists()

    def test_dry_run_leaves_store_unchanged(self, tmp_path):
        # Arrange
        _seed_db(
            "tasks:\n  - {id: a, title: A, status: pending, note: 'body'}\n",
        )
        lane = _store_lane()
        # Act
        _mig.apply_lane(lane, dry_run=True)
        # Assert — original `note: 'body'` still present in the store.
        rows = load_tasks(lane)
        assert rows[0].get("note") == "body"


# === Per-lane git commit (guardrail #3) ====================================


class TestGitCommit:
    """When the lane's parent is a git repo, the migrator commits."""

    def test_real_git_repo_gets_migrate_commit(self, tmp_path):
        # Arrange — make a git repo, place the lane inside it. The migrator
        # reads rows from the canonical DB (seeded here) and writes this
        # card's tasks/<id>/README.md under the lane's parent, so the
        # post-migration state lands inside the repo and the per-lane commit
        # can stage it.
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init(repo)
        lane = repo / ".scitex" / "todo" / "tasks.yaml"
        _seed_db(
            "tasks:\n  - {id: a, title: A, status: pending, note: 'body'}\n",
        )
        # Act
        result = _mig.apply_lane(lane)
        # Assert — the migrator reports the commit landed.
        assert result.git_committed is True, (
            f"git not committed; skip_reason={result.git_skip_reason!r}; "
            f"rows={[(r.id, r.written_readme, r.yaml_updated, r.skipped_reason) for r in result.rows]}"
        )
