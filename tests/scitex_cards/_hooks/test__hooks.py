#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hook-consumer entry-point contract tests.

Lead a2a `6fff33d6` + `fbffb879`, 2026-06-14, operator-mandated.
Pins the canonical event-payload shape, built-in handler semantics,
idempotency, and the entry-point-group name.

No mocks (STX-NM / PA-306). AAA pattern, one assertion per test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scitex_cards._hooks import (
    ENTRY_POINT_GROUP,
    VALID_EVENT_KINDS,
    HookEventError,
    dispatch_event,
    event_validate,
)
from scitex_cards._store import add_task, list_tasks

# === Wire-shape constants ==================================================


def test_entry_point_group_name_is_canonical():
    # Arrange
    # Act
    # Assert
    assert ENTRY_POINT_GROUP == "scitex_cards.hooks"


def test_valid_event_kinds_set():
    # chat-channel PR (lead a2a `1e8e33d0`, 2026-06-14). C2 added the
    # C1 canonical `card-event` kind to the accepted set.
    # Arrange
    # Act
    # Assert
    assert VALID_EVENT_KINDS == frozenset(
        {"push", "done", "card-message", "unblock", "card-event"}
    )


# === event_validate — fail-loud on shape violations ========================


def test_event_validate_rejects_unknown_kind():
    # Arrange
    bad = {"kind": "fart"}
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(bad)


def test_event_validate_rejects_non_dict():
    # Arrange
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(["push"])


def test_event_validate_push_requires_repo():
    # Arrange
    bad = {"kind": "push", "branch": "develop", "commit_sha": "abc"}
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(bad)


def test_event_validate_push_requires_branch():
    # Arrange
    bad = {"kind": "push", "repo": "owner/repo", "commit_sha": "abc"}
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(bad)


def test_event_validate_push_requires_commit_sha():
    # Arrange
    bad = {"kind": "push", "repo": "owner/repo", "branch": "develop"}
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(bad)


def test_event_validate_done_requires_pr_number_int():
    # Arrange
    bad = {
        "kind": "done",
        "repo": "owner/repo",
        "pr_number": "187",
        "pr_url": "https://x.test/pull/187",
    }
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(bad)


def test_event_validate_done_requires_pr_url():
    # Arrange
    bad = {"kind": "done", "repo": "owner/repo", "pr_number": 187}
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(bad)


def test_event_validate_card_ids_must_be_list():
    # Arrange
    bad = {
        "kind": "push",
        "repo": "owner/repo",
        "branch": "develop",
        "commit_sha": "abc",
        "card_ids": "card-1",
    }
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(bad)


def test_event_validate_passes_valid_push():
    # Arrange
    good = {
        "kind": "push",
        "repo": "owner/repo",
        "branch": "develop",
        "commit_sha": "abc123def",
        "author": "me",
        "message": "x",
        "card_ids": ["card-1"],
    }
    # Act
    out = event_validate(good)
    # Assert — round-trip preserved.
    assert out["card_ids"] == ["card-1"]


def test_event_validate_passes_valid_done():
    # Arrange
    good = {
        "kind": "done",
        "repo": "owner/repo",
        "pr_number": 187,
        "pr_url": "https://x.test/pull/187",
        "card_ids": ["card-1"],
    }
    # Act
    out = event_validate(good)
    # Assert
    assert out["pr_number"] == 187


def test_event_validate_card_ids_default_to_empty_list():
    # Arrange — card_ids absent in payload.
    payload = {
        "kind": "push",
        "repo": "owner/repo",
        "branch": "develop",
        "commit_sha": "abc",
    }
    # Act
    out = event_validate(payload)
    # Assert
    assert out["card_ids"] == []


# === Built-in push handler — idempotent comment-append =====================


def _store_with(tmp_path: Path) -> Path:
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="card-1", title="x", assignee="agent:test-suite")
    return store


def test_push_appends_comment_to_card(tmp_path: Path, env):
    # Arrange
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    event = event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "develop",
            "commit_sha": "abc123def456",
            "message": "first push",
            "card_ids": ["card-1"],
        }
    )
    # Act
    summary = dispatch_event(event)
    # Assert
    assert summary["card_writes"][0]["action"] == "comment-appended"


def test_push_is_idempotent_when_commit_sha_already_recorded(
    tmp_path: Path,
    env,
):
    # Arrange — first push recorded; second one with same commit_sha
    # must be a noop.
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    event = event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "develop",
            "commit_sha": "abc123def456",
            "message": "first push",
            "card_ids": ["card-1"],
        }
    )
    dispatch_event(event)
    # Act
    summary = dispatch_event(event)
    # Assert
    assert summary["card_writes"][0]["action"] == "already-recorded"


def test_push_unknown_card_id_is_soft_noop(tmp_path: Path, env):
    # Arrange — producer hinted at a card that doesn't exist.
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    event = event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "develop",
            "commit_sha": "abc",
            "card_ids": ["never-existed"],
        }
    )
    # Act
    summary = dispatch_event(event)
    # Assert
    assert summary["card_writes"][0]["action"] == "card-not-found"


# === Built-in done handler — idempotent done+pr_url ========================


def test_done_flips_card_to_done_with_pr_url(tmp_path: Path, env):
    # Arrange
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    event = event_validate(
        {
            "kind": "done",
            "repo": "owner/repo",
            "pr_number": 187,
            "pr_url": "https://x.test/pull/187",
            "card_ids": ["card-1"],
        }
    )
    # Act
    dispatch_event(event)
    # Assert
    loaded = [t for t in list_tasks(store=store) if t["id"] == "card-1"][0]
    assert loaded.get("status") == "done"


def test_done_records_pr_url_on_card(tmp_path: Path, env):
    # Arrange
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    event = event_validate(
        {
            "kind": "done",
            "repo": "owner/repo",
            "pr_number": 187,
            "pr_url": "https://x.test/pull/187",
            "card_ids": ["card-1"],
        }
    )
    # Act
    dispatch_event(event)
    # Assert
    loaded = [t for t in list_tasks(store=store) if t["id"] == "card-1"][0]
    assert loaded.get("pr_url") == "https://x.test/pull/187"


def test_done_is_idempotent_when_already_done_with_same_pr_url(
    tmp_path: Path,
    env,
):
    # Arrange — first done recorded; second is a noop.
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    event = event_validate(
        {
            "kind": "done",
            "repo": "owner/repo",
            "pr_number": 187,
            "pr_url": "https://x.test/pull/187",
            "card_ids": ["card-1"],
        }
    )
    dispatch_event(event)
    # Act
    summary = dispatch_event(event)
    # Assert
    assert summary["card_writes"][0]["action"] == "noop"


# === Plugin failures are caught + reported, never propagated ==============


def test_dispatch_event_handles_plugin_error_gracefully(tmp_path: Path, env):
    # Arrange — register a real fake entry point whose handler raises,
    # injected through the dispatcher's `entry_points=` seam (no mock /
    # no monkeypatch; the PRODUCTION code path is what's being tested).
    class _FakeEP:
        name = "fake-failing"

        def load(self):
            def _bad(_event):
                raise RuntimeError("plugin exploded")

            return _bad

    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    event = event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "develop",
            "commit_sha": "xyz",
            "card_ids": ["card-1"],
        }
    )
    # Act — must NOT raise.
    summary = dispatch_event(event, entry_points=[_FakeEP()])
    # Assert
    assert summary["plugin_errors"][0]["plugin"] == "fake-failing"


# === unblock event — validate (ADR-0009) ==================================


def test_event_validate_unblock_requires_unlocker_id():
    # Arrange
    bad = {"kind": "unblock", "card_ids": ["b"]}
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(bad)


def test_event_validate_unblock_carries_unlocker_id():
    # Arrange
    payload = {"kind": "unblock", "unlocker_id": "a", "card_ids": ["b"]}
    # Act
    out = event_validate(payload)
    # Assert
    assert out["unlocker_id"] == "a"


def test_event_validate_unblock_normalizes_card_ids():
    # Arrange
    payload = {"kind": "unblock", "unlocker_id": "a", "card_ids": ["b", "c"]}
    # Act
    out = event_validate(payload)
    # Assert
    assert out["card_ids"] == ["b", "c"]


# === unblock built-in handler — idempotent [unblocked] comment ============


def test_unblock_handler_appends_comment_to_dependent(tmp_path: Path, env):
    # Arrange
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    event = event_validate(
        {"kind": "unblock", "unlocker_id": "blocker-x", "card_ids": ["card-1"]}
    )
    # Act
    summary = dispatch_event(event)
    # Assert
    assert summary["card_writes"][0]["action"] == "comment-appended"


def test_unblock_handler_is_idempotent_on_same_unlocker(tmp_path: Path, env):
    # Arrange — re-emitting the same unblock must not double-comment.
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    event = event_validate(
        {"kind": "unblock", "unlocker_id": "blocker-x", "card_ids": ["card-1"]}
    )
    dispatch_event(event)
    # Act
    summary = dispatch_event(event)
    # Assert
    assert summary["card_writes"][0]["action"] == "already-recorded"


def test_unblock_handler_unknown_card_is_soft_noop(tmp_path: Path, env):
    # Arrange
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    event = event_validate(
        {
            "kind": "unblock",
            "unlocker_id": "blocker-x",
            "card_ids": ["never-existed"],
        }
    )
    # Act
    summary = dispatch_event(event)
    # Assert
    assert summary["card_writes"][0]["action"] == "card-not-found"


# === card-message subscribers — optional notify list (ADR-0009) ===========


def test_event_validate_card_message_subscribers_default_empty():
    # Arrange — subscribers absent.
    payload = {"kind": "card-message", "card_id": "c", "body": "hi"}
    # Act
    out = event_validate(payload)
    # Assert
    assert out["subscribers"] == []


def test_event_validate_card_message_rejects_non_list_subscribers():
    # Arrange
    bad = {
        "kind": "card-message",
        "card_id": "c",
        "body": "hi",
        "subscribers": "alice",
    }
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(bad)


def test_event_validate_card_message_rejects_empty_subscriber_entry():
    # Arrange
    bad = {
        "kind": "card-message",
        "card_id": "c",
        "body": "hi",
        "subscribers": [""],
    }
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(bad)


def test_event_validate_card_message_passes_valid_subscribers():
    # Arrange
    payload = {
        "kind": "card-message",
        "card_id": "c",
        "body": "hi",
        "subscribers": ["alice", "bob"],
    }
    # Act
    out = event_validate(payload)
    # Assert
    assert out["subscribers"] == ["alice", "bob"]
