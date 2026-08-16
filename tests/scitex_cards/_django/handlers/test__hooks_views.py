#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP endpoint tests for `/hooks/push` and `/hooks/done`.

Lead a2a `6fff33d6` + `fbffb879`, 2026-06-14. Pins the loose-coupling
contract for SAC's push-hook + dev's merge-Action callers.

Django RequestFactory; no mocks (STX-NM / PA-306).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.test import RequestFactory

from scitex_cards._django.handlers.hooks import (
    hook_done_view,
    hook_push_view,
)
from scitex_cards._store import add_task


@pytest.fixture()
def store_with_card(tmp_path: Path, env) -> Path:
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="card-1", title="x", assignee="agent:test-suite")
    env.set("SCITEX_CARDS_TASKS_YAML_SHARED", str(store))
    return store


def _post_json(rf: RequestFactory, path: str, payload: dict):
    return rf.post(
        path,
        data=json.dumps(payload),
        content_type="application/json",
    )


# === /hooks/push ===========================================================


def test_push_view_returns_200_on_valid_payload(store_with_card):
    # Arrange
    rf = RequestFactory()
    req = _post_json(
        rf,
        "/hooks/push",
        {
            "repo": "owner/repo",
            "branch": "develop",
            "commit_sha": "abc",
            "card_ids": ["card-1"],
        },
    )
    # Act
    response = hook_push_view(req)
    # Assert
    assert response.status_code == 200


def test_push_view_get_returns_405(store_with_card):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/hooks/push")
    # Act
    response = hook_push_view(req)
    # Assert
    assert response.status_code == 405


def test_push_view_bad_json_returns_400(store_with_card):
    # Arrange
    rf = RequestFactory()
    req = rf.post(
        "/hooks/push",
        data="not json",
        content_type="application/json",
    )
    # Act
    response = hook_push_view(req)
    # Assert
    assert response.status_code == 400


def test_push_view_missing_repo_returns_400(store_with_card):
    # Arrange
    rf = RequestFactory()
    req = _post_json(
        rf,
        "/hooks/push",
        {
            "branch": "develop",
            "commit_sha": "abc",
        },
    )
    # Act
    response = hook_push_view(req)
    # Assert
    assert response.status_code == 400


def test_push_view_rejects_wrong_kind_in_payload(store_with_card):
    # Arrange — endpoint binds expected kind; mismatch is a 400.
    rf = RequestFactory()
    req = _post_json(
        rf,
        "/hooks/push",
        {
            "kind": "done",
            "repo": "owner/repo",
            "branch": "develop",
            "commit_sha": "abc",
        },
    )
    # Act
    response = hook_push_view(req)
    # Assert
    assert response.status_code == 400


# === /hooks/done ===========================================================


def test_done_view_returns_200_on_valid_payload(store_with_card):
    # Arrange
    rf = RequestFactory()
    req = _post_json(
        rf,
        "/hooks/done",
        {
            "repo": "owner/repo",
            "pr_number": 187,
            "pr_url": "https://x.test/pull/187",
            "card_ids": ["card-1"],
        },
    )
    # Act
    response = hook_done_view(req)
    # Assert
    assert response.status_code == 200


def test_done_view_get_returns_405(store_with_card):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/hooks/done")
    # Act
    response = hook_done_view(req)
    # Assert
    assert response.status_code == 405


def test_done_view_idempotent_repost_returns_noop_action(store_with_card):
    # Arrange — first POST records done; second POST is a noop.
    rf = RequestFactory()
    body = {
        "repo": "owner/repo",
        "pr_number": 187,
        "pr_url": "https://x.test/pull/187",
        "card_ids": ["card-1"],
    }
    hook_done_view(_post_json(rf, "/hooks/done", body))
    # Act
    response = hook_done_view(_post_json(rf, "/hooks/done", body))
    payload = json.loads(response.content)
    # Assert
    assert payload["card_writes"][0]["action"] == "noop"


def test_done_view_response_carries_summary_keys(store_with_card):
    # Arrange
    rf = RequestFactory()
    body = {
        "repo": "owner/repo",
        "pr_number": 187,
        "pr_url": "https://x.test/pull/187",
        "card_ids": ["card-1"],
    }
    # Act
    response = hook_done_view(_post_json(rf, "/hooks/done", body))
    payload = json.loads(response.content)
    # Assert — public surface SAC + dev rely on.
    assert set(payload.keys()) >= {
        "kind",
        "card_writes",
        "plugin_count",
        "plugin_errors",
    }
