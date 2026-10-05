#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The shared header's project slot never takes the page down with it.

WHAT THIS PINS. ``views._current_project_id`` feeds ``cards_current_project_id``
into ``_cards_shell_context``, which BOTH top-level pages render (the board and
DM share ``_page_header.html``). Its docstring promises EVERY failure answers
``""`` — "a header that 500s takes both pages down with it" — but until this
file no test exercised a single failure branch: the suites around it pin the
library-absent half (``test__project_picker_degrades``) and the scope half
(``test__project_picker_scope``), while the provider half ran only on the
healthy path this host happens to have (no provider registered, so
``host_project_provider()`` returns ``None``).

A branch nobody has ever run is a promise nobody has ever kept — the same
shape the ``__init__`` version resolver documents for its own injectable
branches. So: a raising host provider, a raising ``resolve_project``, a
missing provider API, and a ``None`` answer all land on ``""``; the explicit
``?project=`` plumbing that shares the same function is pinned alongside,
because a test that only asserts ``""`` cannot tell a guard from a function
that returns ``""`` unconditionally.

THE SDK IS REAL, THE HOST HALF IS STUBBED. ``scitex_ui.project_scope`` is the
shipped module — the import seam ``_current_project_id`` actually crosses —
and only the HOST's answers (provider object, resolve outcome) are supplied
per-test via ``monkeypatch``, which reverts them. Nothing of ours is patched.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

import scitex_ui.project_scope as project_scope  # noqa: E402
from scitex_cards._django import views  # noqa: E402


class _User:
    is_staff = False

    def __init__(self, authenticated=True):
        self.is_authenticated = authenticated

    def get_username(self):
        return "alice"


def _request(path="/dm", project=None):
    query = f"?project={project}" if project is not None else ""
    request = RequestFactory().get(path + query, HTTP_HOST="127.0.0.1")
    request.user = _User()
    return request


def _stub_resolve(calls, answer):
    """A host resolve that records the explicit value OUR code passed it."""

    def resolve(request, provider, explicit=None):
        calls.append(explicit)
        return answer(explicit) if callable(answer) else answer

    return resolve


# --- the healthy path: our explicit-project plumbing -------------------------


def test_explicit_project_wins_and_is_stripped(monkeypatch):
    """``?project=`` reaches the SDK stripped, not raw and not dropped."""
    # Arrange
    calls: list = []
    monkeypatch.setattr(
        project_scope, "host_project_provider", lambda: object()
    )
    monkeypatch.setattr(
        project_scope, "resolve_project", _stub_resolve(calls, lambda e: e)
    )
    # Act
    current = views._current_project_id(_request(project="  proj-x  "))
    # Assert
    assert (current, calls) == ("proj-x", ["proj-x"])


def test_blank_project_falls_back_to_no_explicit(monkeypatch):
    """A blank ``?project=`` is absence, not a project named ``""``."""
    # Arrange
    calls: list = []
    monkeypatch.setattr(
        project_scope, "host_project_provider", lambda: object()
    )
    monkeypatch.setattr(
        project_scope, "resolve_project", _stub_resolve(calls, "stored-proj")
    )
    # Act
    current = views._current_project_id(_request(project="   "))
    # Assert
    assert (current, calls) == ("stored-proj", [None])


def test_none_answer_coalesces_to_empty(monkeypatch):
    """The SDK's "no project" (None) is the header's "hide it" (""), not a crash."""
    # Arrange
    monkeypatch.setattr(
        project_scope, "host_project_provider", lambda: object()
    )
    monkeypatch.setattr(
        project_scope, "resolve_project", _stub_resolve([], None)
    )
    # Act
    current = views._current_project_id(_request(project="proj-x"))
    # Assert
    assert current == ""


# --- every failure answers "", never raises ----------------------------------


def test_raising_host_provider_answers_empty(monkeypatch):
    """A host provider that blows up leaves the band as it was, not a 500."""
    # Arrange
    def _raise():
        raise RuntimeError("host provider blew up")

    monkeypatch.setattr(project_scope, "host_project_provider", _raise)
    # Act
    current = views._current_project_id(_request())
    # Assert
    assert current == ""


def test_raising_resolve_answers_empty(monkeypatch):
    """A resolve that blows up is a missing project, not a missing page."""
    # Arrange
    monkeypatch.setattr(
        project_scope, "host_project_provider", lambda: object()
    )

    def _raise(request, provider, explicit=None):
        raise RuntimeError("resolve blew up")

    monkeypatch.setattr(project_scope, "resolve_project", _raise)
    # Act
    current = views._current_project_id(_request())
    # Assert
    assert current == ""


def test_missing_project_scope_api_answers_empty(monkeypatch):
    """An scitex-ui predating project scope is a deployment we still serve."""
    # Arrange
    monkeypatch.setitem(sys.modules, "scitex_ui.project_scope", None)
    # Act
    current = views._current_project_id(_request())
    # Assert
    assert current == ""


def test_no_provider_answers_empty(monkeypatch):
    """No host provider registered: the slot hides, the page renders."""
    # Arrange
    monkeypatch.setattr(
        project_scope, "host_project_provider", lambda: None
    )
    # Act
    current = views._current_project_id(_request())
    # Assert
    assert current == ""


# --- the user-visible promise: the page renders -------------------------------


def test_dm_page_renders_while_the_provider_raises(monkeypatch):
    """The OTHER top-level page, under a failing provider: 200, not 500.

    The header is shared, so this is not a DM defect being tested — it is the
    proof that the slot's failure cannot propagate to either page.
    """
    # Arrange
    def _raise():
        raise RuntimeError("host provider blew up on the DM page")

    monkeypatch.setattr(project_scope, "host_project_provider", _raise)
    # Act
    response = views.chat_page(RequestFactory().get("/dm", HTTP_HOST="127.0.0.1"))
    # Assert
    assert response.status_code == 200
