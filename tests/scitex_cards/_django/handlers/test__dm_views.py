#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``/dm/*`` Django endpoints (operator↔agent direct messages).

Minimal-slice contract (card fleet-agent-direct-message-board-pane-20260707):

  - GET  /dm/threads      → registry agents ∪ thread peers, with unread + last.
  - GET  /dm/thread/<p>   → chronological messages; mark_read=1 acks.
  - POST /dm/thread/<p>   → appends from=operator, dm-dispatches to the
                            agent's pull-inbox; 400 on empty body.
  - 405 on other verbs.

Django RequestFactory against a REAL store via ``?store=``; no mocks
(STX-NM / PA-306). AAA pattern, one assertion per test (STX-TQ007).

THE STORE IS THIS TEST'S OWN THROWAWAY POSTGRESQL SCHEMA, not a scratch
``tasks.yaml``. There is one storage engine now and a filename names no
store, so the old fixture handed every call a target the doors refuse.
The DM layer already understood a DSN here -- ``resolve_dm_db`` returns a
PostgreSQL ``store`` verbatim rather than deriving a file beside it -- so
what changed is the fixture, not the code under test.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlencode

import pytest
from django.test import RequestFactory

from scitex_cards._django.handlers.dm import (
    STORE_REQUEST_ATTR,
    dm_thread_view,
    dm_threads_view,
)
from scitex_cards._inbox import poll_inbox
from scitex_cards._threads import append_message, get_thread


@pytest.fixture()
def store(env) -> str:
    """This test's own throwaway PostgreSQL schema.

    FAILS rather than skips if the harness did not pin one -- see the root
    ``conftest.py``. A skipped storage test and a passing one look identical in
    a summary line, which is how four modules in this suite went unexercised
    for months.
    """
    env.set("SCITEX_CARDS_STORE_GIT_AUTOCOMMIT", "0")
    dsn = os.environ.get("SCITEX_CARDS_DB", "")
    if "search_path" not in dsn:
        pytest.fail(
            "the root conftest did not pin $SCITEX_CARDS_DB to a throwaway "
            f"PostgreSQL schema; it holds {dsn!r}.",
            pytrace=False,
        )
    return dsn


def _q(store, **extra) -> str:
    """The query string for ``store``, ENCODED.

    A DSN is not URL-safe: it carries ``?options=-csearch_path%3D<schema>``.
    Interpolated raw, its ``?`` starts a second query and the view receives a
    store truncated at the schema -- a wrong store that parses, which is the
    failure mode this package keeps meeting. Encoding keeps the value the view
    reads identical to the one the fixture handed out.
    """
    return urlencode({"store": str(store), **extra})


def _get(url):
    return RequestFactory().get(url)


def _agents_of(response) -> list:
    return json.loads(response.content)["agents"]


def _threads_with_one_inbound(store):
    """One inbound (agent→operator) message, no registry entries."""
    append_message("agent-x", "operator", "ping", store=store)
    return dm_threads_view(_get(f"/dm/threads?{_q(store)}"))


def _threads_with_a_silent_registry_agent(store):
    """A registered agent that has never sent or received a message."""
    from scitex_cards._users import register_user

    register_user(kind="agent", names=["agent-quiet"], store=store)
    return dm_threads_view(_get(f"/dm/threads?{_q(store)}"))


def _post_operator_message(store, body: str):
    """A write, scoped the way a WRITE is now allowed to be scoped.

    The store arrives on the request ATTRIBUTE, exactly as scitex-hub's
    tenancy middleware sets it. It is deliberately NOT passed as ``?store=``
    any more: a write no longer honours the query, so scoping these tests
    through it would have them exercise a path that production refuses.
    """
    request = RequestFactory().post(
        "/dm/thread/agent-x",
        data=json.dumps({"body": body}),
        content_type="application/json",
    )
    setattr(request, STORE_REQUEST_ATTR, str(store))
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
    request = RequestFactory().post(f"/dm/threads?{_q(store)}")
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
    response = dm_thread_view(_get(f"/dm/thread/agent-x?{_q(store)}"), "agent-x")
    # Assert
    assert response.status_code == 200


def test_thread_view_names_the_canonical_thread_id(store):
    # Arrange
    append_message("agent-x", "operator", "first", store=store)
    append_message("operator", "agent-x", "second", store=store)
    response = dm_thread_view(_get(f"/dm/thread/agent-x?{_q(store)}"), "agent-x")
    # Act
    data = json.loads(response.content)
    # Assert
    assert data["thread"] == "dm:agent-x::operator"


def test_thread_view_returns_messages_chronologically(store):
    # Arrange
    append_message("agent-x", "operator", "first", store=store)
    append_message("operator", "agent-x", "second", store=store)
    response = dm_thread_view(_get(f"/dm/thread/agent-x?{_q(store)}"), "agent-x")
    # Act
    data = json.loads(response.content)
    # Assert
    assert [m["body"] for m in data["messages"]] == ["first", "second"]


def test_thread_view_mark_read_acks_operator_messages(store):
    # Arrange
    append_message("agent-x", "operator", "unread ping", store=store)
    dm_thread_view(_get(f"/dm/thread/agent-x?{_q(store, mark_read='1')}"), "agent-x")
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
    request = RequestFactory().delete(f"/dm/thread/agent-x?{_q(store)}")
    # Act
    response = dm_thread_view(request, "agent-x")
    # Assert
    assert response.status_code == 405


# === the write target is not the caller's to choose ========================


def test_a_query_store_does_not_become_the_write_target(store, tmp_path, env):
    """The P0 itself: ``?store=`` must not steer where a write lands.

    A caller admitted by any gate used to pick the written file through the
    query string, and a URL-PATH allowlist never sees a query string — so
    every gate reasoning about paths was reasoning about the wrong thing.
    Here the request names an ATTACKER store in the query and supplies no
    trusted attribute; the attacker store must be left untouched.

    THE AMBIENT STORE IS PINNED TO tmp FIRST, and that is not incidental. With
    no attribute and no query fallback the handler falls back to its OWN
    resolution — which, unpinned, is the LIVE FLEET BOARD. The first draft of
    this test omitted the pin, and the run hung on the live store's lock
    instead of failing: a test for a write-safety property must not itself be
    able to write somewhere real.
    """
    # Arrange — a store the request asks for and must not get
    attacker = tmp_path / "attacker" / "tasks.yaml"
    attacker.parent.mkdir(parents=True, exist_ok=True)
    attacker.write_text("tasks: []\n", encoding="utf-8")
    # THE AMBIENT PIN IS STILL THE POINT, and it is now the harness's. This
    # read `env.set("SCITEX_CARDS_DB", str(tmp_path / "ambient.db"))` -- a
    # FILENAME, which the doors refuse, so the handler's fallback would raise
    # instead of writing anywhere and the test would pass without ever
    # exercising the property. The autouse fixture already pins the ambient
    # store to this test's throwaway schema, which is what the pin was for:
    # somewhere real that is provably not the live board.
    env.set("SCITEX_CARDS_STORE", str(store))
    request = RequestFactory().post(
        f"/dm/thread/agent-x?{_q(attacker)}",
        data=json.dumps({"body": "written wherever I say"}),
        content_type="application/json",
    )
    # Act
    dm_thread_view(request, "agent-x")
    # Assert — the MESSAGE did not land in the store the request named.
    #
    # Asserts on CONTENT, not on the sidecar's existence. An earlier draft
    # asserted `not threads.json.exists()` and failed — but the file is
    # created by the READ path (`_store_of`, still query-scoped by design for
    # reads), holding no message. Those are two different defects, and an
    # existence check cannot tell them apart: it would have reported the
    # arbitrary-WRITE hole as still open when what it found was a read that
    # manufactures an empty sidecar. Tracked separately.
    sidecar = attacker.parent / "threads.json"
    leaked = sidecar.read_text(encoding="utf-8") if sidecar.exists() else ""
    assert "written wherever I say" not in leaked, (
        "a ?store= query steered the write: the message body landed in the "
        "file the CALLER named, which is the arbitrary-write surface this "
        "endpoint had"
    )


def test_a_trusted_attribute_still_scopes_the_write(store):
    """The legitimate path must keep working, or this is an outage not a fix.

    scitex-hub's tenancy middleware sets this attribute per request; if it
    stopped being honoured, every hub tenant would silently share one store.
    """
    # Arrange
    _post_operator_message(store, "scoped by the trusted attribute")
    # Act
    stored = get_thread("operator", "agent-x", store=store)
    # Assert
    assert stored[-1]["body"] == "scoped by the trusted attribute"


# EOF
