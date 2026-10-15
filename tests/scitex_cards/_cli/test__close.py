#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `close` CLI verb (CliRunner; no mocks).

Verifies the close-with-reason verb that fills the lead-confirmed gap:
``delete`` drops context, so close composes ``comment_task`` +
``update_task(status=cancelled)`` + ``_log_meta.closed_{at,by}`` to preserve
the reason on the card. (It wrote ``deferred`` until 2026-07-10, when the
operator ruled deferred non-terminal and ``cancelled`` became the close
state.)

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
# close                                                                       #
# --------------------------------------------------------------------------- #
def test_close_persists_comment_and_status_deferred_exit_code(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    store = _store_path()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "a", "--reason", "superseded"]
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert result.exit_code == 0, result.output


def test_close_persists_comment_and_status_cancelled_status(env, postgres_dsn):
    # Arrange — close writes `cancelled` since 2026-07-10. It used to write
    # `deferred`, which overloaded "not now" as the close state and silently
    # hid 354 open cards from every active view once deferred became
    # non-terminal (operator: deferred は終了ではない).
    runner = CliRunner()
    store = _store_path()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    runner.invoke(main, ["close", "a", "--reason", "superseded"])
    # Assert — the exit code for this same invocation is pinned by
    # test_close_persists_comment_and_status_deferred_exit_code above.
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["status"] == "cancelled"


def test_close_persists_comment_and_status_deferred_text(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    store = _store_path()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "a", "--reason", "superseded"]
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["comments"][0]["text"] == "[CLOSED] superseded"


def test_close_persists_comment_and_status_deferred_closed_at(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    store = _store_path()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "a", "--reason", "superseded"]
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["_log_meta"]["closed_at"]


def test_close_persists_comment_and_status_deferred_closed_by(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    store = _store_path()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "a", "--reason", "superseded"]
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["_log_meta"]["closed_by"] == "agent:cli-test"


def test_close_missing_reason_is_usage_error(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(main, ["close", "a"])
    # Assert
    assert result.exit_code == 2 and "Traceback" not in result.output


def test_close_empty_reason_is_usage_error(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(main, ["close", "a", "--reason", "   "])
    # Assert
    assert result.exit_code == 2


def test_close_unknown_id_nonzero_no_traceback(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "no-such-id", "--reason", "x"]
    )
    # Assert
    assert result.exit_code != 0 and "Traceback" not in result.output


def test_close_by_override_flows_into_comment_and_log_meta_author(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    store = _store_path()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:env")
    # Act
    runner.invoke(
        main,
        [
            "close",
            "a",
            "--reason",
            "manual close",
            "--by",
            "agent:explicit",
        ],
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["comments"][0]["author"] == "agent:explicit"


def test_close_by_override_flows_into_comment_and_log_meta_closed_by(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    store = _store_path()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:env")
    # Act
    runner.invoke(
        main,
        [
            "close",
            "a",
            "--reason",
            "manual close",
            "--by",
            "agent:explicit",
        ],
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["_log_meta"]["closed_by"] == "agent:explicit"


def test_close_dry_run_does_not_mutate_get(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    store = _store_path()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    runner.invoke(
        main,
        ["close", "a", "--reason", "ghost", "--dry-run"],
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk.get("status") == "deferred"  # add's default; dry-run left it


def test_close_dry_run_does_not_mutate_get_2(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    store = _store_path()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    runner.invoke(
        main,
        ["close", "a", "--reason", "ghost", "--dry-run"],
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk.get("comments", []) == []


def test_close_dry_run_does_not_mutate_value_excludes(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    store = _store_path()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    runner.invoke(
        main,
        ["close", "a", "--reason", "ghost", "--dry-run"],
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert "closed_at" not in (on_disk.get("_log_meta") or {})


def test_close_json_emits_structured_payload_exit_code(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert result.exit_code == 0, result.output


def test_close_json_emits_structured_payload_task_id(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert payload["task_id"] == "a"


def test_close_json_emits_structured_payload_status(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert payload["status"] == "cancelled"


def test_close_json_emits_structured_payload_reason(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert payload["reason"] == "json-shape"


def test_close_json_emits_structured_payload_text(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert payload["comment"]["text"] == "[CLOSED] json-shape"


def test_close_json_emits_structured_payload_closed_by(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert payload["closed_by"] == "agent:cli-test"


def test_close_json_emits_structured_payload_closed_at(env, postgres_dsn):
    # Arrange
    runner = CliRunner()
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A"]
    )
    env.set("SCITEX_CARDS_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert payload["closed_at"]


# EOF
