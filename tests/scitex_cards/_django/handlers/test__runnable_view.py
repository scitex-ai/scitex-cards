#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.4 — /runnable + /blocked-batch Django endpoints.

Lead a2a `74db4f2d`, 2026-06-14. HTTP twins of `scitex-todo runnable`
+ `scitex-todo blocked` so the parallelism dispatcher consumes JSON
over HTTP.

Django RequestFactory; no mocks (STX-NM / PA-306). AAA pattern, one
assertion per test.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from django.test import RequestFactory

from scitex_cards._django.handlers.runnable import (
    blocked_batch_view,
    runnable_view,
)
from scitex_cards._store import add_task

# === fixtures ==============================================================


@pytest.fixture()
def store_with_runnable() -> Path:
    """Seed the canonical DB with one runnable + one blocked task.

    The store is SQLite now; the harness pins SCITEX_CARDS_TASKS_YAML_SHARED
    and SCITEX_CARDS_DB at a per-test scratch DB, and the view's ``get_board``
    -> ``resolve_tasks_path(None)`` reads that SAME store. Seed via
    ``add_task`` (store=None resolves the pinned store and writes the canonical
    DB) and return the pinned STORE-identity path (a provenance label only —
    NOT a tmp_path yaml, which would trip the store-stamp refusal)."""
    add_task(
        id="t-runnable",
        title="r",
        group="paper",
        assignee="agent:test-suite",
    )
    add_task(
        id="t-blocked",
        title="b",
        status="blocked",
        blocker="operator-decision",
        assignee="agent:test-suite",
    )
    return Path(os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"])


# === /runnable =============================================================


def test_runnable_view_returns_200(store_with_runnable):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/runnable")
    # Act
    response = runnable_view(req)
    # Assert
    assert response.status_code == 200


def test_runnable_view_payload_has_expected_keys(store_with_runnable):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/runnable")
    # Act
    response = runnable_view(req)
    payload = json.loads(response.content)
    # Assert
    assert set(payload.keys()) == {
        "tasks",
        "candidate_count",
        "blocked_by_deps_count",
    }


def test_runnable_view_lists_runnable_task(store_with_runnable):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/runnable")
    # Act
    payload = json.loads(runnable_view(req).content)
    # Assert
    assert [t["id"] for t in payload["tasks"]] == ["t-runnable"]


def test_runnable_view_group_filter_narrows(store_with_runnable):
    # Arrange — filter to a group the runnable task does NOT belong to.
    rf = RequestFactory()
    req = rf.get("/runnable", {"group": "ci-recovery"})
    # Act
    payload = json.loads(runnable_view(req).content)
    # Assert
    assert payload["tasks"] == []


def test_runnable_view_group_empty_string_means_ungrouped(store_with_runnable):
    # Arrange — the runnable task has group=paper, so '' (ungrouped
    # only) should return nothing.
    rf = RequestFactory()
    req = rf.get("/runnable", {"group": ""})
    # Act
    payload = json.loads(runnable_view(req).content)
    # Assert
    assert payload["tasks"] == []


def test_runnable_view_method_post_returns_405(store_with_runnable):
    # Arrange — POST is not allowed on this read-only endpoint.
    rf = RequestFactory()
    req = rf.post("/runnable")
    # Act
    response = runnable_view(req)
    # Assert
    assert response.status_code == 405


# === /blocked-batch ========================================================


def test_blocked_batch_view_returns_200(store_with_runnable):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/blocked-batch")
    # Act
    response = blocked_batch_view(req)
    # Assert
    assert response.status_code == 200


def test_blocked_batch_view_payload_has_expected_keys(store_with_runnable):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/blocked-batch")
    # Act
    payload = json.loads(blocked_batch_view(req).content)
    # Assert
    assert set(payload.keys()) == {"tasks", "total", "by_reason"}


def test_blocked_batch_view_lists_blocked_task(store_with_runnable):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/blocked-batch")
    # Act
    payload = json.loads(blocked_batch_view(req).content)
    # Assert
    assert [t["id"] for t in payload["tasks"]] == ["t-blocked"]


def test_blocked_batch_view_carries_reason_and_chain(store_with_runnable):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/blocked-batch")
    # Act
    payload = json.loads(blocked_batch_view(req).content)
    # Assert
    blocked = payload["tasks"][0]
    assert blocked["reason"] == "explicit-blocker" and blocked["chain"] == [
        "operator-decision"
    ]


def test_blocked_batch_view_by_reason_histogram(store_with_runnable):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/blocked-batch")
    # Act
    payload = json.loads(blocked_batch_view(req).content)
    # Assert
    assert payload["by_reason"]["explicit-blocker"] == 1


def test_blocked_batch_view_method_post_returns_405(store_with_runnable):
    # Arrange
    rf = RequestFactory()
    req = rf.post("/blocked-batch")
    # Act
    response = blocked_batch_view(req)
    # Assert
    assert response.status_code == 405
