#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The React / Forward affordances must actually reach the operator's phone.

A feature can be wholly correct and still be invisible: the template can hold
the right markup and the view serve none of it, a script can exist and never be
linked, an icon can be specified and never resolve over the tunnel. These pin
the delivery, not the logic.

Asserted against the RENDERED RESPONSE rather than the template source
wherever the question is "does the operator get this?".

  1. The menu offers React and Forward, and the panels they open exist.
  2. Every chat script is linked, in dependency order.
  3. The route is registered and mount-relative.
  4. The emoji are LITERAL unicode — no icon font, no CDN, no external fetch.
  5. The JS palette and the Python palette are the same set.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402
from django.urls import resolve  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers.dm import dm_reaction_view  # noqa: E402
from scitex_cards._reactions import REACTION_EMOJI  # noqa: E402

_URLCONF = "scitex_cards._django.urls"

_CHAT_JS_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
)


@pytest.fixture()
def page() -> str:
    """The rendered /chat page — what the browser is actually served."""
    response = views.chat_page(RequestFactory().get("/chat"))
    return response.content.decode("utf-8")


@pytest.fixture()
def actions_js() -> str:
    return (_CHAT_JS_DIR / "chat_actions.js").read_text(encoding="utf-8")


@pytest.fixture()
def menu_js() -> str:
    return (_CHAT_JS_DIR / "chat_menu.js").read_text(encoding="utf-8")


# === the menu ==============================================================


def test_the_menu_offers_react(page: str):
    # Arrange
    # Act
    present = 'id="mm-react"' in page
    # Assert
    assert present


def test_the_menu_offers_forward(page: str):
    # Arrange
    # Act
    present = 'id="mm-forward"' in page
    # Assert
    assert present


def test_the_menu_still_offers_reply(page: str):
    # Arrange
    # Act
    present = 'id="mm-reply"' in page
    # Assert
    assert present


def test_the_menu_still_offers_copy(page: str):
    # Arrange
    # Act
    present = 'id="mm-copy"' in page
    # Assert
    assert present


def test_the_reaction_bar_panel_is_served(page: str):
    # Arrange
    # Act
    present = 'id="react-bar"' in page
    # Assert
    assert present


def test_the_forward_picker_panel_is_served(page: str):
    # Arrange
    # Act
    present = 'id="forward-menu"' in page
    # Assert
    assert present


def test_the_panels_use_the_shared_context_menu_component(page: str):
    # Arrange
    # Act
    count = page.count("stx-app-context-menu")
    # Assert
    # base owns the look; a private stylesheet here would drift from the board.
    assert count >= 3


# === script delivery =======================================================


@pytest.mark.parametrize(
    "name",
    ["chat_diff.js", "chat_actions.js", "chat_menu.js", "chat_attach.js", "chat.js"],
)
def test_every_chat_script_is_linked(page: str, name: str):
    # Arrange
    # Act
    present = name in page
    # Assert
    assert present


def test_the_scripts_are_linked_in_dependency_order(page: str):
    # Arrange
    order = ["chat_diff.js", "chat_actions.js", "chat_menu.js", "chat.js"]
    # Act — match the SCRIPT REFERENCE, not any mention of the filename. Indexing
    # the bare token also matches prose: a template comment naming a module put
    # its "hit" thousands of bytes before the real <script>, and this test went
    # red for a documentation change (2026-07-30). `src="..."` cannot be produced
    # by prose, so the test measures what its name claims.
    #
    # The trailing quote is NOT part of the anchor any more. Static URLs now
    # carry a cache-busting `?v=<release>` suffix, so the rendered reference is
    # `chat_diff.js?v=0.24.0"` and anchoring on `chat_diff.js"` stopped matching
    # at all — ValueError, all three CI legs, 2026-07-30. The anchor is the
    # `src="` prefix plus the filename, with the query string optional, which
    # keeps prose out without pinning a value designed to change.
    pattern = 'src="[^"]*{}(?:\\?[^"]*)?"'
    positions = [
        re.search(pattern.format(re.escape(name)), page).start() for name in order
    ]
    # Assert
    # `defer` executes in document order, so this list IS the load order;
    # chat_menu reads ChatActions and the controller mounts chat_menu.
    assert positions == sorted(positions)


# === the route =============================================================


def test_the_reaction_route_is_registered():
    # Arrange
    # Act
    match = resolve("/dm/thread/agent-x/reaction", urlconf=_URLCONF)
    # Assert
    assert match.func is dm_reaction_view


def test_the_reaction_route_passes_the_peer_through():
    # Arrange
    # Act
    match = resolve("/dm/thread/agent-x/reaction", urlconf=_URLCONF)
    # Assert
    assert match.kwargs["peer"] == "agent-x"


def test_the_reaction_route_does_not_shadow_the_thread_route():
    # Arrange
    # Act
    match = resolve("/dm/thread/agent-x", urlconf=_URLCONF)
    # Assert
    assert match.func is not dm_reaction_view


# === self-contained, mount-aware ===========================================


def test_the_menu_builds_its_urls_from_the_injected_api_base(menu_js: str):
    # Arrange
    # Act
    uses_base = "host.apiBase +" in menu_js
    # Assert
    # a hardcoded "/dm/..." escapes the hub's sub-path mount — bugs #556/#557.
    assert uses_base


def test_the_menu_never_fetches_a_root_absolute_url(menu_js: str):
    # Arrange
    # Act
    # The bug shape is a fetch whose FIRST token is a "/..." literal. A
    # `host.apiBase + "/dm/…"` concatenation contains the same substring and is
    # exactly right, so matching the substring alone would fail the correct code.
    hardcoded = re.findall(r'fetch\(\s*"/', menu_js)
    # Assert
    assert hardcoded == []


@pytest.mark.parametrize("source", ["chat_actions.js", "chat_menu.js"])
def test_no_new_script_fetches_an_external_asset(source: str):
    # Arrange
    text = (_CHAT_JS_DIR / source).read_text(encoding="utf-8")
    # Act
    external = re.findall(r"https?://", text)
    # Assert
    # served over a tunnel to a phone: anything external simply does not load.
    assert external == []


def test_the_palette_is_literal_unicode_not_an_icon_class(actions_js: str):
    # Arrange
    # Act
    present = "\U0001f44d" in actions_js
    # Assert
    # an icon font that fails to resolve leaves an empty box where the
    # affordance was — the same reasoning as the literal-text paperclip.
    assert present


def test_the_template_does_not_hardcode_the_emoji(page: str):
    # Arrange
    # Act
    present = "\U0001f44d" in page
    # Assert
    # the bar is built from the JS palette; a copy in the template could drift.
    assert not present


# === one palette, two languages ============================================


def test_the_js_and_python_palettes_are_the_same_set(actions_js: str):
    # Arrange
    # \b-anchored: chat_actions.js now also declares QUICK_REACTION_EMOJI, and
    # an unanchored "REACTION_EMOJI = [" matches INSIDE that name. Whichever
    # came first in the file would win, so this guard could silently compare
    # the quick row against the full Python palette and pass or fail for
    # reasons unrelated to what it claims to check.
    match = re.search(r"(?<![A-Z_])REACTION_EMOJI = \[(.*?)\];", actions_js, re.S)
    # Act
    js_emoji = re.findall(r'"([^"]+)"', match.group(1))
    # Assert
    assert js_emoji == list(REACTION_EMOJI)


# EOF
