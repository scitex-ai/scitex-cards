#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSS + wiring contract tests for the Chat panel.

Lead a2a ``74db4f2d`` + ``10afa799`` greenlight (TRACK-2 Phase 6,
2026-06-14). Pins:

  - Load-bearing CSS selectors present in ``styles/chat.css``.
  - No hardcoded color literals (the dark/light flip rides on the
    scitex-ui token variables).
  - ``board.css`` `@import`s ``chat.css`` so the bundle picks it up.
  - ``NodeDetailPanel.tsx`` imports + mounts ``ChatPanel``.

Mocks-free: open the source files and grep them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[4]

_CHAT_CSS = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "frontend"
    / "src"
    / "styles"
    / "chat.css"
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

_CHAT_TSX = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "frontend"
    / "src"
    / "ChatPanel.tsx"
)

_DETAIL_TSX = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "frontend"
    / "src"
    / "NodeDetailPanel.tsx"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


# ============================================================================
# Half A — CSS contract
# ============================================================================


@pytest.mark.parametrize(
    "selector",
    [
        ".stx-cards-chat",
        ".stx-cards-chat__title",
        ".stx-cards-chat__list",
        ".stx-cards-chat__bubble",
        ".stx-cards-chat__meta",
        ".stx-cards-chat__author",
        ".stx-cards-chat__ts",
        ".stx-cards-chat__text",
        ".stx-cards-chat__error",
        ".stx-cards-chat__form",
        ".stx-cards-chat__author-input",
        ".stx-cards-chat__text-input",
        ".stx-cards-chat__actions",
    ],
)
def test_chat_css_declares_selector(selector: str) -> None:
    """Every load-bearing selector must have at least one rule in
    chat.css. Catches a rename / accidental drop."""
    # Arrange
    # Act
    css = _read(_CHAT_CSS)
    # Assert
    assert selector in css, f"chat.css missing rule for {selector!r}"


def test_chat_css_no_hardcoded_colors_hex_hits() -> None:
    """No hex / rgb / named-color literals — everything rides the
    scitex-ui token variables so the dark/light flip stays clean."""
    # Arrange
    css = _read(_CHAT_CSS)
    # Strip comments before scanning so doc colour tokens don't trip
    # the assertion.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Hex colors.
    # Act
    hex_hits = re.findall(r"#[0-9a-fA-F]{3,8}\b", no_comments)
    # Assert
    # rgb()/rgba()/hsl()/hsla() literals.
    func_hits = re.findall(r"\b(?:rgb|rgba|hsl|hsla)\s*\(", no_comments)
    # Common CSS named colors.
    named = re.findall(
        r":\s*(red|blue|green|yellow|orange|purple|black|white|gray|grey|"
        r"pink|cyan|magenta)\b",
        no_comments,
        re.IGNORECASE,
    )
    assert hex_hits == [], f"chat.css has hex colors: {hex_hits!r}"


def test_chat_css_no_hardcoded_colors_func_hits() -> None:
    """No hex / rgb / named-color literals — everything rides the
    scitex-ui token variables so the dark/light flip stays clean."""
    # Arrange
    css = _read(_CHAT_CSS)
    # Strip comments before scanning so doc colour tokens don't trip
    # the assertion.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Hex colors.
    # Act
    hex_hits = re.findall(r"#[0-9a-fA-F]{3,8}\b", no_comments)
    # Assert
    # rgb()/rgba()/hsl()/hsla() literals.
    func_hits = re.findall(r"\b(?:rgb|rgba|hsl|hsla)\s*\(", no_comments)
    # Common CSS named colors.
    named = re.findall(
        r":\s*(red|blue|green|yellow|orange|purple|black|white|gray|grey|"
        r"pink|cyan|magenta)\b",
        no_comments,
        re.IGNORECASE,
    )
    assert func_hits == [], f"chat.css has color function literals: {func_hits!r}"


def test_chat_css_no_hardcoded_colors_named() -> None:
    """No hex / rgb / named-color literals — everything rides the
    scitex-ui token variables so the dark/light flip stays clean."""
    # Arrange
    css = _read(_CHAT_CSS)
    # Strip comments before scanning so doc colour tokens don't trip
    # the assertion.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Hex colors.
    # Act
    hex_hits = re.findall(r"#[0-9a-fA-F]{3,8}\b", no_comments)
    # Assert
    # rgb()/rgba()/hsl()/hsla() literals.
    func_hits = re.findall(r"\b(?:rgb|rgba|hsl|hsla)\s*\(", no_comments)
    # Common CSS named colors.
    named = re.findall(
        r":\s*(red|blue|green|yellow|orange|purple|black|white|gray|grey|"
        r"pink|cyan|magenta)\b",
        no_comments,
        re.IGNORECASE,
    )
    assert named == [], f"chat.css has named color literals: {named!r}"


def test_chat_css_imported_by_board_css() -> None:
    """board.css must @import chat.css so the bundle picks it up."""
    # Arrange
    # Act
    css = _read(_BOARD_CSS)
    # Assert
    assert '@import "./chat.css";' in css


def test_chat_css_uses_token_variables() -> None:
    """The stylesheet must rely on the scitex-ui token namespace
    (``--stx-…``) — that's how the dark/light flip propagates."""
    # Arrange
    # Act
    css = _read(_CHAT_CSS)
    # Assert
    assert "var(--stx-" in css, (
        "chat.css should reference at least one --stx-* token "
        "variable so the dark/light flip wires up"
    )


# ============================================================================
# Half B — TSX wiring
# ============================================================================


def test_chat_panel_is_a_module() -> None:
    """ChatPanel.tsx exports the ChatPanel component."""
    # Arrange
    # Act
    tsx = _read(_CHAT_TSX)
    # Assert
    assert "export function ChatPanel(" in tsx


def test_chat_panel_polls_30s() -> None:
    """The ChatPanel declares a polling cadence that matches the other
    fleet surfaces (30s) — keeps the operator's "what just changed"
    cognitive load uniform."""
    # Arrange
    # Act
    tsx = _read(_CHAT_TSX)
    # Assert
    assert "30_000" in tsx or "30000" in tsx


def test_node_detail_panel_imports_chat_panel_tsx_contains() -> None:
    """NodeDetailPanel.tsx imports + mounts the ChatPanel so the new
    surface lives in the existing drawer."""
    # Arrange
    # Act
    tsx = _read(_DETAIL_TSX)
    # Assert
    assert "import { ChatPanel }" in tsx


def test_node_detail_panel_imports_chat_panel_tsx_contains_2() -> None:
    """NodeDetailPanel.tsx imports + mounts the ChatPanel so the new
    surface lives in the existing drawer."""
    # Arrange
    # Act
    tsx = _read(_DETAIL_TSX)
    # Assert
    assert "<ChatPanel" in tsx


def test_chat_panel_references_scitex_cards_agent_env() -> None:
    """The component reads SCITEX_CARDS_AGENT to default the author
    field — no hardcoded proper nouns per the architectural principle."""
    # Arrange
    # Act
    tsx = _read(_CHAT_TSX)
    # Assert
    assert "SCITEX_CARDS_AGENT" in tsx


def test_chat_panel_has_fail_loud_error_path_tsx_contains() -> None:
    """The component surfaces a write failure (error state + toast)
    instead of silently dropping the message."""
    # Arrange
    # Act
    tsx = _read(_CHAT_TSX)
    # Assert
    assert "setError(" in tsx


def test_chat_panel_has_fail_loud_error_path_tsx_contains_2() -> None:
    """The component surfaces a write failure (error state + toast)
    instead of silently dropping the message."""
    # Arrange
    # Act
    tsx = _read(_CHAT_TSX)
    # Assert
    assert "showToast" in tsx
