#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Honest empty state — a not-yet-created store renders "no cards yet".

Root cause (hub card hub-cards-board-data-404, second half): a fresh hub
workspace resolves to a store path whose tasks.yaml has not been created
yet. ``get_board`` already treated the absent file as an empty task list
(``if resolved.exists() else []``) but still called ``load_groups`` on it,
which raised FileNotFoundError → ``api_dispatch`` answered 400 "No task
store found." → the operator saw an error banner instead of an empty board.

Contract pinned here (per the operator's no-error-masking rule):

* READ endpoints (graph/tasks/rev) on an ABSENT store → 200 with an empty
  payload — the one legitimate "nothing here yet" case;
* genuine errors stay loud — unknown endpoint is still 404;
* WRITE endpoints stay loud — update on a missing id is still 404, and
  create materializes the store, which is the package's existing write-verb
  convention (``_read_write_doc(..., missing_ok=True)`` + parent mkdir).
"""

from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import ENV_LANE_GLOBS, _reset_cache  # noqa: E402


@pytest.fixture
def absent_store(tmp_path):
    """A store path whose file (and parent dir) do not exist yet.

    Lane discovery is opted out (explicitly-empty glob env — the documented
    opt-out) so the board is exactly the absent global store — no
    per-project lanes union in from the host running the tests. The prior
    env value is restored on teardown.
    """
    sentinel = object()
    prior = os.environ.get(ENV_LANE_GLOBS, sentinel)
    os.environ[ENV_LANE_GLOBS] = ""
    path = tmp_path / "fresh-workspace" / "tasks.yaml"
    _reset_cache()
    yield path
    _reset_cache()
    if prior is sentinel:
        os.environ.pop(ENV_LANE_GLOBS, None)
    else:
        os.environ[ENV_LANE_GLOBS] = prior


def _dispatch(endpoint, absent_store, method="get", body=None):
    """Drive views.api_dispatch against the absent store."""
    factory = RequestFactory()
    url = f"/{endpoint}?store={absent_store}"
    if method == "post":
        request = factory.post(
            url, data=json.dumps(body or {}), content_type="application/json"
        )
    else:
        request = factory.get(url)
    return views.api_dispatch(request, endpoint)


# --- read endpoints: empty success --------------------------------------------


def test_graph_on_absent_store_returns_http_200(absent_store):
    # Arrange
    endpoint = "graph"
    # Act
    response = _dispatch(endpoint, absent_store)
    # Assert
    assert response.status_code == 200


def test_graph_on_absent_store_returns_zero_nodes(absent_store):
    # Arrange
    endpoint = "graph"
    # Act
    payload = json.loads(_dispatch(endpoint, absent_store).content)
    # Assert
    assert payload["nodes"] == []


def test_graph_on_absent_store_returns_zero_edges(absent_store):
    # Arrange
    endpoint = "graph"
    # Act
    payload = json.loads(_dispatch(endpoint, absent_store).content)
    # Assert
    assert payload["edges"] == []


def test_tasks_on_absent_store_returns_empty_task_list(absent_store):
    # Arrange
    endpoint = "tasks"
    # Act
    payload = json.loads(_dispatch(endpoint, absent_store).content)
    # Assert
    assert payload["tasks"] == []


def test_rev_on_absent_store_reports_zero_count(absent_store):
    # Arrange
    endpoint = "rev"
    # Act
    payload = json.loads(_dispatch(endpoint, absent_store).content)
    # Assert
    assert payload["count"] == 0


# --- genuine errors stay loud --------------------------------------------------


def test_unknown_endpoint_on_absent_store_still_returns_404(absent_store):
    # Arrange
    endpoint = "bogus"
    # Act
    response = _dispatch(endpoint, absent_store)
    # Assert — the empty state must not swallow real routing errors.
    assert response.status_code == 404


def test_update_on_absent_store_returns_404_for_unknown_id(absent_store):
    # Arrange
    body = {"id": "no-such-card", "status": "done"}
    # Act
    response = _dispatch("update", absent_store, method="post", body=body)
    # Assert — writes against nothing stay loud, never empty-success.
    assert response.status_code == 404


# --- create materializes the store (existing write-verb convention) ------------


def test_create_on_absent_store_returns_http_200(absent_store):
    # Arrange
    body = {"title": "First card", "assignee": "operator"}
    # Act
    response = _dispatch("create", absent_store, method="post", body=body)
    # Assert
    assert response.status_code == 200


def test_create_on_absent_store_materializes_the_store_file(absent_store):
    # Arrange
    body = {"title": "First card", "assignee": "operator"}
    # Act
    _dispatch("create", absent_store, method="post", body=body)
    # Assert — add_task mkdirs the parent + writes the store (missing_ok).
    assert absent_store.exists()


# EOF
