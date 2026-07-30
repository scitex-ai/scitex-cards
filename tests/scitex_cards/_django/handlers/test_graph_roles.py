#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the USER-role fields on each GET /graph node payload.

scitex-todo's entity is the USER (an agent is just ``user.kind=agent``).
The board's detail drawer renders a ROLES section — Creator / Assignee /
Collaborators / Subscribers — straight off the ``/graph`` node dict, so
the node payload MUST emit those fields. This covers the wire contract:

  * ``created_by`` is forwarded verbatim (None on legacy rows that
    predate the field — the FE then falls back to the earliest comment
    author, else "—")
  * ``collaborators`` / ``subscribers`` are always emitted as lists
    (empty list when absent) so the FE can render "—" without
    null-checks
  * ``agent`` (the assignee in user terms) keeps being emitted

Real ``RequestFactory`` GET against the canonical SQLite store — no mocks
(STX-NM / PA-306). Mirrors the pattern in ``test_graph_fleet.py``.
One assertion per test (STX-TQ007).
"""

from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("django")

from conftest import seed_db_from_doc  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402
from scitex_cards._yaml import safe_load  # noqa: E402


def _store_text() -> str:
    """Two tasks: one fully-roled, one legacy (no role fields at all)."""
    return (
        "tasks:\n"
        "  - id: roled\n"
        "    title: 'fully roled card'\n"
        "    status: pending\n"
        "    agent: agent:assignee\n"
        "    created_by: agent:creator\n"
        "    collaborators:\n"
        "      - agent:collab-a\n"
        "      - agent:collab-b\n"
        "    subscribers:\n"
        "      - agent:sub-a\n"
        "  - id: legacy\n"
        "    title: 'legacy card with no role fields'\n"
        "    status: pending\n"
    )


@pytest.fixture
def store():
    # SQLite store: seed the two cards into the canonical DB and hand the graph
    # view the PINNED store-identity path (never a tmp_path YAML — a write
    # stamped with a tmp path would fail the next read's ownership check). The
    # DB is authoritative for content; the view ignores the path except as a
    # provenance label. The board/services layer (get_board -> load_groups)
    # stat()s the identity file, which the _django autouse fixture already
    # creates at the pinned path.
    seed_db_from_doc(safe_load(_store_text()) or {}, os.environ["SCITEX_CARDS_DB"])
    store_path = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _reset_cache()
    yield store_path
    _reset_cache()


def _nodes_by_id(store_path: str) -> dict:
    request = RequestFactory().get(f"/graph?store={store_path}")
    response = views.api_dispatch(request, "graph")
    if response.status_code != 200:
        raise AssertionError(f"GET /graph failed: {response.content!r}")
    payload = json.loads(response.content)
    return {n["id"]: n for n in payload["nodes"]}


def test_node_emits_created_by(store):
    # Arrange
    # Act
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["roled"]["created_by"] == "agent:creator"


def test_node_emits_collaborators_list(store):
    # Arrange
    # Act
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["roled"]["collaborators"] == ["agent:collab-a", "agent:collab-b"]


def test_node_emits_subscribers_list(store):
    # Arrange
    # Act
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["roled"]["subscribers"] == ["agent:sub-a"]


def test_node_still_emits_assignee_agent(store):
    # Arrange
    # Act
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["roled"]["agent"] == "agent:assignee"


def test_legacy_node_created_by_is_none(store):
    # Arrange
    # a legacy card predates created_by; the FE falls back to the
    # earliest comment author, else "—".
    # Act
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["legacy"]["created_by"] is None


def test_legacy_node_collaborators_is_an_empty_list(store):
    # Arrange
    # absent role lists emit as [] (the FE renders "—").
    # Act
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["legacy"]["collaborators"] == []


def test_legacy_node_subscribers_is_an_empty_list(store):
    # Arrange
    # Act
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["legacy"]["subscribers"] == []


# EOF
