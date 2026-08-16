#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE "MY CARDS" PAGE RENDERS, AND KNOWS WHERE TO ASK.

The page half of the phone view (card
``cards-gui-phone-view-own-cards-20260814``). The page itself holds no logic --
that is the point of the split; the decisions live in ``me/me_view.js`` and are
node-tested. What CAN break here, silently and only in production, is the
wiring:

* THE MOUNT PREFIX. The board has shipped this bug twice already (#556, #557):
  a root-absolute path renders fine standalone and 404s the moment the app is
  mounted under scitex.ai's ``/apps/cards/``. The phone view is FOR the hosted
  deployment, so it is the worst possible place to get this wrong -- the page
  would load and then simply never show a card.
* THE SCRIPT PAIR AND THEIR ORDER. ``me.js`` reads ``window.MeView``, so the
  pure module must be loaded first or the page throws on the line that renders.
* DISCOVERABILITY. A page nobody can reach without typing its address is the
  exact complaint that produced the switcher in the first place.

Rendered against the real view with ``RequestFactory``, the convention this
suite already uses.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django._me_page import me_page  # noqa: E402


def _render(path: str) -> str:
    """Render the real page view at ``path`` and return its HTML."""
    return me_page(RequestFactory().get(path)).content.decode()


@pytest.fixture
def page_at_subpath() -> str:
    """The page as scitex.ai serves it -- mounted under ``/apps/cards/``."""
    return _render("/apps/cards/me")


@pytest.fixture
def page_at_root() -> str:
    """The page as the standalone loopback board serves it."""
    return _render("/me")


# --- the mount prefix ------------------------------------------------------


def test_the_page_carries_its_api_base_under_a_sub_path_mount(page_at_subpath):
    """THE REGRESSION. Without this the page loads and never shows a card."""
    # Arrange
    expected = 'data-api-base="/apps/cards/"'
    # Act
    html = page_at_subpath
    # Assert
    assert expected in html


def test_the_page_carries_its_api_base_at_a_root_mount(page_at_root):
    """Root mount renders the bare "/" root, so fetches stay "/me/cards"."""
    # Arrange
    expected = 'data-api-base="/"'
    # Act
    html = page_at_root
    # Assert
    assert expected in html


def test_the_slashed_spelling_serves_the_same_page():
    """A trailing slash is the most natural thing in the world to type."""
    # Arrange
    expected = "My Cards"
    # Act
    html = _render("/me/")
    # Assert
    assert expected in html


def test_the_slashed_spelling_resolves_the_same_api_base():
    """The strip is segment-anchored, so "/me/" must not leave "/me" behind."""
    # Arrange
    expected = 'data-api-base="/"'
    # Act
    html = _render("/me/")
    # Assert
    assert expected in html


# --- the script pair -------------------------------------------------------


def test_the_pure_view_module_is_loaded(page_at_root):
    """``me_view.js`` holds every decision the page makes."""
    # Arrange
    expected = "scitex_cards/me/me_view.js"
    # Act
    html = page_at_root
    # Assert
    assert expected in html


def test_the_dom_module_is_loaded(page_at_root):
    """``me.js`` does the fetching and painting."""
    # Arrange
    expected = "scitex_cards/me/me.js"
    # Act
    html = page_at_root
    # Assert
    assert expected in html


def test_the_pure_module_is_loaded_before_the_dom_module(page_at_root):
    """ORDER IS LOAD-BEARING: me.js reads window.MeView at execution time."""
    # Arrange
    html = page_at_root
    # Act
    pure_first = html.index("me/me_view.js") < html.index("me/me.js")
    # Assert
    assert pure_first is True


# --- discoverability and chrome -------------------------------------------


def test_the_page_offers_the_shared_switcher(page_at_root):
    """A page reachable only by typing its URL is a hidden page."""
    # Arrange
    expected = "stx-cards-switcher"
    # Act
    html = page_at_root
    # Assert
    assert expected in html


def test_the_switcher_marks_this_page_as_the_active_one(page_at_root):
    """A switcher that cannot say where you are is just a row of links."""
    # Arrange
    html = page_at_root
    # Act
    marked = 'href="/me" aria-current="page"' in html
    # Assert
    assert marked is True


def test_the_switcher_links_back_to_the_board_under_the_mount(page_at_subpath):
    """The way OUT of this page must survive the sub-path mount too."""
    # Arrange
    expected = 'href="/apps/cards/board"'
    # Act
    html = page_at_subpath
    # Assert
    assert expected in html


def test_the_page_renders_in_the_dark_theme(page_at_root):
    """Dark is the standing default -- the operator's eyes are sensitive."""
    # Arrange
    expected = 'data-theme="dark"'
    # Act
    html = page_at_root
    # Assert
    assert expected in html


def test_the_page_loads_the_stylesheet_that_defines_its_theme(page_at_root):
    """THE ATTRIBUTE ABOVE IS INERT WITHOUT THIS, and fails silently.

    ``data-theme="dark"`` selects a token set; ``theme.css`` is what DEFINES
    one. Shipped without it, every ``var(--bg-page)`` in this template
    resolved to nothing and the page rendered white-on-black-text with the
    dark attribute sitting there doing nothing. Caught in a headless Chromium
    at 390x844 (computed body background came back transparent), not by any
    test -- so this is the test.
    """
    # Arrange
    expected = "scitex_ui/css/shell/theme.css"
    # Act
    html = page_at_root
    # Assert
    assert expected in html


def test_the_page_asks_for_the_full_phone_viewport(page_at_root):
    """``viewport-fit=cover`` is what makes the safe-area insets non-zero."""
    # Arrange
    expected = "viewport-fit=cover"
    # Act
    html = page_at_root
    # Assert
    assert expected in html


def test_the_page_declares_no_favicon_of_its_own(page_at_root):
    """scitex-ui owns the brand mark; a second copy is a copy that drifts."""
    # Arrange
    html = page_at_root
    # Act
    declares_icon = 'rel="icon"' in html
    # Assert
    assert declares_icon is False


def test_no_django_comment_body_leaks_into_the_page(page_at_root):
    """The multi-line ``{# #}`` trap: its body renders as visible page text.

    Guarded generally in test_views.py; asserted here too because this
    template is comment-heavy and the failure is invisible until someone
    looks at the rendered page on a phone.
    """
    # Arrange
    html = page_at_root
    # Act
    leaked = "DELIBERATELY NOT board_v3.html" in html
    # Assert
    assert leaked is False


# EOF
