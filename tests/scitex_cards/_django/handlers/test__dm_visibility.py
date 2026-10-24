#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hide/unhide for DM conversations (card cards-dm-spaces-crud-hide-20261022).

``POST /dm/thread/<peer>/hidden`` flips per-reader view state; ``GET
/dm/threads`` excludes hidden rows by default and answers them flagged under
``?show=all`` (or alone under ``?show=hidden``).

Hiding is VIEW STATE, not deletion: the thread's messages are untouched, so
these tests also pin that a hide/unhide round-trip changes no message row.
Unknown threads are a 400 (hiding names an existing conversation), and the
write is scoped the request-ATTRIBUTE way — see ``test__dm_views.py``.

Django RequestFactory against a REAL throwaway PostgreSQL schema. FAILS
rather than skips when the harness did not pin one (same fail-loud rule as
every other DM DB test): a skipped storage test and a passing one look
identical in a summary line.
"""

from __future__ import annotations

import json
import os

import pytest
from django.test import RequestFactory

from scitex_cards._django.handlers.dm import (
    STORE_REQUEST_ATTR,
    dm_threads_view,
    dm_visibility_view,
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


def _hide(store, peer, hidden):
    request = RequestFactory().post(
        f"/dm/thread/{peer}/hidden",
        data=json.dumps({"hidden": hidden}),
        content_type="application/json",
    )
    setattr(request, STORE_REQUEST_ATTR, str(store))
    return dm_visibility_view(request, peer)


def _agents_of(response) -> list:
    return json.loads(response.content)["agents"]


def _names(response) -> list:
    return [a["name"] for a in _agents_of(response)]


# === POST /dm/thread/<peer>/hidden =========================================


def test_hide_answers_ok_status(store):
    # Arrange
    _seed_thread(store)
    # Act
    response = _hide(store, "agent-x", True)
    # Assert
    assert response.status_code == 200


def test_hide_answers_the_resulting_state(store):
    # Arrange
    key = _seed_thread(store)
    # Act
    response = _hide(store, "agent-x", True)
    # Assert
    assert json.loads(response.content) == {
        "thread": key,
        "peer": "agent-x",
        "hidden": True,
        "changed": True,
    }


def test_repeated_hide_reports_no_change(store):
    # Arrange
    _seed_thread(store)
    _hide(store, "agent-x", True)
    # Act
    response = _hide(store, "agent-x", True)
    # Assert
    assert json.loads(response.content)["changed"] is False


def test_unhide_restores_the_row(store):
    # Arrange
    _seed_thread(store)
    _hide(store, "agent-x", True)
    # Act
    response = _hide(store, "agent-x", False)
    # Assert
    assert json.loads(response.content)["hidden"] is False


def test_hide_rejects_an_unknown_thread_with_400(store):
    # Arrange — no thread with agent-ghost exists.
    # Act
    response = _hide(store, "agent-ghost", True)
    # Assert
    assert response.status_code == 400


def test_hide_rejects_an_unknown_thread_by_name(store):
    # Arrange — no thread with agent-ghost exists.
    # Act
    response = _hide(store, "agent-ghost", True)
    # Assert
    assert json.loads(response.content)["error"] == "unknown-thread"


def test_hide_rejects_a_non_boolean_body(store):
    # Arrange
    _seed_thread(store)
    request = RequestFactory().post(
        "/dm/thread/agent-x/hidden",
        data=json.dumps({"hidden": "yes"}),
        content_type="application/json",
    )
    setattr(request, STORE_REQUEST_ATTR, str(store))
    # Act
    response = dm_visibility_view(request, "agent-x")
    # Assert
    assert response.status_code == 400


def test_hide_rejects_get(store):
    # Arrange
    _seed_thread(store)
    # Act
    response = dm_visibility_view(RequestFactory().get("/x"), "agent-x")
    # Assert
    assert response.status_code == 405


def test_hide_changes_no_message(store):
    # Arrange
    key = _seed_thread(store)
    before = dm_read.messages_in(key, store=store)
    # Act
    _hide(store, "agent-x", True)
    _hide(store, "agent-x", False)
    # Assert
    assert dm_read.messages_in(key, store=store) == before


# === GET /dm/threads visibility ============================================


def _get_threads(store, **extra):
    return dm_threads_view(RequestFactory().get(f"/dm/threads?{_q(store, **extra)}"))


def test_hidden_thread_leaves_the_default_list(store):
    # Arrange
    _seed_thread(store)
    _hide(store, "agent-x", True)
    # Act
    response = _get_threads(store)
    # Assert
    assert "agent-x" not in _names(response)


def test_show_all_flags_the_hidden_row(store):
    # Arrange
    _seed_thread(store)
    _hide(store, "agent-x", True)
    # Act
    response = _get_threads(store, show="all")
    # Assert
    rows = {a["name"]: a for a in _agents_of(response)}
    assert rows["agent-x"]["hidden"] is True


def test_show_hidden_answers_only_hidden_rows(store):
    # Arrange
    _seed_thread(store)
    _hide(store, "agent-x", True)
    # Act
    response = _get_threads(store, show="hidden")
    # Assert
    assert _names(response) == ["agent-x"]


def test_visible_rows_carry_hidden_false(store):
    # Arrange
    _seed_thread(store)
    # Act
    response = _get_threads(store)
    # Assert
    rows = {a["name"]: a for a in _agents_of(response)}
    assert rows["agent-x"]["hidden"] is False


def test_unknown_show_mode_is_a_400(store):
    # Arrange
    _seed_thread(store)
    # Act
    response = _get_threads(store, show="everything")
    # Assert
    assert response.status_code == 400


# === store fold ============================================================


def test_latest_event_wins_the_fold(store):
    # Arrange
    key = _seed_thread(store)
    # Act
    dm_write.set_thread_visibility(key, "operator", True, store=store)
    dm_write.set_thread_visibility(key, "operator", False, store=store)
    dm_write.set_thread_visibility(key, "operator", True, store=store)
    # Assert
    assert dm_read.hidden_for("operator", store=store) == {key}


def test_visibility_is_per_reader(store):
    # Arrange
    key = _seed_thread(store)
    # Act
    dm_write.set_thread_visibility(key, "operator", True, store=store)
    # Assert
    assert dm_read.hidden_for("agent-x", store=store) == set()


def test_unknown_thread_raises_key_error(store):
    # Arrange — no thread row exists for this id.
    # Act
    # Assert
    with pytest.raises(KeyError):
        dm_write.set_thread_visibility(
            "dm:operator::agent-ghost", "operator", True, store=store
        )


# EOF
