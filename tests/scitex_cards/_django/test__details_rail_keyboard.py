"""Collapsed Details rail must be keyboard-expandable (operator 2026-10-23 follow-up).

defc57f2 gave the collapsed rail a mouse-only expand path (a guarded
onclick on the aside). Keyboard-only users had no equivalent: the aside was
not focusable and carried no keydown handler. The contract pinned here:

1. the aside is a tab stop while collapsed (tabindex="0" in the template —
   collapsed-by-default per the operator mandate) with a guarded
   Enter/Space keydown that expands under the same collapsed guard as the
   mouse handler (Space preventDefaults so the page does not scroll);
2. toggleDetailsPane() keeps the tab stop in sync (0 collapsed, -1 open —
   open, every inner control is reachable on its own);
3. _syncDetailsPane() applies the same contract at boot from the stored
   preference.

Mocks-free: open the source files and read them.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

_BOARD_PANE_TEMPLATE = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "_board_pane.html"
)
_BOARD_FOOT_TEMPLATE = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "_board_foot.html"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


def _aside_tag(html: str) -> str:
    m = re.search(r'<aside\b[^>]*id="details-panel"[^>]*>', html, re.DOTALL)
    assert m is not None, "details-panel aside missing from _board_pane.html"
    return m.group(0)


def test_details_rail_is_focusable_while_collapsed() -> None:
    """The rail must be a tab stop (collapsed-by-default matches the mandate)."""
    # Arrange
    # Act
    aside = _aside_tag(_read(_BOARD_PANE_TEMPLATE))
    # Assert
    assert 'tabindex="0"' in aside, (
        "details aside lost tabindex=\"0\" — keyboard users cannot reach "
        "the collapsed rail"
    )


def test_details_rail_expands_on_enter_or_space_when_collapsed() -> None:
    """Enter/Space on the rail must expand it under the collapsed guard."""
    # Arrange
    # Act
    aside = _aside_tag(_read(_BOARD_PANE_TEMPLATE))
    # Assert
    m = re.search(r'onkeydown="([^"]*)"', aside)
    handler = m.group(1) if m else ""
    # Assert — one compound assertion (PA-307): the handler must exist,
    # stay guarded to collapsed, take Enter+Space, not scroll on Space,
    # and expand through toggleDetailsPane().
    assert (
        m is not None
        and "classList.contains('collapsed')" in handler
        and "Enter" in handler
        and "' '" in handler
        and "preventDefault" in handler
        and "toggleDetailsPane()" in handler
    ), "details aside lost its guarded Enter/Space expand handler"


def test_toggle_details_pane_syncs_rail_tab_stop() -> None:
    """toggleDetailsPane must keep the rail tabbable only while collapsed."""
    # Arrange
    # Act
    js = _read(_BOARD_FOOT_TEMPLATE)
    # Assert
    m = re.search(
        r"function toggleDetailsPane\(\) \{(.*?)\n    \}",
        js,
        re.DOTALL,
    )
    body = m.group(1) if m else ""
    # Assert — one compound assertion (PA-307).
    assert m is not None and re.search(
        r"pane\.tabIndex\s*=\s*on\s*\?\s*0\s*:\s*-1", body
    ), "toggleDetailsPane() must sync pane.tabIndex (0 collapsed, -1 open)"


def test_sync_details_pane_applies_rail_tab_stop_at_boot() -> None:
    """_syncDetailsPane must apply the same tab-stop contract from storage."""
    # Arrange
    # Act
    js = _read(_BOARD_FOOT_TEMPLATE)
    # Assert
    m = re.search(
        r"function _syncDetailsPane\(\) \{(.*?)\n    \}",
        js,
        re.DOTALL,
    )
    body = m.group(1) if m else ""
    # Assert — one compound assertion (PA-307).
    assert m is not None and re.search(
        r"pane\.tabIndex\s*=\s*collapsed\s*\?\s*0\s*:\s*-1", body
    ), "_syncDetailsPane() must sync pane.tabIndex alongside the class"
