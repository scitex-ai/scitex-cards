#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_v3 API calls must be MOUNT-AWARE (P1, scitex-hub sub-path mount).

The hub mounts the board under a sub-path (e.g. ``/apps/cards/``). The
template used to hardcode root-absolute fetch paths (``fetch("/graph")``),
so every data call 404'd there — verified live: https://scitex.ai/graph →
404 while https://scitex.ai/apps/cards/graph → 200. The fix threads the
include root from ``board_v3_page`` (``api_base`` context) into a single
``API_BASE`` JS const that prefixes every board fetch.

Two layers of pinning, per the repo's _django view-test conventions
(RequestFactory against the real view — no mocks):

1. Render tests: the page rendered at a sub-path carries the mount path in
   its ``API_BASE`` wiring; the root mount stays "/" (→ "" after the
   trailing-slash strip, so calls remain "/graph"-shaped).
2. Template/static lint: no root-absolute ``fetch("/…")`` literal may creep
   back into board_v3.html or the board_v3 static JS it loads.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: north, title: North Star, status: goal, depends_on: [build]}\n"
    "  - {id: build, title: Build It, status: in_progress, priority: 1}\n"
)

_DJANGO_DIR = Path(views.__file__).resolve().parent
_TEMPLATE = _DJANGO_DIR / "templates" / "scitex_cards" / "board_v3.html"
_BOARD_V3_STATIC = _DJANGO_DIR / "static" / "scitex_cards" / "board_v3"
_CHAT_STATIC = _DJANGO_DIR / "static" / "scitex_cards" / "chat"


@pytest.fixture
def store():
    """Seed the canonical DB and reset the board cache around the test."""
    from conftest import seed_db_from_doc

    from scitex_cards._yaml import safe_load

    doc = safe_load(_STORE_TEXT) or {}
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    _reset_cache()
    yield os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _reset_cache()


# --- render: API_BASE wiring carries the mount path ------------------------


def test_board_v3_page_api_base_carries_subpath_mount(store):
    """At a sub-path mount the page wires API_BASE to the include root."""
    # Arrange — the hub mounts the board's "" (root) route at /apps/cards/.
    request = RequestFactory().get(f"/apps/cards/?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert — the rendered const carries the mount path (the JS strips the
    # trailing slash at runtime, so calls become "/apps/cards/graph").
    assert 'const API_BASE = "/apps/cards/"' in body


def test_board_v3_page_api_base_is_root_at_root_mount(store):
    """A root mount renders api_base "/" — "" after the strip — so every
    call keeps its original "/graph" shape (no regression at :8051)."""
    # Arrange
    request = RequestFactory().get(f"/?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    assert 'const API_BASE = "/"' in body


def test_board_v3_page_api_base_strips_board_v3_alias_segment(store):
    """The /board-v3 alias serves the same view one segment deeper; the view
    must strip that segment so fetches still target the include root."""
    # Arrange
    request = RequestFactory().get(f"/apps/cards/board-v3?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert — NOT "/apps/cards/board-v3" (which would 404 in api_dispatch).
    assert 'const API_BASE = "/apps/cards/"' in body


def test_board_v3_page_mirrors_api_base_onto_window(store):
    """External board scripts (timeline.js) read window.API_BASE."""
    # Arrange
    request = RequestFactory().get(f"/apps/cards/?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    assert "window.API_BASE = API_BASE;" in body


# --- lint: no root-absolute fetch may creep back ---------------------------


def test_board_v3_template_has_no_root_absolute_fetch():
    """Every fetch in board_v3.html must be API_BASE-prefixed."""
    # Arrange
    template_path = _TEMPLATE
    # Act
    source = template_path.read_text(encoding="utf-8")
    # Assert
    assert 'fetch("/' not in source


def test_board_v3_template_fetches_are_api_base_prefixed():
    """All template fetch sites go through the single API_BASE const."""
    # Arrange
    template_path = _TEMPLATE
    # Act
    source = template_path.read_text(encoding="utf-8")
    # Assert — every `fetch(` call is `fetch(API_BASE + "…` (the count match
    # keeps a new un-prefixed variant from slipping past the literal lint).
    assert source.count("fetch(") == source.count('fetch(API_BASE + "')


@pytest.mark.parametrize(
    "js_name", sorted(p.name for p in _BOARD_V3_STATIC.glob("*.js"))
)
def test_board_v3_static_js_has_no_root_absolute_fetch(js_name):
    """The board's external static JS (timeline.js, …) must not hardcode
    root-absolute fetch paths either — they read window.API_BASE."""
    # Arrange
    js_path = _BOARD_V3_STATIC / js_name
    # Act
    source = js_path.read_text(encoding="utf-8")
    # Assert
    assert 'fetch("/' not in source, js_name


@pytest.mark.parametrize("js_name", sorted(p.name for p in _CHAT_STATIC.glob("*.js")))
def test_chat_static_js_has_no_root_absolute_fetch(js_name):
    """The chat page's static JS must not hardcode root-absolute fetch paths
    either — chat.js reads the include root off <body data-api-base> (set by
    chat.html) and prefixes every /dm/* call with it."""
    # Arrange
    js_path = _CHAT_STATIC / js_name
    # Act
    source = js_path.read_text(encoding="utf-8")
    # Assert
    assert 'fetch("/' not in source, js_name


@pytest.mark.parametrize("js_name", sorted(p.name for p in _CHAT_STATIC.glob("*.js")))
def test_chat_static_js_has_no_root_absolute_getjson(js_name):
    """chat.js routes its GETs through the local getJSON helper — a
    root-absolute literal there escapes the mount exactly like a bare
    fetch, so it is linted the same way."""
    # Arrange
    js_path = _CHAT_STATIC / js_name
    # Act
    source = js_path.read_text(encoding="utf-8")
    # Assert
    assert 'getJSON("/' not in source, js_name


# --- render: the chat page carries the mount root on its DOM marker ---------


def test_chat_page_api_base_marker_carries_subpath_mount():
    """At a sub-path mount the chat page's <body data-api-base> carries the
    include root chat.js prefixes every /dm/* call with. The marker must
    ALWAYS be rendered — chat.js throws when it is absent (a missing marker
    is an integration bug, never a silently-guessed root mount)."""
    # Arrange — the hub serves the DM page at <include-root>chat.
    request = RequestFactory().get("/apps/cards/chat")
    # Act
    body = views.chat_page(request).content.decode("utf-8")
    # Assert
    assert 'data-api-base="/apps/cards/"' in body


def test_chat_page_api_base_marker_is_root_at_root_mount():
    """A root mount renders data-api-base "/" — "" after chat.js's
    trailing-slash strip — so calls keep their "/dm/…" shape standalone."""
    # Arrange
    request = RequestFactory().get("/chat")
    # Act
    body = views.chat_page(request).content.decode("utf-8")
    # Assert
    assert 'data-api-base="/"' in body


def test_chat_page_board_link_targets_include_root():
    """The board link must stay inside the mount, not escape to the site root
    (on the hub "/" is the hub's landing page, not the board).

    Asserts the ANCHOR, not the wrapper. The previous form pinned
    ``class="board-link" href=...`` on one element; when the one-way
    "&larr; board" link became the Board | Chat switcher, the class moved to a
    wrapping span and the href to an inner anchor. The mount-awareness — the
    only thing this test exists to protect — never changed, but the assertion
    broke, which is a test coupled to markup SHAPE rather than to BEHAVIOUR.
    Pinning the href keeps the #556/#557 regression caught while letting the
    chrome around it evolve.
    """
    # Arrange
    request = RequestFactory().get("/apps/cards/chat")
    # Act
    body = views.chat_page(request).content.decode("utf-8")
    # Assert
    assert '<a href="/apps/cards/">Board</a>' in body


def test_chat_page_board_link_does_not_escape_to_the_site_root():
    """The actual #556/#557 regression: an absolute "/" leaves the mount.

    Separate from the test above because "the right link is present" and "the
    wrong link is absent" are different claims, and a single assertion that
    only checks presence would still pass if BOTH were emitted.
    """
    # Arrange
    request = RequestFactory().get("/apps/cards/chat")
    # Act
    body = views.chat_page(request).content.decode("utf-8")
    # Assert
    assert '<a href="/">Board</a>' not in body


# EOF
