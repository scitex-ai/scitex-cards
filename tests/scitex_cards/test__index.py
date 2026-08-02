#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the SQLite derived-index (PR-B of the lead-approved
Stage 2 plan, lead a2a `aa02fb0e` / `e5243003`).

Real `tmp_path` SQLite files via the ``SCITEX_TODO_INDEX_PATH`` env
override; no mocks (STX-NM / PA-306). The lane-discovery glob env
(``SCITEX_TODO_LANE_GLOBS``) is overridden per test so we don't pick
up the host's `~/proj/*` lanes.

Covers:

  - `init_schema` creates the four expected tables
  - `rebuild_index` populates from the canonical store
  - a rebuild indexes every card the store holds (the per-file lane
    UNION is no longer expressible now the store is a single SQLite DB
    that `load_tasks` reads regardless of the path handed to it)
  - `info` reports row count + schema version + last_index_at
  - `query_tasks` filters by project / agent / status
  - `index_path` honors the env override
  - The CLI `index rebuild` / `info` verbs round-trip
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_cards import _index as _idx
from scitex_cards._cli import main

# === Fixtures ===============================================================


@pytest.fixture
def index_target(env, tmp_path):
    """Point ``SCITEX_TODO_INDEX_PATH`` at a tmp file so the real
    ``~/.scitex/todo/.tasks.index.sqlite`` is never touched."""
    target = tmp_path / "tasks.index.sqlite"
    env.set("SCITEX_TODO_INDEX_PATH", str(target))
    yield target


def _write_store(path: Path, body: str) -> None:
    """Seed the canonical SQLite store from a YAML-text fixture.

    The store is SQLite now: ``load_tasks`` (which ``rebuild_index`` uses)
    reads the canonical database and IGNORES the path it is handed. So the
    fixture text is parsed into a doc and seeded into the pinned canonical
    DB (``SCITEX_CARDS_DB``). The file is still written on disk -- at BOTH
    the caller-passed ``path`` and the pinned store identity -- so
    ``rebuild_index``'s ``global_path.exists()`` gate is satisfied whichever
    path the caller (a direct ``tmp_path`` arg, or the CLI-resolved store)
    hands it. Each call re-seeds; no test seeds twice.
    """
    from conftest import seed_db_from_doc

    from scitex_cards._yaml import safe_load

    doc = safe_load(body) or {}
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    for target in (path, Path(os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"])):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


# === init_schema ===========================================================


class TestInitSchema:
    """The four expected tables exist after `init_schema`."""

    def test_tasks_table_created(self, index_target):
        # Arrange
        # Act
        with _idx.open_connection(index_target) as c:
            _idx.init_schema(c)
            rows = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'",
            ).fetchall()
        # Assert
        assert len(rows) == 1

    def test_tags_table_created(self, index_target):
        # Arrange
        # Act
        with _idx.open_connection(index_target) as c:
            _idx.init_schema(c)
            rows = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tags'",
            ).fetchall()
        # Assert
        assert len(rows) == 1

    def test_meta_table_created(self, index_target):
        # Arrange
        # Act
        with _idx.open_connection(index_target) as c:
            _idx.init_schema(c)
            rows = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'",
            ).fetchall()
        # Assert
        assert len(rows) == 1


# === rebuild_index — global only ===========================================


class TestRebuildGlobalOnly:
    """Rebuild with no lanes populates from the global YAML."""

    def test_populates_global_tasks(self, index_target, tmp_path):
        # Arrange
        store = tmp_path / "global.yaml"
        _write_store(
            store,
            "tasks:\n"
            "  - {id: g1, title: G1, status: pending}\n"
            "  - {id: g2, title: G2, status: in_progress}\n",
        )
        # Act
        stats = _idx.rebuild_index(store, [])
        # Assert
        assert stats["total"] == 2

    def test_meta_records_index_version(self, index_target, tmp_path):
        # Arrange
        store = tmp_path / "global.yaml"
        _write_store(store, "tasks: []\n")
        # Act
        _idx.rebuild_index(store, [])
        # Assert
        with _idx.open_connection(index_target) as c:
            row = c.execute(
                "SELECT value FROM meta WHERE key='index_version'",
            ).fetchone()
        assert row["value"] == str(_idx.SCHEMA_VERSION)


# === rebuild_index — index reflects the whole store ========================


class TestRebuildWithLanes:
    """The rebuilt index carries every card the store holds.

    The old per-project lane UNION (global file + lane file, lane wins on
    id collision) is no longer expressible: the store is SQLite and
    ``load_tasks`` ignores the path it is handed, so ``rebuild_index``'s
    "global" and "lane" reads BOTH return the one canonical DB (one row per
    id). What survives -- and is what this class now asserts -- is that a
    rebuild indexes every card present in the store. The distinct-source
    collision test was deleted with this migration (subject gone).
    """

    def test_lane_card_appears_in_index(self, index_target, tmp_path):
        # Arrange — seed the canonical store with two distinct cards.
        store = tmp_path / "global.yaml"
        _write_store(
            store,
            "tasks:\n"
            "  - {id: g1, title: G1, status: pending}\n"
            "  - {id: lan1, title: LAN1, status: pending}\n",
        )
        # Act
        _idx.rebuild_index(store, [])
        # Assert — the rebuilt index carries both store cards.
        with _idx.open_connection(index_target) as c:
            rows = {r["id"] for r in c.execute("SELECT id FROM tasks")}
        assert rows == {"g1", "lan1"}


# === info ===================================================================


class TestInfo:
    """`info()` reports the on-disk state."""

    def test_info_reports_row_count_after_rebuild(
        self,
        index_target,
        tmp_path,
    ):
        # Arrange
        store = tmp_path / "global.yaml"
        _write_store(
            store,
            "tasks:\n"
            "  - {id: a, title: A, status: pending}\n"
            "  - {id: b, title: B, status: done}\n",
        )
        _idx.rebuild_index(store, [])
        # Act
        payload = _idx.info()
        # Assert
        assert payload["rows"] == 2

    def test_info_when_no_index_yet(self, index_target):
        # Arrange — never rebuild.
        # Act
        payload = _idx.info()
        # Assert
        assert payload["exists"] is False


# === query_tasks ===========================================================


class TestQueryTasks:
    """The basic SQL read path the /graph SQL flip (PR-D) will build on."""

    def test_filter_by_project(self, index_target, tmp_path):
        # Arrange
        store = tmp_path / "global.yaml"
        _write_store(
            store,
            "tasks:\n"
            "  - {id: x, title: X, status: pending, project: alpha}\n"
            "  - {id: y, title: Y, status: pending, project: beta}\n",
        )
        _idx.rebuild_index(store, [])
        # Act
        rows = _idx.query_tasks(project="alpha")
        # Assert
        assert {r["id"] for r in rows} == {"x"}

    def test_filter_by_status(self, index_target, tmp_path):
        # Arrange
        store = tmp_path / "global.yaml"
        _write_store(
            store,
            "tasks:\n"
            "  - {id: x, title: X, status: pending}\n"
            "  - {id: y, title: Y, status: done}\n",
        )
        _idx.rebuild_index(store, [])
        # Act
        rows = _idx.query_tasks(status="done")
        # Assert
        assert {r["id"] for r in rows} == {"y"}


# === CLI verbs =============================================================


class TestCliRebuildAndInfo:
    """`scitex-todo index rebuild` + `info` round-trip via CliRunner."""

    def test_rebuild_then_info_reports_rows(
        self,
        index_target,
        tmp_path,
    ):
        # Arrange — seed the pinned canonical store; empty lane glob.
        # (The CLI resolves the pinned store itself; repointing the store
        # env at a tmp path is the STORE-PATH anti-pattern -- it would
        # desync from the seeded DB and read back empty.)
        store = tmp_path / "global.yaml"
        _write_store(
            store,
            "tasks:\n  - {id: a, title: A, status: pending}\n",
        )
        # The autouse fixture already sets SCITEX_TODO_LANE_GLOBS="".
        runner = CliRunner()
        # Act
        rb = runner.invoke(main, ["index", "rebuild", "-y"])
        info = runner.invoke(main, ["index", "info", "--json"])
        # Assert — rebuild exited clean + info reports 1 row.
        payload = json.loads(info.output)
        assert rb.exit_code == 0 and payload["rows"] == 1

    def test_dry_run_does_not_create_index(
        self,
        index_target,
        tmp_path,
    ):
        # Arrange
        store = tmp_path / "global.yaml"
        _write_store(store, "tasks: []\n")
        runner = CliRunner()
        # Act
        rb = runner.invoke(main, ["index", "rebuild", "--dry-run"])
        # Assert — dry-run exits 0 + the SQLite file was NOT created.
        assert rb.exit_code == 0 and not index_target.exists()


# === env override ==========================================================


def test_env_override_changes_index_path(env, tmp_path):
    # Arrange
    target = tmp_path / "custom" / "i.sqlite"
    env.set("SCITEX_TODO_INDEX_PATH", str(target))
    # Act
    p = _idx.index_path()
    # Assert
    assert p == target
