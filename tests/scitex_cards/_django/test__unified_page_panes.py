#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ONE page, TWO panes: the unified Cards surface (operator 2026-10-20).

DM vs Board live in the SAME page, switched by hash (#dm / #board), under
ONE shared header that never reloads — no more fluctuation between two
full-page loads. Both panes' scripts start on page load, so data preloads
in parallel.

Rendered against the real chat_page view with ``RequestFactory`` — no mocks,
no fake templates — and PG-free (the view degrades the unread count without
a store). One assertion per test + AAA markers (STX-TQ002/TQ007).
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.views import _cards_version  # noqa: E402

_ID_RE = re.compile(r'id="([a-zA-Z0-9_-]+)"')


@pytest.fixture
def unified_html():
    """The unified page as the standalone server serves it (root mount)."""
    request = RequestFactory().get("/dm")
    return views.chat_page(request).content.decode("utf-8")


def _headers(html: str) -> list[str]:
    """Rendered shared-header rows (SDK title row partial)."""
    return re.findall(r'<header class="stx-app-header', html)


# --- one page, two panes ----------------------------------------------------


def test_unified_page_serves_the_dm_pane(unified_html):
    # Arrange
    html = unified_html
    # Act
    present = 'id="dm-pane"' in html
    # Assert
    assert present


def test_unified_page_serves_the_hidden_board_pane(unified_html):
    # Arrange
    html = unified_html
    # Act
    present = 'id="board-pane" hidden' in html
    # Assert
    assert present


def test_unified_page_runs_both_panes_scripts(unified_html):
    # Arrange
    html = unified_html
    # Act
    both = ("chat.js" in html, "app_router.js" in html)
    # Assert
    assert both == (True, True)


def test_unified_page_renders_exactly_one_shared_header_mount(unified_html):
    # Arrange — the leaf no longer hand-rolls the row (learn-from-stats):
    # one SDK mount stub per page, identity as data attributes. The version
    # reads live from the same _cards_version() reader the view uses — a
    # release bump must never require editing this pin.
    # Act
    mounts = re.findall(r"<div data-stx-app-header", unified_html)
    titled = 'data-app-title="SciTeX Cards"' in unified_html
    versioned = f'data-app-version="{_cards_version()}"' in unified_html
    # Assert
    assert (len(mounts), titled, versioned) == (1, True, True)


def test_unified_page_mount_carries_no_hand_rolled_header(unified_html):
    # Arrange — the SDK module owns the row chrome; any literal
    # `.stx-app-header` element in server HTML is a fork of it.
    # Act
    hand_rolled = re.findall(r'<header class="stx-app-header', unified_html)
    module = "scitex_sdk/ui/js/app/app-header.js" in unified_html
    adopt = "scitex_cards/app_header.js" in unified_html
    # Assert
    assert (hand_rolled, module, adopt) == ([], True, True)


def test_unified_page_marks_dm_active_on_first_paint(unified_html):
    # Arrange — the switcher item carrying --on must be the DM one.
    on_dm = re.search(
        r'<a[^>]*stx-cards-switcher__item--on[^>]*href="([^"]*)"', unified_html
    )
    # Act
    href = on_dm.group(1) if on_dm else ""
    # Assert — the DM default landing (CCT4610) survives unification.
    assert href.endswith("/dm") or href.endswith("dm")


def test_unified_page_carries_no_duplicate_ids(unified_html):
    # Arrange
    ids = _ID_RE.findall(unified_html)
    # Act
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    # Assert
    assert duplicated == []


def test_unified_page_tab_row_is_not_inside_the_title_row(unified_html):
    # Arrange — the SDK title mount holds label + version + project selector
    # ONLY (operator 2026-10-20); the tab row is its sibling, never nested.
    # The mount stub carries no switcher: the SDK renders the row from data
    # attributes, and switcher markup inside it would fork shared chrome.
    mount = re.search(
        r'<div data-stx-app-header.*?</div>', unified_html, re.DOTALL
    )
    # Act
    nested = "stx-cards-switcher" in (mount.group(0) if mount else "")
    # Assert
    assert mount is not None and not nested
