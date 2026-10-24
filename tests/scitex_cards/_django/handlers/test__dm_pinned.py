#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pin/unpin for DM conversations (operator 2026-10-24: CRUD ops as
right-click on the agent list).

``POST /dm/thread/<peer>/pinned`` flips per-reader view state in the SAME
append-only ``dm_visibility_events`` table hiding uses — ``pin``/``unpin``
rows, never a deletion, never content. ``GET /dm/threads`` answers every
row flagged and sorts pinned rows above the recency order.

Pinning is VIEW STATE, not deletion: the thread's messages are untouched,
so these tests also pin that a pin/unpin round-trip changes no message
row. Unknown threads are a 400 (pinning names an existing conversation).

Django RequestFactory against a REAL throwaway PostgreSQL schema. FAILS
rather than skips when the harness did not pin one (same fail-loud rule
as every other DM DB test).
"""

from __future__ import annotations

import json
import os

import pytest
from django.test import RequestFactory

from scitex_cards._django.handlers.dm import (
    STORE_REQUEST_ATTR,
    dm_pinned_view,
    dm_threads_view,
)
from scitex_cards._dm import read as dm_read
from scitex_cards._dm import write as dm_write
from scitex_cards._dm.threads import append_message, thread_key


@pytest.fixture()
def store(env) -> str:
    """This test's own throwaway PostgreSQL schema (fail-loud, never skip)."""
    env.set("SCITEX_CARDS_STORE_GIT_AUTOCOMMIT", "0")
    dsn = os.environ.get("SCITEX_STORE_DSN", "")
    if "search_path" not in dsn:
        pytest.fail(
            "the root conftest did not pin $SCITEX_STORE_DSN to a throwaway "
            f"PostgreSQL schema; it holds {dsn!r}.",
            pytrace=False,
        )
    return dsn


def _q(store, **extra) -> str:
    from urllib.parse import urlencode

    return urlencode({"store": str(store), **extra})


def _seed_thread(store) -> str:
    """One operator↔agent-x thread; returns its thread id."""
    append_message("agent-x", "operator", "ping", store=store)
    return thread_key("operator", "agent-x")


def _pin(store, peer, pinned):
    request = RequestFactory().post(
        f"/dm/thread/{peer}/pinned",
        data=json.dumps({"pinned": pinned}),
        content_type="application/json",
    )
    setattr(request, STORE_REQUEST_ATTR, str(store))
    return dm_pinned_view(request, peer)


def _agents_of(response) -> list:
    return json.loads(response.content)["agents"]


# === POST /dm/thread/<peer>/pinned =========================================


def test_pin_answers_the_resulting_state(store):
    # Arrange
    key = _seed_thread(store)
    # Act
    response = _pin(store, "agent-x", True)
    # Assert
    assert response.status_code == 200
    assert json.loads(response.content) == {
        "thread": key,
        "peer": "agent-x",
        "pinned": True,
        "changed": True,
    }


def test_repeated_pin_reports_no_change(store):
    # Arrange
    _seed_thread(store)
    _pin(store, "agent-x", True)
    # Act
    response = _pin(store, "agent-x", True)
    # Assert
    assert json.loads(response.content)["changed"] is False


def test_unpin_restores_the_row(store):
    # Arrange
    _seed_thread(store)
    _pin(store, "agent-x", True)
    # Act
    response = _pin(store, "agent-x", False)
    # Assert
    assert json.loads(response.content)["pinned"] is False


def test_pin_rejects_an_unknown_thread(store):
    # Arrange — no thread with agent-ghost exists.
    # Act
    response = _pin(store, "agent-ghost", True)
    # Assert
    assert response.status_code == 400
    assert json.loads(response.content)["error"] == "unknown-thread"


def test_pin_rejects_a_non_boolean_body(store):
    # Arrange
    _seed_thread(store)
    request = RequestFactory().post(
        "/dm/thread/agent-x/pinned",
        data=json.dumps({"pinned": "yes"}),
        content_type="application/json",
    )
    setattr(request, STORE_REQUEST_ATTR, str(store))
    # Act
    response = dm_pinned_view(request, "agent-x")
    # Assert
    assert response.status_code == 400


def test_pin_rejects_get(store):
    # Arrange
    _seed_thread(store)
    request = RequestFactory().get(f"/dm/thread/agent-x/pinned?{_q(store)}")
    # Act
    response = dm_pinned_view(request, "agent-x")
    # Assert
    assert response.status_code == 405


def test_pin_changes_no_message(store):
    # Arrange
    _seed_thread(store)
    before = dm_read.messages_in(
        thread_key("operator", "agent-x"), store=store
    )
    # Act
    _pin(store, "agent-x", True)
    _pin(store, "agent-x", False)
    # Assert
    assert dm_read.messages_in(
        thread_key("operator", "agent-x"), store=store
    ) == before


# === GET /dm/threads carries pinned ========================================


def test_pinned_thread_is_flagged_in_the_list(store):
    # Arrange
    _seed_thread(store)
    _pin(store, "agent-x", True)
    request = RequestFactory().get(f"/dm/threads?{_q(store, show='all')}")
    # Act
    rows = {a["name"]: a for a in _agents_of(dm_threads_view(request))}
    # Assert
    assert rows["agent-x"]["pinned"] is True


def test_pinned_thread_floats_above_recency(store):
    # Arrange — agent-y writes later, so recency alone would put it first.
    _seed_thread(store)
    append_message("agent-y", "operator", "later ping", store=store)
    _pin(store, "agent-x", True)
    request = RequestFactory().get(f"/dm/threads?{_q(store)}")
    # Act
    names = [a["name"] for a in _agents_of(dm_threads_view(request))]
    # Assert
    assert names.index("agent-x") < names.index("agent-y")


def test_unpinned_thread_falls_back_to_recency(store):
    # Arrange
    _seed_thread(store)
    append_message("agent-y", "operator", "later ping", store=store)
    _pin(store, "agent-x", True)
    _pin(store, "agent-x", False)
    request = RequestFactory().get(f"/dm/threads?{_q(store)}")
    # Act
    names = [a["name"] for a in _agents_of(dm_threads_view(request))]
    # Assert
    assert names.index("agent-y") < names.index("agent-x")


def test_pin_does_not_hide_the_row(store):
    # Arrange
    _seed_thread(store)
    # Act
    _pin(store, "agent-x", True)
    request = RequestFactory().get(f"/dm/threads?{_q(store)}")
    names = [a["name"] for a in _agents_of(dm_threads_view(request))]
    # Assert
    assert "agent-x" in names


# === the fold ==============================================================


def test_latest_event_wins_the_pin_fold(store):
    # Arrange
    key = _seed_thread(store)
    # Act
    dm_write.set_thread_pinned(key, "operator", True, store=store)
    dm_write.set_thread_pinned(key, "operator", False, store=store)
    dm_write.set_thread_pinned(key, "operator", True, store=store)
    # Assert
    assert dm_read.pinned_for("operator", store=store) == {key}


def test_pin_is_per_reader(store):
    # Arrange
    key = _seed_thread(store)
    # Act
    dm_write.set_thread_pinned(key, "operator", True, store=store)
    # Assert
    assert dm_read.pinned_for("agent-x", store=store) == set()


def test_hide_events_are_not_pin_state(store):
    # Arrange
    key = _seed_thread(store)
    # Act
    dm_write.set_thread_visibility(key, "operator", True, store=store)
    # Assert
    assert dm_read.pinned_for("operator", store=store) == set()


def test_pin_events_are_not_hide_state(store):
    # Arrange
    key = _seed_thread(store)
    # Act
    dm_write.set_thread_pinned(key, "operator", True, store=store)
    # Assert
    assert dm_read.hidden_for("operator", store=store) == set()


def test_unknown_pinned_thread_raises_key_error(store):
    # Arrange — no thread row exists for this id.
    # Act
    with pytest.raises(KeyError):
        dm_write.set_thread_pinned(
            "dm:operator::agent-ghost", "operator", True, store=store
        )


# EOF
