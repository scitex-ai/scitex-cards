"""Collapsed Details rail must stay expandable (operator 2026-10-23).

The SDK stacks title-then-toggle in a full-height column; the board's
vertical title (~350px) pushed the 13px toggle below the pane's clipped
bottom on short viewports — a 48px strip with no reachable control, so the
Details column "cannot be expanded". The contract pinned here:

1. 13-details-pane.css pins the toggle FIRST (order:-1) in the collapsed
   rail, so it is always the topmost, clickable thing;
2. the collapsed rail itself expands on any rail click (guarded to the
   collapsed state, so clicks in an open pane never collapse it);
3. the toggle button keeps its own handler (the rail click is additive,
   not a replacement).

Mocks-free: open the source files and read them.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

_DETAILS_CSS = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "13-details-pane.css"
)
_BOARD_PANE_TEMPLATE = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "_board_pane.html"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


def test_details_css_pins_toggle_first_in_collapsed_rail() -> None:
    """The collapsed-rail toggle rule must exist and order it before the title."""
    # Arrange
    # Act
    css = _read(_DETAILS_CSS)
    # Assert
    m = re.search(
        r"\.details-pane\.stx-shell-sidebar\.collapsed\s+\.stx-shell-sidebar__toggle\s*\{([^}]*)\}",
        css,
    )
    assert m is not None and "order" in m.group(1) and "-1" in m.group(1), (
        "13-details-pane.css must pin the collapsed-rail toggle first "
        "(order:-1) so it cannot clip out of reach"
    )


def test_details_pane_expands_on_guarded_rail_click() -> None:
    """The aside must expand on any collapsed-rail click, guarded to collapsed only."""
    # Arrange
    # Act
    html = _read(_BOARD_PANE_TEMPLATE)
    # Assert
    assert (
        "onclick=\"if(this.classList.contains('collapsed'))toggleDetailsPane()\""
        in html
    ), "details aside lost its guarded collapsed-rail expand handler"


def test_details_toggle_button_keeps_its_own_handler() -> None:
    """The rail click is additive — the toggle button keeps onclick (runs first on bubble)."""
    # Arrange
    # Act
    html = _read(_BOARD_PANE_TEMPLATE)
    # Assert
    assert 'id="details-toggle"' in html and 'onclick="toggleDetailsPane()"' in html, (
        "details toggle button lost its own toggleDetailsPane() handler"
    )


def test_details_css_balanced_braces() -> None:
    """Edits did not corrupt brace nesting."""
    # Arrange
    # Act
    css = _read(_DETAILS_CSS)
    # Assert
    assert css.count("{") == css.count("}"), (
        f"unbalanced braces in 13-details-pane.css: "
        f"{css.count('{')} opens vs {css.count('}')} closes"
    )
