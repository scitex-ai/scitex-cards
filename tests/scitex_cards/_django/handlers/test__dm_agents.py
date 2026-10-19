#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for ``GET /dm/agents`` — the DM New-button buzz-search (CCT 3853).

The agent list is CONSUMED from the loose agent-directory port, never kept:
rows come from ``AgentDirectoryPort.list_agents()`` (the Agents app is SSOT
for agent runtime) with ``has_thread`` marking agents the current reader
already has a room with. ``directory`` is the injectable seam for provider
resolution, so these tests pass fakes — no monkeypatch (PA-306).

Django RequestFactory against a REAL throwaway PostgreSQL schema via
``?store=`` where thread state matters; no mocks (STX-NM / PA-306). AAA
pattern, one assertion per test (STX-TQ007).
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlencode

import pytest
from django.test import RequestFactory
from django.urls import resolve

from scitex_cards._django.handlers.dm import dm_agents_view
from scitex_cards._django import views as _chat_views
from scitex_cards._dm.threads import append_message
from scitex_cards._ports import AgentInfo

_URLCONF = "scitex_cards._django.urls"


@pytest.fixture()
def store(env) -> str:
    """This test's own throwaway PostgreSQL schema (fail, never skip)."""
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
    """The query string for ``store``, ENCODED (a DSN is not URL-safe)."""
    return urlencode({"store": str(store), **extra})


def _get(url):
    return RequestFactory().get(url)


class _FakeDirectory:
    """An ``AgentDirectoryPort`` with canned rows (no entry points)."""

    def __init__(self, rows):
        self._rows = list(rows)

    def list_agents(self):
        return list(self._rows)

    def get_agent(self, host_at_name):
        for row in self._rows:
            if row.host_at_name == host_at_name:
                return row
        return None


def _row(host_at_name, name, host="h", status="running"):
    # Arrange helper, not a test — builds one directory row.
    return AgentInfo(
        host_at_name=host_at_name, name=name, host=host, status=status
    )


class TestShape:
    """The view answers 200 with the shaped rows."""

    def test_empty_directory_serves_empty_200(self):
        # Arrange
        request = _get("/dm/agents")
        # Act
        resp = dm_agents_view(request, directory=_FakeDirectory([]))
        # Assert
        assert resp.status_code == 200

    def test_empty_directory_body_has_agents_key(self):
        # Arrange
        request = _get("/dm/agents")
        # Act
        body = json.loads(
            dm_agents_view(request, directory=_FakeDirectory([])).content
        )
        # Assert
        assert body == {"agents": []}

    def test_row_shape_is_id_name_host_status_has_thread(self):
        # Arrange
        directory = _FakeDirectory([_row("h@agent-x", "agent-x")])
        request = _get("/dm/agents")
        # Act
        (row,) = json.loads(
            dm_agents_view(request, directory=directory).content
        )["agents"]
        # Assert
        assert set(row) == {"id", "name", "host", "status", "has_thread"}


class TestBuzzSearch:
    """``?q=`` substring-matches ids and short names, exact first, cap 20."""

    def test_q_filters_out_non_matches(self):
        # Arrange
        directory = _FakeDirectory(
            [_row("h@agent-x", "agent-x"), _row("h@agent-y", "agent-y")]
        )
        request = _get("/dm/agents?" + urlencode({"q": "agent-x"}))
        # Act
        body = json.loads(
            dm_agents_view(request, directory=directory).content
        )
        # Assert
        assert [r["id"] for r in body["agents"]] == ["h@agent-x"]

    def test_exact_match_ranks_before_prefix(self):
        # Arrange
        directory = _FakeDirectory(
            [_row("h@agent-x-long", "agent-x-long"), _row("h@x", "x")]
        )
        request = _get("/dm/agents?" + urlencode({"q": "x"}))
        # Act
        body = json.loads(
            dm_agents_view(request, directory=directory).content
        )
        # Assert
        assert [r["id"] for r in body["agents"]][0] == "h@x"

    def test_rows_cap_at_twenty(self):
        # Arrange
        directory = _FakeDirectory(
            [_row(f"h@agent-{i:02d}", f"agent-{i:02d}") for i in range(25)]
        )
        request = _get("/dm/agents")
        # Act
        body = json.loads(
            dm_agents_view(request, directory=directory).content
        )
        # Assert
        assert len(body["agents"]) == 20


class TestHasThread:
    """``has_thread`` reflects the reader's existing rooms; no store → False."""

    def test_thread_peer_has_thread_true(self, store):
        # Arrange — one agent→operator message seeds the room.
        append_message("agent-x", "operator", "ping", store=store)
        directory = _FakeDirectory([_row("h@agent-x", "agent-x")])
        request = _get(f"/dm/agents?{_q(store)}")
        # Act
        (row,) = json.loads(
            dm_agents_view(request, directory=directory).content
        )["agents"]
        # Assert
        assert row["has_thread"] is True

    def test_stranger_has_thread_false(self, store):
        # Arrange
        append_message("agent-x", "operator", "ping", store=store)
        directory = _FakeDirectory([_row("h@stranger", "stranger")])
        request = _get(f"/dm/agents?{_q(store)}")
        # Act
        (row,) = json.loads(
            dm_agents_view(request, directory=directory).content
        )["agents"]
        # Assert
        assert row["has_thread"] is False

    def test_no_store_degrades_has_thread_to_false(self):
        # Arrange — no ?store=: the directory needs none.
        directory = _FakeDirectory([_row("h@agent-x", "agent-x")])
        request = _get("/dm/agents")
        # Act
        (row,) = json.loads(
            dm_agents_view(request, directory=directory).content
        )["agents"]
        # Assert
        assert row["has_thread"] is False


class TestContract:
    """Method gate + route registration."""

    def test_post_is_405(self):
        # Arrange
        request = RequestFactory().post("/dm/agents")
        # Act
        resp = dm_agents_view(request, directory=_FakeDirectory([]))
        # Assert
        assert resp.status_code == 405

    def test_dm_agents_route_hits_the_view(self):
        # Arrange
        # Act
        match = resolve("/dm/agents", urlconf=_URLCONF)
        # Assert
        assert match.func is dm_agents_view


def _page() -> str:
    """The rendered /chat page — what the browser is actually served."""
    return _chat_views.chat_page(RequestFactory().get("/chat")).content.decode(
        "utf-8"
    )


class TestChatPage:
    """The New button and its module are served (CCT 3853)."""

    def test_new_button_is_served(self):
        # Arrange
        # Act
        present = 'id="dm-new"' in _page()
        # Assert
        assert present

    def test_chat_new_is_linked(self):
        # Arrange
        # Act
        present = "chat/chat_new.js" in _page()
        # Assert
        assert present

    def test_chat_new_loads_before_the_controller(self):
        # Arrange
        page = _page()
        # Act
        ordered = page.index("chat/chat_new.js") < page.index("chat/chat.js")
        # Assert
        assert ordered
