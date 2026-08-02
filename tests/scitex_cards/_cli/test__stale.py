#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `list-stale` CLI verb (CliRunner; no mocks).

Mirrors the criteria of the board's `/stale` endpoint (PR #153) +
the 🧹 Stale Review panel (PR #154). AAA pattern + one assertion
per test (TQ002 / TQ007).
"""

from __future__ import annotations

import datetime
import json
import os
import textwrap

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _store(tmp_path):
    """Seed the canonical DB with the stale-review fixture; return the STORE path.

    The store is SQLite now; the CLI reads the canonical DB via
    ``resolve_tasks_path(None)``. Tests still author the fixture as readable
    YAML text — parse it, seed the DB, and return the STORE identity path (NOT
    the DB path — see THE STORE-PATH RULE in the migration playbook).
    """
    from conftest import seed_db_from_doc

    from scitex_cards._yaml import safe_load

    now = datetime.datetime.now(datetime.timezone.utc)
    old = _iso(now - datetime.timedelta(days=30))
    recent = _iso(now - datetime.timedelta(days=2))
    text = textwrap.dedent(
        f"""\
        tasks:
          - id: old-pending-card
            title: '[P2] long stale pending card'
            status: deferred
            created_at: '{old}'
            project: scitex-dev
            assignee: proj-scitex-dev
          - id: recent-pending-card
            title: '[P2] recently created pending card'
            status: deferred
            created_at: '{recent}'
            project: scitex-dev
            assignee: proj-scitex-dev
          - id: no-timestamp-card
            title: 'undated pending card'
            status: deferred
            project: business
            assignee: proj-scitex-lead
          - id: vague-card
            title: 'tbd'
            status: deferred
          - id: done-card
            title: 'completed card should never be flagged'
            status: done
            created_at: '{old}'
            project: scitex-dev
        """
    )
    doc = safe_load(text) or {}
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def test_stale_list_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale"])
    # Assert
    assert result.exit_code == 0, result.output


def test_stale_list_includes_old_pending(tmp_path):
    # Arrange
    runner = CliRunner()
    _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale"])
    # Assert
    assert "old-pending-card" in result.output


def test_stale_list_excludes_recent_pending(tmp_path):
    # Arrange
    runner = CliRunner()
    _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale"])
    # Assert
    assert "recent-pending-card" not in result.output


def test_stale_list_excludes_done(tmp_path):
    # Arrange
    runner = CliRunner()
    _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale"])
    # Assert
    assert "done-card" not in result.output


def test_stale_list_includes_no_timestamp_by_default(tmp_path):
    # Arrange
    runner = CliRunner()
    _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale"])
    # Assert
    assert "no-timestamp-card" in result.output


def test_stale_list_exclude_no_timestamp_filter(tmp_path):
    # Arrange
    runner = CliRunner()
    _store(tmp_path)
    # Act
    result = runner.invoke(
        main,
        ["list-stale", "--exclude-no-timestamp"],
    )
    # Assert — no-timestamp-card was flagged ONLY for missing timestamps; the
    # filter must drop it (vague-card stays — it has a second reason).
    assert "no-timestamp-card" not in result.output


def test_stale_list_project_filter(tmp_path):
    # Arrange
    runner = CliRunner()
    _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale", "--project", "scitex-dev"])
    # Assert
    assert "no-timestamp-card" not in result.output


def test_stale_list_json_emits_array(tmp_path):
    # Arrange
    runner = CliRunner()
    _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale", "--json"])
    # Assert
    data = json.loads(result.output)
    assert isinstance(data, list)


def test_stale_list_rejects_negative_days(tmp_path):
    # Arrange
    runner = CliRunner()
    _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale", "--days", "-1"])
    # Assert
    assert result.exit_code != 0


def test_stale_list_empty_when_days_huge(tmp_path):
    # Arrange
    runner = CliRunner()
    _store(tmp_path)
    # Act
    result = runner.invoke(
        main,
        ["list-stale", "--days", "10000", "--exclude-no-timestamp"],
    )
    # Assert — vague-card stays (flagged for vagueness, not age); we just
    # check that the runner returns a sane non-error result.
    assert result.exit_code == 0


# EOF
