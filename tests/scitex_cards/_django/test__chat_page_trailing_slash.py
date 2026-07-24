#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/chat/`` must serve the DM page instead of 404ing.

The page was registered only as ``chat`` (no trailing slash). ``/chat/`` then
matched NEITHER route: not ``chat``, and not ``chat/<str:card_id>`` — a ``str``
converter will not match an empty segment. So it fell through to the catch-all
``<path:endpoint>`` and answered ``{"error": "Unknown endpoint: chat/"}``.

The operator hit exactly that the first time they opened the page (2026-07-24):
a trailing slash is the most natural thing in the world to type, and it 404'd a
page that exists. ``legacy/`` and ``board-v3/`` already carry the same dual
registration — chat was the odd one out.

Resolution is pinned to the app's own urlconf so the test states a fact about
THIS package's routes, not about wherever a project happens to mount them.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from django.urls import resolve  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers.chat import chat_view  # noqa: E402

_URLCONF = "scitex_cards._django.urls"


class TestChatPageAcceptsBothSpellings:
    """Both spellings reach the DM page; the per-card thread is not shadowed."""

    def test_unslashed_chat_resolves_to_the_dm_page(self):
        # Arrange / Act
        match = resolve("/chat", urlconf=_URLCONF)
        # Assert
        assert match.func is views.chat_page

    def test_slashed_chat_resolves_to_the_dm_page(self):
        """THE regression: `/chat/` used to fall through to the API catch-all."""
        # Arrange / Act
        match = resolve("/chat/", urlconf=_URLCONF)
        # Assert
        assert match.func is views.chat_page

    def test_slashed_route_does_not_shadow_the_per_card_thread(self):
        # Arrange — /chat/<card_id> is a DIFFERENT surface (a card's comment
        # thread). Adding the page's slashed spelling must not swallow it.
        # Act
        match = resolve("/chat/some-card-id", urlconf=_URLCONF)
        # Assert
        assert match.func is chat_view
        assert match.kwargs["card_id"] == "some-card-id"

    def test_neither_spelling_falls_through_to_the_api_catch_all(self):
        # Arrange — the catch-all is what produced the operator's JSON 404.
        # Act
        unslashed = resolve("/chat", urlconf=_URLCONF)
        slashed = resolve("/chat/", urlconf=_URLCONF)
        # Assert
        assert unslashed.func is not views.api_dispatch
        assert slashed.func is not views.api_dispatch
