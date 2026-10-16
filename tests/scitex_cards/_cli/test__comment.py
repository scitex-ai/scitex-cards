#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `comment` CLI verb (CliRunner; no mocks).

Verifies the thin CLI wrapper over ``_store.comment_task``:
    - happy path persistence (round-trip into ``task.comments[]``)
    - --author override flows into the entry's ``author`` field
    - --json emits a structured payload
    - error-paths surface as ClickException (non-zero, no traceback)
    - --dry-run prints intent and does NOT mutate the store

CliRunner against the ambient per-test store. No mocks.

Store-touching tests request the ``postgres_dsn`` fail-LOUD fixture
(bare-param pattern): every write below targets the ambient per-test
store, which is refused without a writable cluster.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from scitex_cards import _model
from scitex_cards._cli import main


def _store_path() -> str:
    # The store is the database; load/save read+write it and IGNORE any
    # path except as the provenance stamp. Return the PINNED STORE identity
    # (== resolve_tasks_path(None)) so a read-after-write round-trips instead
    # of tripping the stamp-mismatch guard.
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


# --------------------------------------------------------------------------- #
# comment                                                                     #
# --------------------------------------------------------------------------- #
def test_comment_exits_zero(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(main, ["comment", "a", "first note"])
    # Assert
    assert result.exit_code == 0, result.output


def test_comment_persists_into_comments_list(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    store = _store_path()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    runner.invoke(main, ["comment", "a", "first note"])
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["comments"][0]["text"] == "first note"


def test_comment_author_flag_stored_in_by_field(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    store = _store_path()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:env")
    runner.invoke(
        main,
        ["comment", "a", "explicit-author note", "--author", "agent:explicit"],
    )
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["comments"][0]["author"] == "agent:explicit"


def test_comment_json_emits_valid_payload(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["comment", "a", "structured", "--json"]
    )
    payload = json.loads(result.output.strip())
    # Assert
    assert payload["task_id"] == "a" and payload["comment"]["text"] == "structured"


def test_comment_unknown_task_id_nonzero_no_traceback(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["comment", "does-not-exist", "anything"]
    )
    # Assert
    assert result.exit_code != 0 and "Traceback" not in result.output


def test_comment_empty_text_nonzero(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(main, ["comment", "a", "   "])
    # Assert
    assert result.exit_code != 0


def test_comment_dry_run_does_not_mutate(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    store = _store_path()
    runner.invoke(main, ["add", "--assignee", "agent:test-suite", "a", "A"])
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    runner.invoke(
        main, ["comment", "a", "ghost", "--dry-run"]
    )
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk.get("comments", []) == []


# EOF
