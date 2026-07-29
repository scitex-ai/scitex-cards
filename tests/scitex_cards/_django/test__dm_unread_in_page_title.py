#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The unread count must reach the tab from the EXISTING unread source.

Operator, 2026-07-29 (TG): 「新着がある場合、ページタイトルに新着メッセージ数
（未読メッセージ数）を出してください。多少点滅などエフェクトがあっても良いかも
です。」

``test_chat_title.py`` (under ``tests/.../static/scitex_cards/chat/``) runs the
real decision functions under node. THIS file pins the WIRING those functions
are useless without, and the wiring is where the interesting mistake lives:

  1. THE COUNT HAS ONE SOURCE. ``/dm/threads`` already returns per-peer
     ``unread`` and the drawer already paints it as a numeric badge. The title
     is fed THAT SAME ARRAY from THAT SAME POLL. A second fetch — or a count
     derived from the message list — would be a second answer to "how many
     unread?", and the two would disagree the moment ``mark_read`` landed
     between them. So: chat.js must hand ``state.agents`` to the title module,
     and chat_title.js must contain no fetch of its own.

  2. THE PAGE MUST ACTUALLY SERVE THE MODULE. A behaviour file that no
     ``<script>`` loads is dead code that tests happily exercise, and the
     ordering matters — chat.js reads ``window.ChatTitle`` at boot.

  3. REDUCED MOTION. The preference must be READ from the live window; a
     flash that only "documents" the preference is not honouring it.

Asserted against the rendered page and the shipped source, which is the
convention this repo's other JS-behaviour tests already follow (see
``test__chat_drawer_is_inert_when_closed.py``): there is no browser in CI, and a
test that silently could not observe the behaviour would be worse than none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402

_STATIC = Path(views.__file__).resolve().parent / "static" / "scitex_cards"
_TITLE_JS = _STATIC / "chat" / "chat_title.js"
_CHAT_JS = _STATIC / "chat" / "chat.js"


@pytest.fixture
def chat_html():
    """The DM page as the standalone server serves it."""
    request = RequestFactory().get("/chat")
    return views.chat_page(request).content.decode("utf-8")


@pytest.fixture
def title_source():
    return _TITLE_JS.read_text(encoding="utf-8")


@pytest.fixture
def chat_source():
    return _CHAT_JS.read_text(encoding="utf-8")


# --- the module ships and the page loads it --------------------------------


def test_the_title_module_exists_where_the_page_points():
    """A {% static %} link to a missing file renders fine and 404s silently —
    the tab would simply never gain a count and nothing would say why."""
    # Arrange
    module_path = _TITLE_JS
    # Act
    exists = module_path.is_file()
    # Assert
    assert exists


def test_the_page_loads_the_title_module(chat_html):
    """Dead code otherwise."""
    # Arrange
    html = chat_html
    # Act
    loaded = "chat/chat_title.js" in html
    # Assert
    assert loaded


def test_the_title_module_loads_before_the_orchestrator(chat_html):
    """chat.js reads ``window.ChatTitle`` at boot; `defer` scripts execute in
    document order, so this ordering IS the dependency."""
    # Arrange
    html = chat_html
    # Act
    ordered = html.index("chat_title.js") < html.index("chat/chat.js")
    # Assert
    assert ordered


# --- ONE unread source, and it is the existing one -------------------------


def test_the_orchestrator_feeds_the_title_from_the_thread_list_poll(chat_source):
    """``state.agents`` is the /dm/threads payload — the same array the drawer
    badges are rendered from. This line is the whole "one source of truth"."""
    # Arrange
    source = chat_source
    # Act
    wired = "pageTitle.update(state.agents)" in source
    # Assert
    assert wired


def test_the_title_module_never_fetches_anything(title_source):
    """THE anti-second-source assertion. If this module ever grows a request,
    the tab and the badges become two counts free to disagree."""
    # Arrange
    source = title_source
    # Act
    fetches = "fetch(" in source or "XMLHttpRequest" in source
    # Assert
    assert not fetches


def test_the_title_module_reads_the_same_unread_field_as_the_badges(title_source):
    """Both renderings key off ``unread`` on the peer row — not off a message
    count, not off a cursor this file keeps."""
    # Arrange
    source = title_source
    # Act
    reads_unread = ".unread" in source
    # Assert
    assert reads_unread


def test_the_peer_list_still_renders_its_own_unread_badge(chat_source):
    """The tab count is IN ADDITION to the per-peer badges, never instead of
    them. Adding a second rendering of one fact is where a "cleanup" removes
    the first."""
    # Arrange
    source = chat_source
    # Act
    badges = 'el("span", "badge", String(a.unread))' in source
    # Assert
    assert badges


# --- reduced motion --------------------------------------------------------


def test_the_flash_reads_the_reduced_motion_preference(title_source):
    """Honouring the preference means QUERYING it. Anything else is a comment
    about accessibility rather than accessibility."""
    # Arrange
    source = title_source
    # Act
    queries = "(prefers-reduced-motion: reduce)" in source
    # Assert
    assert queries


def test_the_flash_asks_the_live_window_for_the_preference(title_source):
    """`matchMedia` on the real window — the preference is a runtime fact, not
    a build-time one."""
    # Arrange
    source = title_source
    # Act
    asks = "matchMedia" in source
    # Assert
    assert asks


# --- the flash stops -------------------------------------------------------


def test_the_flash_clears_its_own_timer(title_source):
    """A timer nobody clears is the forever-blink. The alternation must be able
    to end, and it must end on the COUNT rather than wherever it happened to
    be."""
    # Arrange
    source = title_source
    # Act
    clears = "clearInterval" in source
    # Assert
    assert clears


# EOF
