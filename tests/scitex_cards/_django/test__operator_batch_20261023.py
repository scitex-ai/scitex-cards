"""Operator batch 2026-10-23: square chips/badges, DWM toggles, status spread,
overlay dropdown rows, Details free-text filter.

Five pins, one per batch item. All mocks-free: read the tracked sources.

1. No pill-shaped chip/badge objects remain in board_v3 CSS (999px only on
   round-by-nature dots/avatars/swatches/tracks).
2. The Stats D/W/M toggle group is squared (2px group radius, clipping).
3. in_progress + deferred leave the amber wedge (STATUS_STYLE SSOT +
   README Table 5 agree on the new sky-blue / violet fills).
4. Details filter rows are width-capped with ellipsis (pane never pushed).
5. Details Filters section owns a free-text input wired two-way to
   STATE.filters.search (same match path as the header search), cleared
   by both clear paths.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BOARD_V3 = (
    _REPO_ROOT / "src" / "scitex_cards" / "_django" / "static" / "scitex_cards" / "board_v3"
)
_TEMPLATES = _REPO_ROOT / "src" / "scitex_cards" / "_django" / "templates" / "scitex_cards"
_MERMAID = (
    _REPO_ROOT / "src" / "scitex_cards" / "_diagram" / "_mermaid.py"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


def _board_v3_css() -> str:
    return "\n".join(
        _read(p) for p in sorted(_BOARD_V3.glob("*.css")) if p.name != "test_graph_builders"
    )


def test_chip_and_badge_objects_are_squared_not_pills() -> None:
    """No 999px radius survives on chip/badge/pill-class rules (dots keep 50%)."""
    # Arrange
    css = _board_v3_css()
    pat = re.compile(
        r"\.[A-Za-z0-9_-]*(?:chip|badge|pill|blocker|qhint)[A-Za-z0-9_-]*"
        r"|\.mx-occ__pill|\.graph-fit-btn|\.toggle-block"
        r"|\.search-suggest__badge|\.group-strip-banner|\.col-count"
        r"|\.fleetstrip|\.stat-bar-track|\.stat-bar"
    )
    # Act: find every rule block whose SELECTOR names a chip/badge/pill object
    # and check its radius.
    bad: list[str] = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector = m.group(1).strip().splitlines()[-1].strip()
        if pat.search(selector) and "999px" in m.group(2):
            bad.append(selector)
    # Assert
    assert not bad, f"pill-shaped chip/badge rules remain (999px): {bad}"


def test_stats_dwm_toggle_group_is_squared() -> None:
    """The D/W/M timescale group clips at 2px (operator: no curly objects)."""
    # Arrange
    # Act
    css = _read(_BOARD_V3 / "13-details-pane.css")
    # Assert
    m = re.search(r"\.details-scale\s*\{([^}]*)\}", css)
    assert m is not None and "border-radius: 2px" in m.group(1), (
        "details-scale group lost its squared 2px radius"
    )


def test_status_spread_leaves_the_amber_wedge() -> None:
    """in_progress reads sky-blue and deferred reads violet (SSOT + README agree)."""
    # Arrange
    # Act
    src = _read(_MERMAID)
    readme = _read(_REPO_ROOT / "README.md")
    # Assert
    assert (
        '"in_progress": ("#b3e5fc"' in src
        and '"deferred": ("#d1c4e9"' in src
        and "#b3e5fc" in readme
        and "#d1c4e9" in readme
    ), "STATUS_STYLE spread or README Table 5 drifted (in_progress sky-blue / deferred violet)"


def test_details_filter_rows_cannot_push_the_pane() -> None:
    """Filter rows cap width with ellipsis; the pane width never shifts."""
    # Arrange
    # Act
    css = _read(_BOARD_V3 / "13-details-pane.css")
    # Assert
    row = re.search(r"\.details-row--filter\s*\{([^}]*)\}", css)
    sel = re.search(
        r"\.details-row--filter select,\s*\.details-row--filter input\.filt-text\s*\{([^}]*)\}",
        css,
    )
    assert row is not None and "overflow" in row.group(1) and "hidden" in row.group(1) and sel is not None and "ellipsis" in sel.group(1), (
        "details filter rows lost their width cap or ellipsis (pane can be pushed)"
    )


def test_details_free_text_filter_wired_two_way() -> None:
    """#f-text exists, writes STATE.filters.search, syncs + clears both ways."""
    # Arrange
    # Act
    pane = _read(_TEMPLATES / "_board_pane.html")
    foot = _read(_TEMPLATES / "_board_foot.html")
    # Assert
    assert (
        'id="f-text"' in pane
        and "onDetailsTextInput(event)" in pane
        and "function onDetailsTextInput(e)" in foot
        and "STATE.filters.search = v" in foot
        and 'getElementById("f-text")' in foot
    ), "Details free-text filter lost input, handler, STATE write, or sync/clear wiring"
