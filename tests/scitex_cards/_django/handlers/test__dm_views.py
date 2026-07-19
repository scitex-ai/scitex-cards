#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``/dm/*`` Django endpoints (operator↔agent direct messages).

Minimal-slice contract (card fleet-agent-direct-message-board-pane-20260707):

  - GET  /dm/threads      → registry agents ∪ thread peers, with unread + last.
  - GET  /dm/thread/<p>   → chronological messages; mark_read=1 acks.
  - POST /dm/thread/<p>   → appends from=operator, dm-dispatches to the
                            agent's pull-inbox; 400 on empty body.
  - 405 on other verbs.

Django RequestFactory against a real tmp store via ``?store=``; no mocks
(STX-NM / PA-306). AAA pattern, one assertion per test (STX-TQ007).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.test import RequestFactory

from scitex_cards._django.handlers.dm import dm_thread_view, dm_threads_view
from scitex_cards._inbox import poll_inbox
from scitex_cards._threads import append_message, get_thread


@pytest.fixture()
def store(tmp_path: Path, env) -> Path:
    """A real tmp tasks.yaml (threads sidecar lands next to it)."""
    env.set("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "0")
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return path


def _get(url):
    return RequestFactory().get(url)


def _agents_of(response) -> list:
    return json.loads(response.content)["agents"]


def _threads_with_one_inbound(store):
    """One inbound (agent→operator) message, no registry entries."""
    append_message("agent-x", "operator", "ping", store=store)
    return dm_threads_view(_get(f"/dm/threads?store={store}"))


def _threads_with_a_silent_registry_agent(store):
    """A registered agent that has never sent or received a message."""
    from scitex_cards._users import register_user

    register_user(kind="agent", names=["agent-quiet"], store=store)
    return dm_threads_view(_get(f"/dm/threads?store={store}"))


def _post_operator_message(store, body: str):
    request = RequestFactory().post(
        f"/dm/thread/agent-x?store={store}",
        data=json.dumps({"body": body}),
        content_type="application/json",
    )
    return dm_thread_view(request, "agent-x")


# === /dm/threads ===========================================================


def test_threads_view_returns_ok_for_a_thread_peer(store):
    # Arrange
    # one inbound thread, no registry.
    # Act
    response = _threads_with_one_inbound(store)
    # Assert
    assert response.status_code == 200


def test_threads_view_lists_the_thread_peer_by_name(store):
    # Arrange
    response = _threads_with_one_inbound(store)
    # Act
    agents = _agents_of(response)
    # Assert
    assert [a["name"] for a in agents] == ["agent-x"]


def test_threads_view_counts_the_unread_inbound_message(store):
    # Arrange
    response = _threads_with_one_inbound(store)
    # Act
    agents = _agents_of(response)
    # Assert
    assert agents[0]["unread"] == 1


def test_threads_view_exposes_the_last_message_body(store):
    # Arrange
    response = _threads_with_one_inbound(store)
    # Act
    agents = _agents_of(response)
    # Assert
    assert agents[0]["last_body"] == "ping"


def test_threads_view_includes_registry_agents_without_threads(store):
    # Arrange
    response = _threads_with_a_silent_registry_agent(store)
    # Act
    agents = _agents_of(response)
    # Assert
    # registered but silent agents still appear (composable-to).
    assert [a["name"] for a in agents] == ["agent-quiet"]


def test_silent_registry_agent_has_no_unread_messages(store):
    # Arrange
    response = _threads_with_a_silent_registry_agent(store)
    # Act
    agents = _agents_of(response)
    # Assert
    assert agents[0]["unread"] == 0


def test_silent_registry_agent_has_no_last_timestamp(store):
    # Arrange
    response = _threads_with_a_silent_registry_agent(store)
    # Act
    agents = _agents_of(response)
    # Assert
    assert agents[0]["last_ts"] is None


def test_threads_view_rejects_post(store):
    # Arrange
    request = RequestFactory().post(f"/dm/threads?store={store}")
    # Act
    response = dm_threads_view(request)
    # Assert
    assert response.status_code == 405


# === GET /dm/thread/<peer> =================================================


def test_thread_view_returns_ok_for_a_known_peer(store):
    # Arrange
    append_message("agent-x", "operator", "first", store=store)
    append_message("operator", "agent-x", "second", store=store)
    # Act
    response = dm_thread_view(_get(f"/dm/thread/agent-x?store={store}"), "agent-x")
    # Assert
    assert response.status_code == 200


def test_thread_view_names_the_canonical_thread_id(store):
    # Arrange
    append_message("agent-x", "operator", "first", store=store)
    append_message("operator", "agent-x", "second", store=store)
    response = dm_thread_view(_get(f"/dm/thread/agent-x?store={store}"), "agent-x")
    # Act
    data = json.loads(response.content)
    # Assert
    assert data["thread"] == "dm:agent-x::operator"


def test_thread_view_returns_messages_chronologically(store):
    # Arrange
    append_message("agent-x", "operator", "first", store=store)
    append_message("operator", "agent-x", "second", store=store)
    response = dm_thread_view(_get(f"/dm/thread/agent-x?store={store}"), "agent-x")
    # Act
    data = json.loads(response.content)
    # Assert
    assert [m["body"] for m in data["messages"]] == ["first", "second"]


def test_thread_view_mark_read_acks_operator_messages(store):
    # Arrange
    append_message("agent-x", "operator", "unread ping", store=store)
    dm_thread_view(_get(f"/dm/thread/agent-x?store={store}&mark_read=1"), "agent-x")
    # Act
    # read back from the sidecar, not just the response.
    msgs = get_thread("operator", "agent-x", store=store)
    # Assert
    assert msgs[0]["read"] is True


# === POST /dm/thread/<peer> ================================================


def test_post_operator_message_returns_ok(store):
    # Arrange
    # Act
    response = _post_operator_message(store, "check the deploy")
    # Assert
    assert response.status_code == 200


def test_post_operator_message_is_stored_from_the_operator(store):
    # Arrange
    response = _post_operator_message(store, "check the deploy")
    # Act
    message = json.loads(response.content)["message"]
    # Assert
    assert message["from"] == "operator"


def test_post_operator_message_is_addressed_to_the_agent(store):
    # Arrange
    response = _post_operator_message(store, "check the deploy")
    # Act
    message = json.loads(response.content)["message"]
    # Assert
    assert message["to"] == "agent-x"


def test_post_operator_message_is_appended_to_the_thread(store):
    # Arrange
    response = _post_operator_message(store, "check the deploy")
    message = json.loads(response.content)["message"]
    # Act
    stored = get_thread("operator", "agent-x", store=store)
    # Assert
    assert stored == [message]


def test_post_operator_message_dispatches_one_inbox_item(store):
    # Arrange
    _post_operator_message(store, "check the deploy")
    # Act
    inbox = poll_inbox("agent-x", store=store)
    # Assert
    assert len(inbox) == 1


def test_dispatched_inbox_item_is_a_dm_event(store):
    # Arrange
    _post_operator_message(store, "check the deploy")
    # Act
    inbox = poll_inbox("agent-x", store=store)
    # Assert
    assert inbox[0]["event_type"] == "dm"


def test_dispatched_inbox_item_carries_the_message_body(store):
    # Arrange
    _post_operator_message(store, "check the deploy")
    # Act
    inbox = poll_inbox("agent-x", store=store)
    # Assert
    assert inbox[0]["body"] == "check the deploy"


def test_post_rejects_empty_body(store):
    # Arrange
    # Act
    response = _post_operator_message(store, "   ")
    # Assert
    assert response.status_code == 400


def test_thread_view_rejects_delete(store):
    # Arrange
    request = RequestFactory().delete(f"/dm/thread/agent-x?store={store}")
    # Act
    response = dm_thread_view(request, "agent-x")
    # Assert
    assert response.status_code == 405


# EOF
