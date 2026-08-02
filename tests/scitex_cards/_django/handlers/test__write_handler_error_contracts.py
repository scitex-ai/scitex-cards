#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Error-payload contracts for the delegated write handlers.

Pins the three repairs applied after the lock-bypass conversion's
adversarial review (2026-07-17):

1. ``handle_edge``'s 404 names the ACTUALLY-missing id even when the
   missing id itself contains the substring ``"source"`` (the old code
   substring-matched the exception text and reported the wrong card).
2. ``handle_update`` rejects an EXPLICIT ``{"status": null}`` with a 400
   instead of forwarding None to ``update_task``, which would DELETE the
   status key — a status-less card drops out of every lane, the state the
   verb's own contract forbids.
3. ``handle_comment`` turns ``comment_task``'s ``TaskNotFoundError`` (a
   lane-only card, or a delete landing after the cached fast-path) into a
   clean 404, matching ``handle_update``, instead of bubbling a 500.
"""

from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("django")

from conftest import (
    seed_db_from_doc,  # noqa: E402  (re-exported by _django/conftest.py)
)
from django.test import RequestFactory  # noqa: E402

from scitex_cards._django.handlers import crud  # noqa: E402
from scitex_cards._django.handlers import edge as edge_handlers  # noqa: E402
from scitex_cards._django.services import _reset_cache, get_board  # noqa: E402
from scitex_cards._model import load_tasks  # noqa: E402
from scitex_cards._yaml import safe_load  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: build, title: Build It, status: in_progress,"
    " agent: alice, assignee: alice}\n"
    "  - {id: my-source-card, title: Source-ish, status: deferred,"
    " agent: bob, assignee: bob}\n"
)


@pytest.fixture
def store(env):
    # Hermetic: no per-project lane union from the real ~/proj tree.
    env.set("SCITEX_TODO_LANE_GLOBS", "")
    # SQLite store: seed the prior cards into the canonical DB, then hand the
    # handlers the PINNED store-identity path (never a tmp_path YAML — a write
    # stamped with a tmp path fails the next read's ownership check). The DB is
    # authoritative for content; the path is a provenance label. The
    # board/services layer (get_board -> load_groups) still stat()s the identity
    # file, but an autouse fixture in _django/conftest.py guarantees it exists.
    seed_db_from_doc(safe_load(_STORE_TEXT) or {}, os.environ["SCITEX_CARDS_DB"])
    store_path = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _reset_cache()
    yield store_path
    _reset_cache()


def _request(endpoint, body):
    return RequestFactory().post(
        f"/{endpoint}",
        data=json.dumps(body),
        content_type="application/json",
    )


def _stale_board(store_path):
    """A BoardState snapshot that will NOT see writes landing after it."""
    _reset_cache()
    board = get_board(store_path)
    _reset_cache()
    return board


def _tasks_by_id(store_path):
    # Read back through the canonical store (SQLite); the path is a label only.
    return {t["id"]: t for t in load_tasks(store_path)}


def _edge_onto_a_concurrently_deleted_target(store_path):
    """Add a ``blocks`` edge whose TARGET vanished after the board snapshot.

    The missing id contains the substring ``"source"``, which is exactly what
    the old substring-matching 404 reported the wrong card for.
    """
    from scitex_cards._store import delete_task

    board = _stale_board(store_path)
    delete_task(store_path, "my-source-card")
    return edge_handlers.handle_edge(
        _request(
            "edge",
            {
                "action": "add",
                "kind": "blocks",
                "source": "build",
                "target": "my-source-card",
            },
        ),
        board,
    )


def _update_build_with_null_status(store_path):
    """Send the payload no GUI control emits but a raw client can."""
    board = _stale_board(store_path)
    return crud.handle_update(
        _request("update", {"id": "build", "status": None}), board
    )


def _comment_on_a_concurrently_deleted_card(store_path):
    """Comment on a card that passes the cached fast-path, then vanishes."""
    from scitex_cards._store import delete_task

    board = _stale_board(store_path)
    delete_task(store_path, "build")
    return crud.handle_comment(
        _request("comment", {"id": "build", "text": "still there?"}), board
    )


def test_edge_onto_a_deleted_target_answers_404(store):
    # Arrange
    store_path = store
    # Act
    response = _edge_onto_a_concurrently_deleted_target(store_path)
    # Assert
    assert response.status_code == 404


def test_edge_404_names_missing_id_even_when_it_contains_source(store):
    # Arrange
    store_path = store
    # Act
    response = _edge_onto_a_concurrently_deleted_target(store_path)
    # Assert — the 404 names the card that is actually missing, not `build`.
    assert "my-source-card" in json.loads(response.content)["error"]


def test_update_with_an_explicit_null_status_answers_400(store):
    # Arrange
    store_path = store
    # Act
    response = _update_build_with_null_status(store_path)
    # Assert — loud 400 rather than a silent status-key deletion.
    assert response.status_code == 400


def test_update_null_status_error_names_the_status_field(store):
    # Arrange
    store_path = store
    # Act
    response = _update_build_with_null_status(store_path)
    # Assert
    assert "status" in json.loads(response.content)["error"]


def test_update_null_status_leaves_the_card_status_intact(store):
    # Arrange
    store_path = store
    # Act
    _update_build_with_null_status(store_path)
    # Assert — the card keeps both its status key and its value.
    assert _tasks_by_id(store_path)["build"]["status"] == "in_progress"


def test_comment_on_concurrently_deleted_card_is_404_not_500(store):
    # Arrange
    store_path = store
    # Act
    response = _comment_on_a_concurrently_deleted_card(store_path)
    # Assert
    assert response.status_code == 404


def test_comment_404_names_the_card_that_vanished(store):
    # Arrange
    store_path = store
    # Act
    response = _comment_on_a_concurrently_deleted_card(store_path)
    # Assert
    assert "build" in json.loads(response.content)["error"]


# EOF
