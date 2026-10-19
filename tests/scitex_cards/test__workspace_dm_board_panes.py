#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Separation pins for the Cards workspace: Board shows boards ONLY.

Operator CCT4612 (2026-10-19) withdraws the DM/chat LEFT pane the
2026-10-19 layout directive had embedded in the board page: DM lives on
its own route/view (/dm, /chat, root) and the tab switcher is the
crossing. This file INVERTS the pins it used to carry — each pin below
asserts an ABSENCE, so a re-embedded pane goes red instead of slipping
back in beside the Board.

When a pin here fails, either the separation regressed or the operator
changed the directive: do not "fix" this file to match the new source
unless the operator did. Each pin names the file that owns the contract
so the fix lands in the right place.

One assertion per test + AAA markers (STX-TQ002/TQ007, per PA-307): each
test below asserts exactly one outcome; multi-fact contracts are split
into one test per fact.
"""

from __future__ import annotations

import re
from pathlib import Path

import scitex_cards

DJANGO = Path(scitex_cards.__file__).parent / "_django"
TEMPLATE = DJANGO / "templates" / "scitex_cards" / "board_v3.html"
STATIC = DJANGO / "static" / "scitex_cards" / "board_v3"


def _board_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_board_template_carries_no_dm_pane_aside():
    """CCT4612: the dm-pane aside is gone from board_v3.html."""
    # Arrange
    markup = _board_text()
    # Act
    present = 'id="dm-pane"' in markup
    # Assert
    assert present is False, "board_v3.html re-grew the dm-pane aside"


def test_board_template_carries_no_dm_threads_region():
    """CCT4612: the live thread-list region is gone with the pane."""
    # Arrange
    markup = _board_text()
    # Act
    present = 'id="dm-threads"' in markup
    # Assert
    assert present is False, "board_v3.html re-grew the dm-threads region"


def test_board_template_carries_no_dm_thread_region():
    """CCT4612: the open-thread region is gone with the pane."""
    # Arrange
    markup = _board_text()
    # Act
    present = 'id="dm-thread"' in markup
    # Assert
    assert present is False, "board_v3.html re-grew the dm-thread region"


def test_board_template_links_no_dm_pane_css():
    """CCT4612: board_v3.html no longer links 19-dm-pane.css."""
    # Arrange
    markup = _board_text()
    # Act
    linked = "19-dm-pane.css" in markup
    # Assert
    assert linked is False, "board_v3.html re-linked 19-dm-pane.css"


def test_board_template_links_no_dm_pane_js():
    """CCT4612: board_v3.html no longer links dmPane.js."""
    # Arrange
    markup = _board_text()
    # Act
    linked = "dmPane.js" in markup
    # Assert
    assert linked is False, "board_v3.html re-linked dmPane.js"


def test_board_template_calls_no_dm_pane_toggle():
    """CCT4612: the pane's collapse control went with the pane."""
    # Arrange
    markup = _board_text()
    # Act
    present = "toggleDmPane()" in markup
    # Assert
    assert present is False, "board_v3.html re-grew the toggleDmPane control"


def test_dm_pane_js_asset_is_removed():
    """CCT4612: dmPane.js itself is deleted, not just unlinked."""
    # Arrange
    asset = STATIC / "dmPane.js"
    # Act
    gone = not asset.exists()
    # Assert
    assert gone is True, "dmPane.js still ships beside the board"


def test_dm_pane_css_asset_is_removed():
    """CCT4612: 19-dm-pane.css itself is deleted, not just unlinked."""
    # Arrange
    asset = STATIC / "19-dm-pane.css"
    # Act
    gone = not asset.exists()
    # Assert
    assert gone is True, "19-dm-pane.css still ships beside the board"


def test_board_template_keeps_the_shared_page_header():
    """The tab switcher arrives via _page_header.html — the crossing stays."""
    # Arrange
    markup = _board_text()
    # Act
    present = "_page_header.html" in markup
    # Assert
    assert present is True, "board_v3.html lost the shared page header"


def test_board_template_marks_board_as_the_active_tab():
    """The switcher on the board page highlights the Board tab."""
    # Arrange
    markup = _board_text()
    # Act
    present = 'active="board"' in markup
    # Assert
    assert present is True, "board_v3.html lost active=board on the switcher"


def test_board_template_keeps_the_board_surface():
    """Boards only — but still boards: .board-area survives the removal."""
    # Arrange
    markup = _board_text()
    # Act
    present = 'class="board-area"' in markup
    # Assert
    assert present is True, "board_v3.html lost the board surface"


def test_template_hash_comments_stay_single_line():
    """Django's `{# #}` is single-line-only in this codebase (PR #173 leak):
    a multi-line span renders its body as literal text into <head>, which
    the browser then shows in the tab. Pin the whole template so no edit
    re-introduces the class."""
    # Arrange
    markup = _board_text()
    # Act
    spans = re.findall(r"\{#.*?#\}", markup, flags=re.S)
    multi = [s for s in spans if "\n" in s]
    # Assert
    assert not multi, f"multi-line {{# #}} leaks into the page: {multi[:2]}"
