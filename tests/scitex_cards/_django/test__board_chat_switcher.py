#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The Board | Chat switcher must exist on BOTH pages and survive a sub-path mount.

WHY THIS FILE EXISTS
--------------------
1. DISCOVERABILITY. ``/chat/`` shipped reachable only by typing the URL.
   Operator, 2026-07-28 (TG): 「今だと chat が隠し URL みたいになってしまって
   いるので、ホームに Board | Chat のスイッチャーを付けて欲しいです。」 They are
   migrating off Telegram onto this chat, so a chat page nobody can navigate to
   is a migration blocker. These tests fail the moment either page loses its
   switcher — i.e. the moment the chat goes back into hiding.

2. MOUNT AWARENESS. The hub mounts this app under a sub-path (``/apps/cards/``),
   where a hardcoded ``/`` lands on the hub's own landing page and a hardcoded
   ``/chat`` 404s. Two shipped bugs (#556, #557) were exactly this class, which
   is why every href assertion below is made against a SUB-PATH request and not
   only against the standalone root mount.

Rendered against the real views with ``RequestFactory`` — the convention the
sibling ``_django`` view tests already follow (no mocks, no fake templates).
"""

from __future__ import annotations

import os
import re
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
_PARTIAL = _DJANGO_DIR / "templates" / "scitex_cards" / "_page_switcher.html"
_SWITCHER_CSS = _DJANGO_DIR / "static" / "scitex_cards" / "page-switcher.css"

_ITEM_RE = re.compile(r"<a[^>]*stx-cards-switcher__item[^>]*>([^<]*)</a>")


def _switcher_item(html: str, label: str) -> str:
    """Return the switcher's rendered ``<a …>Label</a>``, or "" when absent."""
    for match in _ITEM_RE.finditer(html):
        if match.group(1).strip() == label:
            return match.group(0)
    return ""


@pytest.fixture
def store():
    """Seed the canonical DB and reset the board cache around the test.

    The board page renders through the live board loader, so it needs a store
    it may read — an EXPLICIT, per-test one (``$SCITEX_CARDS_DB`` is repointed
    at a scratch path by the top-level conftest), never the fleet's.
    """
    from conftest import seed_db_from_doc

    from scitex_cards._yaml import safe_load

    doc = safe_load(_STORE_TEXT) or {}
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    _reset_cache()
    yield os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _reset_cache()


@pytest.fixture
def board_at_subpath(store):
    """The board home as the hub serves it: mounted under ``/apps/cards/``."""
    request = RequestFactory().get(f"/apps/cards/?store={store}")
    return views.board_v3_page(request).content.decode("utf-8")


@pytest.fixture
def board_at_root():
    """The board home as the standalone server serves it (root mount)."""
    request = RequestFactory().get("/")
    return views.board_v3_page(request).content.decode("utf-8")


@pytest.fixture
def chat_at_subpath():
    """The DM page as the hub serves it: mounted under ``/apps/cards/``."""
    request = RequestFactory().get("/apps/cards/chat")
    return views.chat_page(request).content.decode("utf-8")


@pytest.fixture
def chat_at_root():
    """The DM page as the standalone server serves it (root mount)."""
    request = RequestFactory().get("/chat")
    return views.chat_page(request).content.decode("utf-8")


# --- the board home offers a way INTO the chat -----------------------------


def test_board_page_links_to_the_chat_page(board_at_subpath):
    """Without this link the chat is a URL you have to know — the whole
    complaint. The href must carry the mount prefix, not a bare "/chat"."""
    # Arrange
    html = board_at_subpath
    # Act
    item = _switcher_item(html, "Chat")
    # Assert
    assert 'href="/apps/cards/chat"' in item


def test_board_page_chat_link_is_root_relative_at_root_mount(board_at_root):
    """Standalone (:8051) the include root is "/", so the link is "/chat" —
    the URL the operator was typing by hand."""
    # Arrange
    html = board_at_root
    # Act
    item = _switcher_item(html, "Chat")
    # Assert
    assert 'href="/chat"' in item


def test_board_page_marks_the_board_as_the_active_surface(board_at_subpath):
    """A switcher that does not say where you are is a pair of links, not a
    switcher. ``aria-current`` is the assertion because it is what a screen
    reader and the CSS active state both key off."""
    # Arrange
    html = board_at_subpath
    # Act
    item = _switcher_item(html, "Board")
    # Assert
    assert 'aria-current="page"' in item


def test_board_page_does_not_mark_chat_as_active(board_at_subpath):
    """Exactly one item may be current; a second one would make the marker
    meaningless."""
    # Arrange
    html = board_at_subpath
    # Act
    item = _switcher_item(html, "Chat")
    # Assert
    assert "aria-current" not in item


def test_board_page_loads_the_shared_switcher_stylesheet(board_at_subpath):
    """Unstyled, the switcher is two stacked links the operator will not read
    as a control."""
    # Arrange
    stylesheet = "scitex_cards/page-switcher.css"
    # Act
    linked = stylesheet in board_at_subpath
    # Assert
    assert linked


# --- the chat page offers a way BACK to the board --------------------------


def test_chat_page_links_back_to_the_board(chat_at_subpath):
    """The board link must stay inside the mount: on the hub "/" is the hub's
    own landing page, not this board."""
    # Arrange
    html = chat_at_subpath
    # Act
    item = _switcher_item(html, "Board")
    # Assert
    assert 'href="/apps/cards/"' in item


def test_chat_page_board_link_is_root_relative_at_root_mount(chat_at_root):
    """Standalone the include root is "/" — chat_page strips its own trailing
    segment off request.path to recover it."""
    # Arrange
    html = chat_at_root
    # Act
    item = _switcher_item(html, "Board")
    # Assert
    assert 'href="/"' in item


def test_chat_page_marks_chat_as_the_active_surface(chat_at_subpath):
    """Same switcher template, opposite active item — the partial takes the
    page name from its caller rather than sniffing the request."""
    # Arrange
    html = chat_at_subpath
    # Act
    item = _switcher_item(html, "Chat")
    # Assert
    assert 'aria-current="page"' in item


def test_chat_page_does_not_mark_board_as_active(chat_at_subpath):
    """See the board-side twin: exactly one current item."""
    # Arrange
    html = chat_at_subpath
    # Act
    item = _switcher_item(html, "Board")
    # Assert
    assert "aria-current" not in item


def test_chat_page_loads_the_shared_switcher_stylesheet(chat_at_subpath):
    """Both pages must link the SAME stylesheet, or the two switchers drift
    into looking like two different controls."""
    # Arrange
    stylesheet = "scitex_cards/page-switcher.css"
    # Act
    linked = stylesheet in chat_at_subpath
    # Assert
    assert linked


# --- lint: the partial may never hardcode a root-absolute href -------------


def test_switcher_partial_has_no_root_absolute_href():
    """The regression that produced #556/#557: a literal ``href="/…"`` escapes
    a sub-path mount. Every href must be built from ``api_base``."""
    # Arrange
    partial_path = _PARTIAL
    # Act
    source = partial_path.read_text(encoding="utf-8")
    # Assert
    assert 'href="/' not in source


def test_switcher_partial_builds_every_href_from_api_base():
    """Counting the hrefs against the api_base uses keeps a new, unprefixed
    link from slipping past the literal lint above."""
    # Arrange
    partial_path = _PARTIAL
    # Act
    source = partial_path.read_text(encoding="utf-8")
    # Assert
    assert source.count("href=") == source.count('href="{{ api_base }}')


def test_switcher_stylesheet_exists_where_both_templates_point():
    """A {% static %} link to a missing file renders fine and 404s silently in
    the browser, leaving the switcher unstyled on both pages."""
    # Arrange
    css_path = _SWITCHER_CSS
    # Act
    exists = css_path.is_file()
    # Assert
    assert exists


# EOF
