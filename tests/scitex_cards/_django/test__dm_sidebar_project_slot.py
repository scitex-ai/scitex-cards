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
    assert honoured


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


def test_agent_rows_offer_a_context_menu():
    """Right-click on an agent row must open a row menu (operator 2026-10-23:
    CRUD ops as right-click on the agent list). The template serves the
    menu markup; chat_menu.js mounts it. A menu with nothing behind it
    would be a dead control — Hide/Unhide rides the existing visibility
    endpoint, the only row action with a backend."""
    # Arrange
    template = _CHAT.read_text(encoding="utf-8")
    script = (_STATIC / "chat" / "chat_menu.js").read_text(encoding="utf-8")
    # Act
    served = 'id="agent-menu"' in template
    mounted = "mountAgentMenu" in script or "agentMenu" in script
    # Assert
    assert served and mounted


def test_agent_menu_hides_and_unhides_through_the_visibility_endpoint():
    """The row menu's Hide/Unhide posts to the visibility endpoint —
    the same one the swipe action uses. No second write path."""
    # Arrange
    script = (_STATIC / "chat" / "chat_menu.js").read_text(encoding="utf-8")
    hide = (_STATIC / "chat" / "chat_hide.js").read_text(encoding="utf-8")
    # Act
    path = "/dm/thread/"
    menu_posts = path in script and "/hidden" in script
    same_as_swipe = path in hide and "/hidden" in hide
    # Assert
    assert menu_posts and same_as_swipe


def test_agent_menu_receives_the_list_and_refresh_seams():
    """The row menu needs the agent list element (right-click target)
    and the list refresh (post-Hide repaint). chat.js passes both —
    a menu mounted without them silently never opens."""
    # Arrange
    script = (_STATIC / "chat" / "chat.js").read_text(encoding="utf-8")
    # Act
    seams = "agentsEl" in script and "refreshAgents" in script
    mounted = "ChatMenu.mount" in script
    # Assert
    assert seams and mounted


def test_agent_menu_serves_a_pin_item():
    """The row menu offers Pin beside Hide (operator 2026-10-24) — served
    in the template, not built at runtime, so the page can be asserted
    to actually serve it."""
    # Arrange
    template = _CHAT.read_text(encoding="utf-8")
    # Act
    served = 'id="am-pin"' in template
    # Assert
    assert served


def test_agent_menu_is_hidden_until_opened():
    """The Hide/Pin menu must not render on page load (operator 2026-10-24:
    never show it by default) — it opens at the cursor on right-click."""
    # Arrange
    template = _CHAT.read_text(encoding="utf-8")
    # Act
    hidden = "#agent-menu { display: none; }" in template
    shown = "#agent-menu.open { display: block; }" in template
    # Assert
    assert hidden and shown


def test_hover_does_not_reveal_the_inline_row_action():
    """Hide/Pin live in the right-click menu only (operator 2026-10-24) —
    a hover-revealed action reads as a control showing by default. The
    touch slide (.open) keeps its reveal: it is the touch path to Hide."""
    # Arrange
    template = _CHAT.read_text(encoding="utf-8")
    # Act
    no_hover = ":hover .row-act" not in template
    no_focus_reveal = "focus-within" not in template
    slide_kept = ".agent-wrap.open .row-act" in template
    # Assert
    assert no_hover and no_focus_reveal and slide_kept


def test_agent_menu_pins_through_its_own_endpoint():
    """Pin/Unpin posts to the pinned endpoint — the same one-write-path
    rule as Hide, not a second shape."""
    # Arrange
    script = (_STATIC / "chat" / "chat_menu.js").read_text(encoding="utf-8")
    # Act
    posts = '"/pinned"' in script and "{ pinned: pinned }" in script
    refreshed = "refreshAgents" in script
    # Assert
    assert posts and refreshed


def test_agent_menu_labels_pin_from_row_state():
    """The item reads Unpin on a pinned row, Pin elsewhere — decided off
    the row's own markup, the way Hide reads its hidden state."""
    # Arrange
    script = (_STATIC / "chat" / "chat_menu.js").read_text(encoding="utf-8")
    # Act
    labelled = 'pinBtn.textContent = pinned ? "Unpin" : "Pin"' in script
    # Assert
    assert labelled


def test_pinned_rows_carry_their_marker_class():
    """The 📌 mark rides an `is-pinned` row class fed by the server flag —
    chat.js must stamp it or pinned rows float first with no visible mark."""
    # Arrange
    script = _CHAT_JS.read_text(encoding="utf-8")
    # Act
    stamped = "is-pinned" in script and "a.pinned" in script
    # Assert
    assert stamped


def test_agent_list_fits_the_filter_row():
    """The 240px column truncated the filter placeholder mid-shortcut
    ("Filter agents (Ctr…") and cramped Filter+New (operator 2026-10-24:
    the filter must fill its width). The column must clear 280px."""
    # Arrange
    import re

    template = _CHAT.read_text(encoding="utf-8")
    # Act
    match = re.search(r"#agents \{(.*?)\}", template, flags=re.DOTALL)
    width = re.search(r"width:\s*(\d+)px", match.group(1)) if match else None
    # Assert
    assert width is not None and int(width.group(1)) >= 280


def test_agent_filter_carries_a_kbd_shortcut_marker():
    """Operator 2026-10-24: the shortcut lives in a hub-style kbd marker
    at the input's end — the placeholder never inlines it. Served in the
    template, aria-hidden, and pointed nowhere (pointer-events none lives
    in CSS, not assertable here)."""
    # Arrange
    template = _CHAT.read_text(encoding="utf-8")
    # Act
    served = '<kbd class="agent-filter-kbd" aria-hidden="true">Ctrl+K</kbd>' in template
    short = "Ctrl+K to focus" not in template and "(Ctrl+K)" not in template
    # Assert
    assert served and short


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
