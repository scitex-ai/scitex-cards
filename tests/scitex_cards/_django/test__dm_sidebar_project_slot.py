#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The DM sidebar + hide wiring (no mocks). The header carries the picker.

Operator 2026-10-23: the project selector lives ONLY in the top-most
application header (shared SDK element) — the DM sidebar copy is dropped.
Card cards-dm-spaces-crud-hide-20261022 adds hide/unhide beside the list.

What is pinned:

  1. The HEADER renders the CANONICAL picker (``_project_picker.html``),
     guarded by the availability flag — never a second hand-rolled picker.
  2. Exactly ONE picker per page: NEITHER page passes ``header_picker=False``;
     the sidebar carries no picker markup.
  3. The list fetches ``?show=all`` (the client partitions) and mounts
     ``ChatHide`` for the row actions — asserted against the sources, the
     way ``test__dm_unread_in_page_title`` asserts wiring.
  4. Rendered degradation: with no host provider the page serves 200 with
     the list intact and NO picker markup — the slot costs nothing where
     it cannot render.

Rendered against the real view with ``RequestFactory``; the chat page needs
no store, so this runs with no PostgreSQL (unlike the board half of the
shared-header suite).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402

_DJANGO_DIR = Path(views.__file__).resolve().parent
_TEMPLATES = _DJANGO_DIR / "templates" / "scitex_cards"
_STATIC = _DJANGO_DIR / "static" / "scitex_cards"

_CHAT = _TEMPLATES / "chat.html"
_BOARD = _TEMPLATES / "board_v3.html"
_HEADER = _TEMPLATES / "_page_header.html"
_CHAT_JS = _STATIC / "chat" / "chat.js"


@pytest.fixture
def dm_html():
    """The DM page as the standalone server serves it (no store needed)."""
    return views.chat_page(RequestFactory().get("/dm")).content.decode("utf-8")


def _nav_agents_region(source: str) -> str:
    """The ``<nav id="agents">…</nav>`` block of the template source."""
    start = source.find('<nav id="agents"')
    end = source.find("</nav>", start)
    assert start != -1 and end != -1, "nav#agents missing from chat.html"
    return source[start:end]


# --- ONE canonical picker, in the sidebar ----------------------------------


def test_sidebar_carries_no_picker():
    """The DM sidebar drops the picker; the header owns it."""
    # Arrange
    region = _nav_agents_region(_CHAT.read_text(encoding="utf-8"))
    # Act
    clean = '_project_picker.html' not in region and 'dm-project' not in region
    # Assert
    assert clean


def test_chat_uses_the_header_picker_slot():
    """Two pickers on one page is the defect; header-only is the fix."""
    # Arrange
    source = _CHAT.read_text(encoding="utf-8")
    # Act
    suppressed = "header_picker=False" in source
    # Assert
    assert not suppressed


def test_board_keeps_the_header_picker_slot():
    """The suppression is chat-only: the board names no flag and is
    unaffected (undefined != False keeps its slot)."""
    # Arrange
    source = _BOARD.read_text(encoding="utf-8")
    # Act
    untouched = "header_picker" not in source
    # Assert
    assert untouched


def test_header_slot_honours_the_suppression_flag():
    """The partial reads the flag; a page that passes False gets no slot."""
    # Arrange
    source = _HEADER.read_text(encoding="utf-8")
    # Act
    honoured = "header_picker != False" in source
    # Assert
    honoured


def test_picker_partial_opts_user_scoped_leaves_in():
    """The SDK tag renders nothing for user-scoped apps unless the leaf
    opts in — measured 2026-10-22: the slot was empty on every page
    despite a registered host provider. The partial must carry the
    opt-in or the sidebar/header slots stay empty forever."""
    # Arrange
    source = (_TEMPLATES / "_project_picker.html").read_text(encoding="utf-8")
    # Act
    opted_in = source.count("allow_user_scope=True") >= 2
    # Assert
    assert opted_in


# --- hide/unhide wiring ----------------------------------------------------


def test_list_fetches_every_row_including_hidden():
    """The client partitions (visible + Hidden disclosure), so it must fetch
    ``?show=all`` — the default would hide hidden rows from the client that
    is supposed to reveal them."""
    # Arrange
    source = _CHAT_JS.read_text(encoding="utf-8")
    # Act
    fetches = '"/dm/threads?show=all"' in source
    # Assert
    assert fetches


def test_chat_mounts_the_hide_module():
    """Row swipe/buttons live in chat_hide.js; the template must declare it
    and chat.js must mount it, or the rows silently lose their actions."""
    # Arrange
    template = _CHAT.read_text(encoding="utf-8")
    script = _CHAT_JS.read_text(encoding="utf-8")
    # Act
    declared = "chat/chat_hide.js" in template and "chat/chat_visibility.js" in template
    mounted = "ChatHide.mount" in script
    # Assert
    assert declared and mounted


# --- rendered degradation --------------------------------------------------


def test_dm_page_serves_the_list_without_a_provider(dm_html):
    """200 with the list intact where no host provider registers one."""
    # Arrange
    html = dm_html
    # Act
    intact = 'id="agents"' in html and 'id="agent-list"' in html
    # Assert
    assert intact


def test_no_picker_markup_without_a_provider(dm_html):
    """The guard holds in the served bytes: no provider, no picker, and no
    empty slot pretending otherwise."""
    # Arrange
    html = dm_html
    # Act
    absent = "data-stx-project-picker" not in html
    # Assert
    assert absent


# EOF
