#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the POST /priority handler -- drag-reorder persistence.

Mirrors ``src/scitex_cards/_django/handlers/priority.py``. Drives
``views.api_dispatch`` with a real ``RequestFactory`` POST; no mocks
(STX-NM / PA-306).

SQLite cutover: card DATA lives in the canonical DB (a path-independent read),
so the baseline cards are seeded THERE via ``seed_db_from_doc``; the handler is
handed the PINNED store-identity path (never a tmp_path YAML — a write stamped
with a tmp path fails the next read's ownership check). The reorder is read
back through ``load_tasks`` (the DB), not off a YAML file. The board/services
layer still stat()s the identity file, so it must EXIST even though its content
is never read — an empty file is enough.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("django")

from conftest import seed_db_from_doc  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers import graph as _graph_mod  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402
from scitex_cards._model import load_tasks  # noqa: E402

# Three connected tasks with a deterministic baseline priority layout.
# `gate` blocks `build`, and `north` depends on `build`. All three start with
# priorities 5/6/7 so we can prove the handler actually overwrites them.
_STORE_DOC = {
    "tasks": [
        {
            "id": "north",
            "title": "North Star",
            "status": "goal",
            "depends_on": ["build"],
            "priority": 5,
        },
        {"id": "build", "title": "Build It", "status": "in_progress", "priority": 6},
        {
            "id": "gate",
            "title": "Gate",
            "status": "blocked",
            "blocks": ["build"],
            "priority": 7,
        },
    ]
}


@pytest.fixture
def store():
    """Seed the canonical DB and hand the handler the pinned store-identity path."""
    seed_db_from_doc(_STORE_DOC, os.environ["SCITEX_CARDS_DB"])
    store_path = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    Path(store_path).write_text("", encoding="utf-8")
    _reset_cache()
    _graph_mod._graph_cache_reset()
    yield store_path
    _reset_cache()
    _graph_mod._graph_cache_reset()


def _post_priority(store_path, body):
    """Drive views.api_dispatch for a POST /priority request."""
    request = RequestFactory().post(
        f"/priority?store={store_path}",
        data=json.dumps(body),
        content_type="application/json",
    )
    return views.api_dispatch(request, "priority")


def _load_priorities(store_path):
    """Read the canonical store (SQLite) and return {id: priority}."""
    return {t["id"]: t.get("priority") for t in load_tasks(store_path)}


def test_priority_endpoint_returns_ok_on_post(store):
    # Arrange
    body = {"order": ["build", "gate", "north"]}
    # Act
    response = _post_priority(store, body)
    # Assert
    assert response.status_code == 200


def test_priority_endpoint_assigns_sequential_priorities(store):
    # Arrange — order says build=1, gate=2, north=3.
    body = {"order": ["build", "gate", "north"]}
    # Act
    _post_priority(store, body)
    # Assert — the canonical store now reflects the new priorities.
    priorities = _load_priorities(store)
    assert priorities == {"build": 1, "gate": 2, "north": 3}


def test_priority_endpoint_response_lists_updated_ids(store):
    # Arrange
    body = {"order": ["gate", "build"]}
    # Act
    response = _post_priority(store, body)
    payload = json.loads(response.content)
    # Assert
    assert payload["updated"] == ["gate", "build"]


def test_priority_endpoint_response_includes_store_path(store):
    # Arrange
    body = {"order": ["build"]}
    # Act
    response = _post_priority(store, body)
    payload = json.loads(response.content)
    # Assert
    assert payload["store_path"] == store


@pytest.fixture
def partial_reorder_priorities(store):
    """Reorder only `build` and `gate` (omitting `north`), returning the
    on-disk {id: priority} after the POST so each assertion stays single-Act."""
    _post_priority(store, {"order": ["gate", "build"]})
    return _load_priorities(store)


def test_priority_endpoint_leaves_unlisted_task_at_original_rank(
    partial_reorder_priorities,
):
    # Arrange
    priorities = partial_reorder_priorities
    # Act
    north_rank = priorities["north"]
    # Assert — `north` was omitted from the order, so it keeps its original 5.
    assert north_rank == 5


def test_priority_endpoint_ranks_first_listed_task_one(partial_reorder_priorities):
    # Arrange
    priorities = partial_reorder_priorities
    # Act
    gate_rank = priorities["gate"]
    # Assert — `gate` was listed first, so it ranks 1.
    assert gate_rank == 1


def test_priority_endpoint_ranks_second_listed_task_two(partial_reorder_priorities):
    # Arrange
    priorities = partial_reorder_priorities
    # Act
    build_rank = priorities["build"]
    # Assert — `build` was listed second, so it ranks 2.
    assert build_rank == 2


@pytest.fixture
def unknown_id_reorder(store):
    """POST an order containing an unknown id (`ghost`), returning the
    (response payload, on-disk priorities) tuple after the POST."""
    response = _post_priority(store, {"order": ["ghost", "build", "gate"]})
    payload = json.loads(response.content)
    return payload, _load_priorities(store)


def test_priority_endpoint_response_omits_unknown_ids(unknown_id_reorder):
    # Arrange
    payload, _priorities = unknown_id_reorder
    # Act
    updated = payload["updated"]
    # Assert — response only lists ids that actually existed in the store.
    assert updated == ["build", "gate"]


def test_priority_endpoint_ranks_by_full_order_position(unknown_id_reorder):
    # Arrange
    _payload, priorities = unknown_id_reorder
    # Act
    build_rank = priorities["build"]
    # Assert — ranks follow position in the full `order` list, so build=2.
    assert build_rank == 2


def test_priority_endpoint_skips_unknown_without_shifting_ranks(unknown_id_reorder):
    # Arrange
    _payload, priorities = unknown_id_reorder
    # Act
    gate_rank = priorities["gate"]
    # Assert — `gate` is third in `order`, so it ranks 3.
    assert gate_rank == 3


def test_priority_endpoint_rejects_get_with_405(store):
    # Arrange
    request = RequestFactory().get(f"/priority?store={store}")
    # Act
    response = views.api_dispatch(request, "priority")
    # Assert
    assert response.status_code == 405


def test_priority_endpoint_rejects_invalid_json_with_400(store):
    # Arrange — POST a body that is not valid JSON.
    request = RequestFactory().post(
        f"/priority?store={store}",
        data="{not json",
        content_type="application/json",
    )
    # Act
    response = views.api_dispatch(request, "priority")
    # Assert
    assert response.status_code == 400


def test_priority_endpoint_rejects_missing_order_with_400(store):
    # Arrange — well-formed JSON but no `order` key.
    body = {"something_else": []}
    # Act
    response = _post_priority(store, body)
    # Assert
    assert response.status_code == 400


def test_priority_endpoint_rejects_non_string_ids_with_400(store):
    # Arrange — `order` must be a list of strings; ints are an error.
    body = {"order": [1, 2, 3]}
    # Act
    response = _post_priority(store, body)
    # Assert
    assert response.status_code == 400


@pytest.fixture
def two_card_store_after_reorder():
    """Seed a two-card store (alpha/beta), reorder [beta, alpha], and return
    the {id: priority} read back through the canonical store.

    (Under the SQLite cutover the old ``commented.yaml`` variant has no
    subject: the handler writes the DB, never a YAML file, so there is no
    hand-written comment to preserve or drop — that assertion tested a
    YAML round-trip that no longer exists and is retired. The priorities-are-
    applied assertion below survives verbatim.)"""
    seed_db_from_doc(
        {
            "tasks": [
                {"id": "alpha", "title": "First", "status": "pending"},
                {"id": "beta", "title": "Second", "status": "pending"},
            ]
        },
        os.environ["SCITEX_CARDS_DB"],
    )
    store_path = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    Path(store_path).write_text("", encoding="utf-8")
    _reset_cache()
    _post_priority(store_path, {"order": ["beta", "alpha"]})
    priorities = _load_priorities(store_path)
    _reset_cache()
    return priorities


def test_priority_endpoint_applies_priorities_to_two_card_store(
    two_card_store_after_reorder,
):
    # Arrange
    priorities = two_card_store_after_reorder
    # Act
    result = priorities
    # Assert — priorities follow the posted order.
    assert result == {"beta": 1, "alpha": 2}


@pytest.fixture
def graph_after_reorder(store):
    """Prime the board+graph cache via /graph, mutate via POST /priority, then
    return the post-write {id: node} map from a follow-up /graph.

    Pins the read-your-own-writes fix (TASK 2): the /graph payload cache keys
    on ``board.sig`` (the DB content version), so the follow-up GET rebuilds
    from the reordered DB instead of returning the stale first payload — even
    though the priority write never moves the identity file's mtime."""
    request = RequestFactory().get(f"/graph?store={store}")
    views.api_dispatch(request, "graph")
    _post_priority(store, {"order": ["gate", "build", "north"]})
    request = RequestFactory().get(f"/graph?store={store}")
    payload = json.loads(views.api_dispatch(request, "graph").content)
    return {n["id"]: n for n in payload["nodes"]}


def test_priority_endpoint_invalidates_cache_for_first_node(graph_after_reorder):
    # Arrange
    by_id = graph_after_reorder
    # Act
    gate_rank = by_id["gate"]["priority"]
    # Assert — follow-up GET re-reads the store, so `gate` ranks 1, not cached.
    assert gate_rank == 1


def test_priority_endpoint_invalidates_cache_for_second_node(graph_after_reorder):
    # Arrange
    by_id = graph_after_reorder
    # Act
    build_rank = by_id["build"]["priority"]
    # Assert
    assert build_rank == 2


def test_priority_endpoint_invalidates_cache_for_third_node(graph_after_reorder):
    # Arrange
    by_id = graph_after_reorder
    # Act
    north_rank = by_id["north"]["priority"]
    # Assert
    assert north_rank == 3


# EOF
