#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The chrome title follows the #dm/#board switch (operator 2026-10-31).

Two screenshots showed the title stuck on the wrong pane: #board active
while the tab read "Cards DM (3) (22 in last 1h)" (board suffix compounded
onto a DM-written title) and "Cards DM (3)" (stale DM title, no repaint on
switch). The contract: the router repaints the visible pane's title on
every switch via optional window.STX.paneTitle[which] hooks — a title must
never break a pane switch, so absent hooks are a no-op.

Source pins over the three client files (title logic is browser-side; the
node suite covers repaintTitle's behavior directly).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("django")

_STATIC = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
)
_TEMPLATES = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing: {path}"
    return path.read_text(encoding="utf-8")


def test_router_repaints_the_visible_pane_title_on_show() -> None:
    """show() must repaint — poll ticks are up to ~10s away."""
    # Arrange
    source = _read(_STATIC / "app_router.js")
    # Act
    wired = "repaintTitle(which)" in source and "STX.paneTitle" in source
    # Assert
    assert wired


def test_router_title_repaint_never_breaks_a_switch() -> None:
    """Absent hooks (standalone pages) are a no-op, never a throw."""
    # Arrange
    source = _read(_STATIC / "app_router.js")
    # Act
    start = source.find("function repaintTitle")
    body = source[start : start + 600] if start != -1 else ""
    # Assert — one compound assertion (PA-307: single assert per test).
    assert start != -1 and "try" in body and "catch" in body


def test_dm_registers_its_title_hook_off_the_cached_agents() -> None:
    """The DM hook reuses the last poll's list — never a new request."""
    # Arrange
    source = _read(_STATIC / "chat" / "chat.js")
    # Act
    hook = "paneTitle.dm" in source and "pageTitle.update(state.agents" in source
    # Assert
    assert hook


def test_board_registers_its_title_hook_off_the_cached_nodes() -> None:
    """The board hook reuses the last rendered graph — never a new request."""
    # Arrange
    source = _read(_TEMPLATES / "_board_foot.html")
    # Act
    hook = "paneTitle.board" in source and "STATE.graph" in source
    # Assert
    assert hook


# EOF
