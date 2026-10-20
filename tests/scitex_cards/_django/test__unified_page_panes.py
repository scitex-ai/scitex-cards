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


def test_unified_page_renders_exactly_one_shared_header(unified_html):
    # Arrange
    html = unified_html
    # Act
    headers = _headers(html)
    # Assert — a second copy would be the fluctuation the operator called out.
    assert len(headers) == 1


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
