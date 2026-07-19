#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `close` CLI verb (CliRunner; no mocks).

Verifies the close-with-reason verb that fills the lead-confirmed gap:
``delete`` drops context, so close composes ``comment_task`` +
``update_task(status=cancelled)`` + ``_log_meta.closed_{at,by}`` to preserve
the reason on the card. (It wrote ``deferred`` until 2026-07-10, when the
operator ruled deferred non-terminal and ``cancelled`` became the close
state.)
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from scitex_cards import _model
from scitex_cards._cli import main


def _store_path(tmp_path) -> str:
    return str(tmp_path / "tasks.yaml")


# --------------------------------------------------------------------------- #
# close                                                                       #
# --------------------------------------------------------------------------- #
def test_close_persists_comment_and_status_deferred_exit_code(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "a", "--reason", "superseded", "--tasks", store]
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert result.exit_code == 0, result.output


def test_close_persists_comment_and_status_cancelled_status(tmp_path, env):
    # Arrange — close writes `cancelled` since 2026-07-10. It used to write
    # `deferred`, which overloaded "not now" as the close state and silently
    # hid 354 open cards from every active view once deferred became
    # non-terminal (operator: deferred は終了ではない).
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    runner.invoke(main, ["close", "a", "--reason", "superseded", "--tasks", store])
    # Assert — the exit code for this same invocation is pinned by
    # test_close_persists_comment_and_status_deferred_exit_code above.
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["status"] == "cancelled"


def test_close_persists_comment_and_status_deferred_text(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "a", "--reason", "superseded", "--tasks", store]
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["comments"][0]["text"] == "[CLOSED] superseded"


def test_close_persists_comment_and_status_deferred_closed_at(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "a", "--reason", "superseded", "--tasks", store]
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["_log_meta"]["closed_at"]


def test_close_persists_comment_and_status_deferred_closed_by(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "a", "--reason", "superseded", "--tasks", store]
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["_log_meta"]["closed_by"] == "agent:cli-test"


def test_close_missing_reason_is_usage_error(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(main, ["close", "a", "--tasks", store])
    # Assert
    assert result.exit_code == 2 and "Traceback" not in result.output


def test_close_empty_reason_is_usage_error(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(main, ["close", "a", "--reason", "   ", "--tasks", store])
    # Assert
    assert result.exit_code == 2


def test_close_unknown_id_nonzero_no_traceback(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "no-such-id", "--reason", "x", "--tasks", store]
    )
    # Assert
    assert result.exit_code != 0 and "Traceback" not in result.output


def test_close_by_override_flows_into_comment_and_log_meta_author(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:env")
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
            "--tasks",
            store,
        ],
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["comments"][0]["author"] == "agent:explicit"


def test_close_by_override_flows_into_comment_and_log_meta_closed_by(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:env")
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
            "--tasks",
            store,
        ],
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["_log_meta"]["closed_by"] == "agent:explicit"


def test_close_dry_run_does_not_mutate_get(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    runner.invoke(
        main,
        ["close", "a", "--reason", "ghost", "--tasks", store, "--dry-run"],
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk.get("status") == "deferred"  # add's default; dry-run left it


def test_close_dry_run_does_not_mutate_get_2(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    runner.invoke(
        main,
        ["close", "a", "--reason", "ghost", "--tasks", store, "--dry-run"],
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk.get("comments", []) == []


def test_close_dry_run_does_not_mutate_value_excludes(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    runner.invoke(
        main,
        ["close", "a", "--reason", "ghost", "--tasks", store, "--dry-run"],
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert "closed_at" not in (on_disk.get("_log_meta") or {})


def test_close_json_emits_structured_payload_exit_code(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--tasks", store, "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert result.exit_code == 0, result.output


def test_close_json_emits_structured_payload_task_id(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--tasks", store, "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert payload["task_id"] == "a"


def test_close_json_emits_structured_payload_status(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--tasks", store, "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert payload["status"] == "cancelled"


def test_close_json_emits_structured_payload_reason(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--tasks", store, "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert payload["reason"] == "json-shape"


def test_close_json_emits_structured_payload_text(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--tasks", store, "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert payload["comment"]["text"] == "[CLOSED] json-shape"


def test_close_json_emits_structured_payload_closed_by(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--tasks", store, "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert payload["closed_by"] == "agent:cli-test"


def test_close_json_emits_structured_payload_closed_at(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "--assignee", "agent:test-suite", "a", "A", "--tasks", store]
    )
    env.set("SCITEX_TODO_AGENT_ID", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--tasks", store, "--json"],
    )
    # Assert
    payload = json.loads(result.output.strip())
    assert payload["closed_at"]


# EOF
