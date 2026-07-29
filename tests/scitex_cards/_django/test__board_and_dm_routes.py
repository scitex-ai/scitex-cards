#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/board`` and ``/dm`` must serve the two pages — WITHOUT unpublishing ``/``.

Operator, 2026-07-29 (TG): the URLs being ``/`` and ``/chat`` is uncomfortable;
they want ``/board`` and ``/dm``. They typed both, got 404 on each, and
reasonably read that as the board being broken.

WHY THEY 404'd — and what it was NOT. It was NOT a collision with the ``dm/*``
JSON API. ``path()`` matches EXACT strings, so ``dm``, ``dm/threads`` and
``dm/thread/<peer>`` are three different routes that cannot shadow one another;
an earlier read of this as "blocked on a collision" was simply wrong. The real
cause was the catch-all ``<path:endpoint>`` at the bottom of the urlconf, which
swallowed both names into ``api_dispatch`` → ``{"error": "Unknown endpoint:
dm"}``. Registering the pages BEFORE the catch-all is the entire fix, and the
last two tests here pin exactly that (they assert the routes do not land on
``api_dispatch``, which is the failure the operator actually saw).

THE ALIAS GUARANTEE IS THE POINT OF THIS FILE. This is an ADDITION, not a
rename: ``/`` and ``/chat`` stay. The operator has both bookmarked, agents
reference them, and the switcher's own hrefs are built from them. Losing one
while "moving to the new names" is the regression that would hurt most, so it
is pinned here alongside the new routes rather than left implicit.

Resolution is pinned to the app's own urlconf so each test states a fact about
THIS package's routes, not about wherever a project happens to mount them —
the convention ``test__chat_page_trailing_slash.py`` already follows.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from django.urls import resolve  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers.chat import chat_view  # noqa: E402
from scitex_cards._django.handlers.dm import (  # noqa: E402
    dm_thread_view,
    dm_threads_view,
)

_URLCONF = "scitex_cards._django.urls"


# --- the names the operator asked for --------------------------------------


def test_board_resolves_to_the_board_view():
    """The operator typed /board and got a JSON 404."""
    # Arrange
    path = "/board"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is views.board_v3_page


def test_dm_resolves_to_the_dm_view():
    """The operator typed /dm and got a JSON 404."""
    # Arrange
    path = "/dm"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is views.chat_page


def test_slashed_board_resolves_to_the_board_view():
    """A trailing slash is the most natural thing in the world to type, and
    `legacy/` + `board-v3/` already carry their slashed spelling."""
    # Arrange
    path = "/board/"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is views.board_v3_page


def test_slashed_dm_resolves_to_the_dm_view():
    """Same trailing-slash contract `/chat/` was given in #(chat slash) after
    the operator hit exactly this on 2026-07-24."""
    # Arrange
    path = "/dm/"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is views.chat_page


def test_board_does_not_fall_through_to_the_api_catch_all():
    """The catch-all is what produced the operator's JSON 404 — the new route
    must be matched BEFORE it, not merely exist somewhere in the urlconf."""
    # Arrange
    path = "/board"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is not views.api_dispatch


def test_dm_does_not_fall_through_to_the_api_catch_all():
    """The other half of the operator's 404."""
    # Arrange
    path = "/dm"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is not views.api_dispatch


# --- THE ALIAS GUARANTEE: the published URLs keep working ------------------


def test_root_still_resolves_to_the_board_view():
    """`/` is the URL in the operator's bookmark and in TG 263. Adding a
    readable name may not cost them the one they already use."""
    # Arrange
    path = "/"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is views.board_v3_page


def test_chat_still_resolves_to_the_dm_view():
    """`/chat` is published: bookmarked, referenced by agents, and the href
    the switcher renders. A published URL is a MIGRATION, not a label."""
    # Arrange
    path = "/chat"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is views.chat_page


def test_slashed_chat_still_resolves_to_the_dm_view():
    """Kept for the same reason as `/chat` — it was itself a fix for an
    operator-hit 404 and must not be undone by the next one."""
    # Arrange
    path = "/chat/"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is views.chat_page


# --- the dm/* JSON API is NOT shadowed by the new page route ---------------


def test_dm_threads_still_resolves_to_the_api():
    """THE claimed collision, measured. The agent-list endpoint the DM page
    polls every ~5s must still reach its handler, not the page."""
    # Arrange
    path = "/dm/threads"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is dm_threads_view


def test_dm_threads_does_not_resolve_to_the_page():
    """Stated as its own fact: reaching *a* handler is not the same as not
    reaching the PAGE, and serving HTML to a JSON poll is the shape the
    collision worry was really about."""
    # Arrange
    path = "/dm/threads"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is not views.chat_page


def test_dm_thread_for_a_peer_still_resolves_to_the_api():
    """The per-peer thread pane's endpoint."""
    # Arrange
    path = "/dm/thread/scitex-dev"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is dm_thread_view


def test_dm_thread_still_captures_its_peer():
    """Reaching the right view with the peer swallowed would serve the thread
    surface with nothing to show."""
    # Arrange
    path = "/dm/thread/scitex-dev"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.kwargs["peer"] == "scitex-dev"


def test_the_per_card_comment_thread_is_still_reachable():
    """`/chat/<card_id>` is a DIFFERENT surface from the DM page; the new
    aliases must not disturb it either."""
    # Arrange
    path = "/chat/some-card-id"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is chat_view


# --- the include root each page derives must know the new aliases ----------


def test_dm_page_derives_the_include_root_at_a_subpath_mount():
    """A page served at a name it cannot strip wires every fetch one segment
    too deep: at /apps/cards/dm the DM poll would go to
    /apps/cards/dm/dm/threads and the page would render, then do nothing."""
    # Arrange
    path = "/apps/cards/dm"
    # Act
    api_base = views._include_root(path, views._DM_ALIASES)
    # Assert
    assert api_base == "/apps/cards/"


def test_board_page_derives_the_include_root_at_a_subpath_mount():
    """Same contract on the board side, where an un-stripped segment means
    every /graph call 404s (the shape of #556 and #557)."""
    # Arrange
    path = "/apps/cards/board"
    # Act
    api_base = views._include_root(path, views._BOARD_ALIASES)
    # Assert
    assert api_base == "/apps/cards/"


def test_the_alias_strip_is_anchored_to_a_whole_segment():
    """`board` is now an alias, so a naive endswith() would eat the tail of an
    unrelated mount: /apps/scoreboard/ must stay itself, not become
    /apps/score."""
    # Arrange
    path = "/apps/scoreboard/"
    # Act
    api_base = views._include_root(path, views._BOARD_ALIASES)
    # Assert
    assert api_base == "/apps/scoreboard/"


# EOF
