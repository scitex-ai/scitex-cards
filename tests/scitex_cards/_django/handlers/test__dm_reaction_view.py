#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for ``POST /dm/thread/<peer>/reaction`` and the reactions the thread
endpoint now returns alongside its messages.

Contract:

  - POST appends ONE reaction event and answers with the message's refolded
    state, so the tapping client paints what every other client will see.
  - The thread is DERIVED from the URL's peer; the body never names it.
  - GET /dm/thread/<peer> gains a "reactions" key and its "messages" records
    are byte-identical to before (the v5 design freezes the record).
  - 400 on a missing message_id / emoji / unknown action; 405 on GET.

Django RequestFactory against a real tmp store via ``?store=``; no mocks
(STX-NM / PA-306). AAA pattern, one assertion per test (STX-TQ007).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.test import RequestFactory

from scitex_cards._django.handlers.dm import (
    STORE_REQUEST_ATTR,
    dm_reaction_view,
    dm_thread_view,
)
from scitex_cards._threads import append_message


@pytest.fixture()
def store(tmp_path: Path, env) -> Path:
    """A real tmp tasks.yaml (both sidecars land next to it)."""
    env.set("SCITEX_CARDS_STORE_GIT_AUTOCOMMIT", "0")
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return path


@pytest.fixture()
def message(store: Path) -> dict:
    """One agent→operator message to react to."""
    return append_message("agent-x", "operator", "deploy is green", store=store)


def _post_reaction(store: Path, payload: dict, peer: str = "agent-x"):
    """A reaction is a WRITE, so it is scoped the way writes are now scoped.

    Through the trusted request attribute, exactly as scitex-hub's tenancy
    middleware sets it — not through ``?store=``, which a write no longer
    honours. Scoping it through the query would have this exercise a path
    production refuses.
    """
    request = RequestFactory().post(
        f"/dm/thread/{peer}/reaction",
        data=json.dumps(payload),
        content_type="application/json",
    )
    setattr(request, STORE_REQUEST_ATTR, str(store))
    return dm_reaction_view(request, peer)


def _react(
    store: Path,
    message: dict,
    emoji: str = "👍",
    action: str = "add",
    peer: str = "agent-x",
):
    return _post_reaction(
        store,
        {"message_id": message["id"], "emoji": emoji, "action": action},
        peer=peer,
    )


def _get_thread(store: Path, peer: str = "agent-x"):
    request = RequestFactory().get(f"/dm/thread/{peer}?store={store}")
    return dm_thread_view(request, peer)


@pytest.fixture()
def reacted(store: Path, message: dict) -> tuple:
    """The operator has reacted 👍 to the message."""
    return store, message, _react(store, message)


# === POST /dm/thread/<peer>/reaction =======================================


def test_reaction_post_returns_ok(reacted: tuple):
    # Arrange
    _store, _message, response = reacted
    # Act
    status = response.status_code
    # Assert
    assert status == 200


def test_reaction_post_answers_with_the_refolded_state(reacted: tuple):
    # Arrange
    _store, _message, response = reacted
    # Act
    data = json.loads(response.content)
    # Assert
    assert data["reactions"] == {"👍": ["operator"]}


def test_reaction_post_attributes_the_event_to_the_operator(reacted: tuple):
    # Arrange
    _store, _message, response = reacted
    # Act
    event = json.loads(response.content)["event"]
    # Assert
    assert event["actor"] == "operator"


def test_reaction_post_derives_the_thread_from_the_peer(reacted: tuple):
    # Arrange
    _store, _message, response = reacted
    # Act
    event = json.loads(response.content)["event"]
    # Assert
    # the caller named a peer and a message, never a thread.
    assert event["thread"] == "dm:agent-x::operator"


def test_removing_a_reaction_empties_the_fold(store: Path, message: dict):
    # Arrange
    _react(store, message)
    response = _react(store, message, action="remove")
    # Act
    data = json.loads(response.content)
    # Assert
    assert data["reactions"] == {}


def test_reaction_post_rejects_a_missing_message_id(store: Path):
    # Arrange
    # Act
    response = _post_reaction(store, {"emoji": "👍"})
    # Assert
    assert response.status_code == 400


def test_reaction_post_rejects_a_missing_emoji(store: Path, message: dict):
    # Arrange
    # Act
    response = _post_reaction(store, {"message_id": message["id"]})
    # Assert
    assert response.status_code == 400


def test_reaction_post_rejects_an_unknown_action(store: Path, message: dict):
    # Arrange
    # Act
    response = _post_reaction(
        store, {"message_id": message["id"], "emoji": "👍", "action": "explode"}
    )
    # Assert
    assert response.status_code == 400


def test_reaction_post_rejects_invalid_json(store: Path):
    # Arrange
    request = RequestFactory().post(
        f"/dm/thread/agent-x/reaction?store={store}",
        data="{not json",
        content_type="application/json",
    )
    # Act
    response = dm_reaction_view(request, "agent-x")
    # Assert
    assert response.status_code == 400


def test_reaction_view_rejects_get(store: Path):
    # Arrange
    request = RequestFactory().get(f"/dm/thread/agent-x/reaction?store={store}")
    # Act
    response = dm_reaction_view(request, "agent-x")
    # Assert
    assert response.status_code == 405


# === GET /dm/thread/<peer> now carries reactions ===========================


def test_thread_view_returns_an_empty_reactions_map_by_default(
    store: Path, message: dict
):
    # Arrange
    response = _get_thread(store)
    # Act
    data = json.loads(response.content)
    # Assert
    assert data["reactions"] == {}


def test_thread_view_returns_the_reaction_keyed_by_message(reacted: tuple):
    # Arrange
    store, message, _response = reacted
    response = _get_thread(store)
    # Act
    data = json.loads(response.content)
    # Assert
    assert data["reactions"] == {message["id"]: {"👍": ["operator"]}}


def test_thread_view_message_records_are_unchanged_by_a_reaction(
    store: Path, message: dict
):
    # Arrange
    before = json.loads(_get_thread(store).content)["messages"]
    _react(store, message)
    # Act
    after = json.loads(_get_thread(store).content)["messages"]
    # Assert
    # reactions ride ALONGSIDE the records, never inside them.
    assert after == before


def test_a_reaction_on_another_thread_does_not_leak_in(store: Path, message: dict):
    # Arrange
    other = append_message("agent-y", "operator", "unrelated", store=store)
    _react(store, other, emoji="🎉", peer="agent-y")
    response = _get_thread(store, peer="agent-x")
    # Act
    data = json.loads(response.content)
    # Assert
    assert data["reactions"] == {}


# EOF
