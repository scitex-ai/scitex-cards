#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the directory-card migration scanner (PR-D Stage 1).

Real `tmp_path` lanes; no mocks (STX-NM / PA-306). The scanner is
pure-data — easy to fixture, no FS writes beyond the fixture itself.
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import seed_db_from_doc

from scitex_cards import _migration as _mig
from scitex_cards._cli import main


def _write_lane(lane: Path, body: str) -> None:
    lane.parent.mkdir(parents=True, exist_ok=True)
    lane.write_text(body, encoding="utf-8")


def _mk_dir_card(lane: Path, card_id: str, readme: str = "body\n") -> None:
    """Create a canonical `tasks/<id>/README.md` sibling."""
    tasks_dir = lane.parent / "tasks" / card_id
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "README.md").write_text(readme, encoding="utf-8")


# === classify_row ===========================================================


class TestClassify:
    """Per-row classification covers the five migration kinds."""

    def test_canonical_row_has_no_classifications(self, tmp_path):
        # Arrange — minimal-metadata row + tasks/<id>/ sibling.
        lane = tmp_path / "tasks.yaml"
        _write_lane(
            lane,
            "tasks:\n  - {id: a, title: A, status: pending}\n",
        )
        _mk_dir_card(lane, "a")
        row = {"id": "a", "title": "A", "status": "pending"}
        # Act
        plan = _mig.classify_row(row, lane)
        # Assert
        assert plan.canonical is True

    def test_row_with_note_field_flagged(self, tmp_path):
        # Arrange
        lane = tmp_path / "tasks.yaml"
        _write_lane(lane, "tasks: []\n")
        _mk_dir_card(lane, "a")
        row = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "note": "This is a long body that lives in yaml today.",
        }
        # Act
        plan = _mig.classify_row(row, lane)
        # Assert
        assert "NEEDS_NOTE_MIGRATE" in plan.classifications

    def test_row_with_long_title_flagged(self, tmp_path):
        # Arrange — title longer than the cap.
        lane = tmp_path / "tasks.yaml"
        _write_lane(lane, "tasks: []\n")
        _mk_dir_card(lane, "a")
        title = "X" * (_mig.MAX_TITLE_CHARS + 1)
        row = {"id": "a", "title": title, "status": "pending"}
        # Act
        plan = _mig.classify_row(row, lane)
        # Assert
        assert "NEEDS_TITLE_TRIM" in plan.classifications

    def test_row_with_long_comment_flagged(self, tmp_path):
        # Arrange — comments[] text > the cap.
        lane = tmp_path / "tasks.yaml"
        _write_lane(lane, "tasks: []\n")
        _mk_dir_card(lane, "a")
        long_text = "y" * (_mig.MAX_COMMENT_CHARS + 1)
        row = {
            "id": "a",
            "title": "A",
            "status": "pending",
            "comments": [{"ts": "x", "author": "z", "text": long_text}],
        }
        # Act
        plan = _mig.classify_row(row, lane)
        # Assert
        assert "NEEDS_COMMENT_TRIM" in plan.classifications

    def test_row_without_directory_flagged(self, tmp_path):
        # Arrange — no tasks/<id>/ sibling.
        lane = tmp_path / "tasks.yaml"
        _write_lane(lane, "tasks: []\n")
        row = {"id": "a", "title": "A", "status": "pending"}
        # Act
        plan = _mig.classify_row(row, lane)
        # Assert
        assert "NEEDS_DIR" in plan.classifications

    def test_row_with_empty_id_flagged(self, tmp_path):
        # Arrange
        lane = tmp_path / "tasks.yaml"
        row = {"title": "no id", "status": "pending"}
        # Act
        plan = _mig.classify_row(row, lane)
        # Assert
        assert "EMPTY_ID" in plan.classifications

    def test_multiple_classifications_compose(self, tmp_path):
        # Arrange — row with note + no dir + long title.
        lane = tmp_path / "tasks.yaml"
        _write_lane(lane, "tasks: []\n")
        long_title = "Z" * (_mig.MAX_TITLE_CHARS + 1)
        row = {
            "id": "multi",
            "title": long_title,
            "status": "pending",
            "note": "body content",
        }
        # Act
        plan = _mig.classify_row(row, lane)
        # Assert
        assert {"NEEDS_DIR", "NEEDS_NOTE_MIGRATE", "NEEDS_TITLE_TRIM"}.issubset(
            set(plan.classifications)
        )


# === scan_lane / scan_all_lanes ============================================


class TestScanLane:
    """`scan_lane` aggregates per-row plans + per-kind counts."""

    def test_scan_lane_counts_total_and_canonical(self, tmp_path):
        # Arrange — one canonical, one needs-migrate. scan_lane loads via
        # load_tasks, which now reads the canonical DB (the lane path no
        # longer selects the store), so SEED the rows into the DB; the lane
        # path still anchors each row's tasks/<id>/ directory probe.
        lane = tmp_path / "tasks.yaml"
        seed_db_from_doc(
            {
                "tasks": [
                    {"id": "ok", "title": "OK", "status": "deferred"},
                    {
                        "id": "bad",
                        "title": "BAD",
                        "status": "deferred",
                        "note": "has body",
                    },
                ]
            },
            os.environ["SCITEX_CARDS_DB"],
        )
        _mk_dir_card(lane, "ok")
        _mk_dir_card(lane, "bad")
        # Act
        plan = _mig.scan_lane(lane)
        # Assert
        assert (plan.total, plan.canonical_count, plan.needs_migration_count) == (
            2,
            1,
            1,
        )

    def test_unloadable_lane_returns_empty_plan(self, tmp_path, caplog, env):
        # Arrange — scan_lane loads via load_tasks, which now reads the
        # canonical DB, not the lane's YAML text (so a malformed YAML lane
        # is no longer a reachable subject). Point the DB env at a path that
        # does not exist so the read RAISES; scan_lane must still swallow it
        # into an empty plan + a WARNING — the swallow-and-warn contract is
        # unique to the scanner and survives the store cutover.
        lane = tmp_path / "tasks.yaml"
        env.set("SCITEX_CARDS_DB", str(tmp_path / "does-not-exist.db"))
        # Act
        with caplog.at_level("WARNING", logger="scitex_cards._migration._migrate"):
            plan = _mig.scan_lane(lane)
        # Assert — empty plan + log warning.
        assert plan.total == 0 and any(
            "cannot load" in r.message for r in caplog.records
        )


class TestScanAllLanes:
    """`scan_all_lanes` rolls up across multiple lanes."""

    def test_scan_all_aggregates_lane_plans(self, tmp_path):
        # Arrange — two tmp lanes over ONE canonical DB. scan_lane reads the
        # DB (not per-lane YAML), so every lane reports the full seeded set;
        # the roll-up therefore sums 2 lanes x 2 rows = 4. len == 2 still
        # proves both lanes were scanned; total == 4 proves the fleet summed
        # both LanePlans (the aggregation this test exists to cover).
        seed_db_from_doc(
            {
                "tasks": [
                    {"id": "ax", "title": "AX", "status": "deferred"},
                    {"id": "bx", "title": "BX", "status": "deferred"},
                ]
            },
            os.environ["SCITEX_CARDS_DB"],
        )
        lane_a = tmp_path / "a" / "tasks.yaml"
        _mk_dir_card(lane_a, "ax")
        lane_b = tmp_path / "b" / "tasks.yaml"
        _mk_dir_card(lane_b, "bx")
        # Act
        fleet = _mig.scan_all_lanes([lane_a, lane_b])
        # Assert
        assert (len(fleet.lanes), fleet.to_dict()["total_rows"]) == (2, 4)


# === Markdown rendering ====================================================


class TestRenderMarkdown:
    """The Markdown report is operator-readable + names the sample
    classifications so they can be eyeballed."""

    def test_markdown_includes_lane_count_and_totals(self, tmp_path):
        # Arrange — scan_all_lanes reads the canonical DB; seed one row.
        lane = tmp_path / "tasks.yaml"
        seed_db_from_doc(
            {"tasks": [{"id": "ok", "title": "OK", "status": "deferred"}]},
            os.environ["SCITEX_CARDS_DB"],
        )
        _mk_dir_card(lane, "ok")
        fleet = _mig.scan_all_lanes([lane])
        # Act
        md = _mig.render_markdown(fleet)
        # Assert
        assert "Directory-card migration plan" in md and "Total rows: **1**" in md


# === CLI verb ==============================================================


class TestCliPlan:
    """`scitex-todo migration plan --json` emits parseable JSON."""

    def test_cli_plan_json_decodes(self, env, tmp_path):
        # Arrange — empty global + empty lane glob.
        global_store = tmp_path / "global.yaml"
        global_store.write_text("tasks: []\n", encoding="utf-8")
        env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(global_store))
        # Act
        result = CliRunner().invoke(main, ["migration", "plan", "--json"])
        # Assert — JSON-decodable + has the expected top-level keys.
        payload = json.loads(result.output)
        assert {
            "lane_count",
            "total_rows",
            "canonical_rows",
            "needs_migration_rows",
            "lanes",
        } <= set(payload.keys())

    def test_cli_plan_markdown_includes_summary(self, env, tmp_path):
        # Arrange
        global_store = tmp_path / "global.yaml"
        global_store.write_text("tasks: []\n", encoding="utf-8")
        env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(global_store))
        # Act
        result = CliRunner().invoke(main, ["migration", "plan", "--markdown"])
        # Assert
        assert "Directory-card migration plan" in result.output


# === Note-preservation byte-equal guard (lead guardrail #1) =================


class TestNoteVerbatim:
    """The scanner's `note_excerpt` is the FIRST 80 chars of the note;
    the migrator (separate PR) must preserve the full note byte-for-
    byte. This test pins the no-truncation, no-reformat contract by
    asserting the excerpt is the literal prefix."""

    def test_note_excerpt_is_literal_prefix_under_80(self, tmp_path):
        # Arrange — short note < 80 chars.
        lane = tmp_path / "tasks.yaml"
        _write_lane(lane, "tasks: []\n")
        body = "short body, never truncated"
        row = {"id": "a", "title": "A", "status": "pending", "note": body}
        # Act
        plan = _mig.classify_row(row, lane)
        # Assert
        assert plan.note_excerpt == body

    def test_note_excerpt_truncated_with_ellipsis_over_80(self, tmp_path):
        # Arrange
        lane = tmp_path / "tasks.yaml"
        _write_lane(lane, "tasks: []\n")
        body = "z" * 200
        row = {"id": "a", "title": "A", "status": "pending", "note": body}
        # Act
        plan = _mig.classify_row(row, lane)
        # Assert — the literal first 80 + "…".
        assert plan.note_excerpt == ("z" * 80 + "…")
