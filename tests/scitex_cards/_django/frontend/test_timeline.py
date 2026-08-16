#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSS + template contract tests for the Time View layout.

Operator-direct ask (TG, relayed by lead a2a ``d0f7a0e3``, 2026-06-14):
add a live raster TIME VIEW as the 5th LAYOUT toggle alongside Graph /
Table / Recent / Calendar.

This module pins the LOAD-BEARING CSS CONTRACT: key selectors are
present + NO hardcoded colors (the dark/light theme flip rides entirely
on the scitex-ui token variables). Mocks-free: open the source files
and grep them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[4]

_TIMELINE_CSS = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "frontend"
    / "src"
    / "styles"
    / "timeline.css"
)

_BOARD_CSS = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "frontend"
    / "src"
    / "styles"
    / "board.css"
)

_TIMELINE_TSX = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "frontend"
    / "src"
    / "TimelineView.tsx"
)

_CARDSBOARD_TSX = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "frontend"
    / "src"
    / "CardsBoard.tsx"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


# ============================================================================
# Half A — CSS contract: every required selector is present
# ============================================================================


@pytest.mark.parametrize(
    "selector",
    [
        ".stx-cards-timeline",
        ".stx-cards-timeline__bar",
        ".stx-cards-timeline__title",
        ".stx-cards-timeline__controls",
        ".stx-cards-timeline__select",
        ".stx-cards-timeline__svg",
        ".stx-cards-timeline__lane-label",
        ".stx-cards-timeline__lane-bg",
        ".stx-cards-timeline__bar--completed",
        ".stx-cards-timeline__edge",
        ".stx-cards-timeline__ticktext",
        ".stx-cards-timeline__tickline",
    ],
)
def test_timeline_css_declares_selector(selector: str) -> None:
    """Every load-bearing selector must have at least one rule in
    timeline.css. Catches a rename / accidental drop."""
    # Arrange
    # Act
    css = _read(_TIMELINE_CSS)
    # Assert
    assert selector in css, f"timeline.css missing rule for {selector!r}"


def test_timeline_css_no_hardcoded_colors_hex_hits() -> None:
    """No hex / rgb / named-color literals — everything must ride through
    the scitex-ui token variables so the dark/light flip stays clean."""
    # Arrange
    css = _read(_TIMELINE_CSS)
    # Strip comments before scanning so /* ...#ffffff... */ doc colour
    # tokens don't trip the assertion.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Hex colors.
    # Act
    hex_hits = re.findall(r"#[0-9a-fA-F]{3,8}\b", no_comments)
    # Assert
    # rgb()/rgba()/hsl()/hsla() literals.
    func_hits = re.findall(r"\b(?:rgb|rgba|hsl|hsla)\s*\(", no_comments)
    # Common CSS named colors — not exhaustive, but covers the easy
    # mistakes ("red", "blue", etc.).
    named = re.findall(
        r":\s*(red|blue|green|yellow|orange|purple|black|white|gray|grey|"
        r"pink|cyan|magenta)\b",
        no_comments,
        re.IGNORECASE,
    )
    assert hex_hits == [], f"timeline.css has hex colors: {hex_hits!r}"


def test_timeline_css_no_hardcoded_colors_func_hits() -> None:
    """No hex / rgb / named-color literals — everything must ride through
    the scitex-ui token variables so the dark/light flip stays clean."""
    # Arrange
    css = _read(_TIMELINE_CSS)
    # Strip comments before scanning so /* ...#ffffff... */ doc colour
    # tokens don't trip the assertion.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Hex colors.
    # Act
    hex_hits = re.findall(r"#[0-9a-fA-F]{3,8}\b", no_comments)
    # Assert
    # rgb()/rgba()/hsl()/hsla() literals.
    func_hits = re.findall(r"\b(?:rgb|rgba|hsl|hsla)\s*\(", no_comments)
    # Common CSS named colors — not exhaustive, but covers the easy
    # mistakes ("red", "blue", etc.).
    named = re.findall(
        r":\s*(red|blue|green|yellow|orange|purple|black|white|gray|grey|"
        r"pink|cyan|magenta)\b",
        no_comments,
        re.IGNORECASE,
    )
    assert func_hits == [], f"timeline.css has color function literals: {func_hits!r}"


def test_timeline_css_no_hardcoded_colors_named() -> None:
    """No hex / rgb / named-color literals — everything must ride through
    the scitex-ui token variables so the dark/light flip stays clean."""
    # Arrange
    css = _read(_TIMELINE_CSS)
    # Strip comments before scanning so /* ...#ffffff... */ doc colour
    # tokens don't trip the assertion.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Hex colors.
    # Act
    hex_hits = re.findall(r"#[0-9a-fA-F]{3,8}\b", no_comments)
    # Assert
    # rgb()/rgba()/hsl()/hsla() literals.
    func_hits = re.findall(r"\b(?:rgb|rgba|hsl|hsla)\s*\(", no_comments)
    # Common CSS named colors — not exhaustive, but covers the easy
    # mistakes ("red", "blue", etc.).
    named = re.findall(
        r":\s*(red|blue|green|yellow|orange|purple|black|white|gray|grey|"
        r"pink|cyan|magenta)\b",
        no_comments,
        re.IGNORECASE,
    )
    assert named == [], f"timeline.css has named color literals: {named!r}"


def test_timeline_css_imported_by_board_css() -> None:
    """board.css must @import timeline.css so the bundle picks it up."""
    # Arrange
    # Act
    css = _read(_BOARD_CSS)
    # Assert
    assert '@import "./timeline.css";' in css


def test_timeline_css_uses_token_variables() -> None:
    """The stylesheet must rely on the scitex-ui token namespace
    (``--stx-…``) — that's how the dark/light flip propagates."""
    # Arrange
    # Act
    css = _read(_TIMELINE_CSS)
    # Assert
    assert "var(--stx-" in css, (
        "timeline.css should reference at least one --stx-* token "
        "variable so the dark/light flip wires up"
    )


# ============================================================================
# Half B — TSX wiring: the 5th LAYOUT toggle exists + TimelineView mounted
# ============================================================================


def test_timeline_view_is_a_module() -> None:
    """TimelineView.tsx exports the TimelineView component."""
    # Arrange
    # Act
    tsx = _read(_TIMELINE_TSX)
    # Assert
    assert "export function TimelineView(" in tsx


def test_cardsboard_wires_timeline_view_tsx_contains() -> None:
    """CardsBoard.tsx mounts TimelineView when ``view === 'timeline'`` and
    the toggle button sets view='timeline'."""
    # Arrange
    # Act
    tsx = _read(_CARDSBOARD_TSX)
    # Assert
    assert "import { TimelineView }" in tsx


def test_cardsboard_wires_timeline_view_tsx_contains_2() -> None:
    """CardsBoard.tsx mounts TimelineView when ``view === 'timeline'`` and
    the toggle button sets view='timeline'."""
    # Arrange
    # Act
    tsx = _read(_CARDSBOARD_TSX)
    # Assert
    assert 'setView("timeline")' in tsx


def test_cardsboard_wires_timeline_view_tsx_contains_3() -> None:
    """CardsBoard.tsx mounts TimelineView when ``view === 'timeline'`` and
    the toggle button sets view='timeline'."""
    # Arrange
    # Act
    tsx = _read(_CARDSBOARD_TSX)
    # Assert
    assert 'view === "timeline"' in tsx


def test_cardsboard_polls_30s_default_window() -> None:
    """The TimelineView component declares a polling cadence that matches
    the other fleet surfaces (30s) — keeps the operator's "what just
    changed" cognitive load uniform."""
    # Arrange
    # Act
    tsx = _read(_TIMELINE_TSX)
    # Assert
    assert "30_000" in tsx or "30000" in tsx
