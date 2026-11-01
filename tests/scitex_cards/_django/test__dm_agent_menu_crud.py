#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The agent-list right-click menu offers every honest CRUD op.

Prompt-02 (operator 2026-10-22): CRUD ops must be available as the
right-click menu on the agent list — which offered only Hide, then
Hide + Pin. rename/delete/archive/mute have NO backend (append-only
store, no tombstone; the delete card is deferred), so a menu item for
them would be a dead control. The menu therefore grows only with items
that work:

  1. Mark as read — reuses the thread route's GET ?mark_read=1 (the same
     ack opening the thread sends) without switching threads; a refused
     ack reads the typed reason like a refused flag.
  2. Copy name — client-side clipboard with an execCommand fallback, so
     it needs no endpoint at all.

What is pinned (source-read, no mocks — the menu touches the DOM and
cannot be required under node):

  1. chat.html serves the am-read + am-copy buttons beside am-hide/am-pin.
  2. chat_menu.js wires both (mark_read=1 fetch; clipboard writeText).
  3. No dead items: rename/delete/archive/mute stay unwired.

One assertion per test (PA-307: STX-TQ007) + AAA markers (STX-TQ002).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("django")

_MENU_JS = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
    / "chat_menu.js"
)

from scitex_cards._django import views  # noqa: E402

_CHAT_HTML = (
    Path(views.__file__).resolve().parent
    / "templates"
    / "scitex_cards"
    / "chat.html"
)


def _menu_source() -> str:
    assert _MENU_JS.is_file(), f"chat_menu.js missing: {_MENU_JS}"
    return _MENU_JS.read_text(encoding="utf-8")


def test_the_page_serves_read_and_copy_items() -> None:
    """am-read + am-copy ride beside am-hide/am-pin in the served menu."""
    # Arrange
    html = _CHAT_HTML.read_text(encoding="utf-8")
    # Act
    served = 'id="am-read"' in html and 'id="am-copy"' in html
    # Assert
    assert served


def test_mark_as_read_reuses_the_thread_ack() -> None:
    """Mark-as-read GETs ?mark_read=1 — the ack opening a thread sends."""
    # Arrange
    source = _menu_source()
    # Act
    ack = "mark_read=1" in source and "getMarkRead" in source
    # Assert
    assert ack


def test_copy_name_needs_no_endpoint() -> None:
    """Copy-name is clipboard-only, with a fallback where it is absent."""
    # Arrange
    source = _menu_source()
    # Act
    clip = "clipboard.writeText" in source and "execCommand" in source
    # Assert
    assert clip


def test_backendless_items_stay_unwired() -> None:
    """rename/delete/archive/mute get no menu wiring (no backend)."""
    # Arrange
    source = _menu_source()
    # Act
    dead = (
        "am-rename" not in source
        and "am-delete" not in source
        and "postRename" not in source
        and "postDelete" not in source
    )
    # Assert
    assert dead


# EOF
