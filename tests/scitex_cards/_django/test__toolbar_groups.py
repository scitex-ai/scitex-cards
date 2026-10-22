"""CSS + template contract tests for the board's filterbar (toolbar) groups.

History
-------
The groups were introduced by the "board UI overhaul part 2" declutter
(operator complaint via lead a2a `d1af161e`, 2026-06-14): Search, Filters,
Layout, Sort, "N new" badge, Group, Reload, +Add Task and hide-project were
all crammed into one rubber-band flex row and visually collided. The fix was
a structural reorganization of the ``.filterbar`` children into logical
groups with breathing room and a divider.

DECLUTTER 2026-07-13 (operator TG, this PR)
-------------------------------------------
The operator asked for far more than breathing room — he asked for the
controls to LEAVE the header:

  * "Column, Table view がいらないです、削除してください" — the Column and Table
    layouts are gone, and with them Sort / Group / Group-by-time, which only
    ever acted on Column CARDS.
  * "reload は要らない" / "hide project も" / "Blocking me の大きな表示も"
    ("the legend already conveys it") — the whole STATUS cluster left.
  * "114 new/24h ですが、それも details のほうで" — the recent-count counter
    MOVED to Details > Stats. It did not vanish.
  * The six filter dropdowns moved out of the header popover into the new
    right-hand Details column.

So the toolbar is now: identity | search | Layout | + Add Task.

This module therefore pins BOTH halves of the contract:

  1. the surviving structure (the group classes, the controls that stayed),
  2. and — the load-bearing half after a removal — that the retired controls
     are really GONE from the template and their rules gone from the CSS.

Mocks-free: open the source files and read them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# --- repo paths -----------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]

_TOOLBAR_CSS = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "07-toolbar-groups.css"
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
# board_v3.html includes _board_pane.html, so reads fall through to the
# pane for the markup that moved there (VIEW group → Details > View).
_BOARD_V3_TEMPLATE = _BOARD_PANE_TEMPLATE


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


# ============================================================================
# Half A — CSS contract: every surviving selector is present
# ============================================================================


@pytest.mark.parametrize(
    "selector",
    [
        ".stx-cards-filterbar__group--view",
        ".stx-cards-filterbar__group--search",
        ".stx-cards-filterbar__primary",
        ".stx-cards-filterbar__divider",
    ],
)
def test_toolbar_css_declares_group_selector(selector: str) -> None:
    """Every surviving group / primary / divider class must have at least
    one CSS rule in 07-toolbar-groups.css."""
    # Arrange
    # Act
    css = _read(_TOOLBAR_CSS)
    # Assert
    assert selector in css, f"07-toolbar-groups.css missing rule for {selector!r}"


def test_toolbar_css_responsive_media_query_present_m() -> None:
    """A media query for the narrow-viewport wrap (≤780px) must be
    declared so each group drops to its own row gracefully."""
    # Arrange
    css = _read(_TOOLBAR_CSS)
    # Accept any max-width up to 800px so a future bump to 768/800 still
    # passes — the contract is "there is a responsive wrap rule", not the
    # exact pixel value.
    # Act
    m = re.search(r"@media\s*\([^)]*max-width\s*:\s*(\d+)px\)", css)
    # Assert
    assert m, "07-toolbar-groups.css missing @media (max-width: …px) rule"


def test_toolbar_css_responsive_media_query_present_case_2() -> None:
    """The responsive wrap breakpoint must stay in the operator's range."""
    # Arrange
    css = _read(_TOOLBAR_CSS)
    # Act
    m = re.search(r"@media\s*\([^)]*max-width\s*:\s*(\d+)px\)", css)
    # Assert
    px = int(m.group(1))
    assert 700 <= px <= 800, (
        f"responsive wrap breakpoint out of range: got {px}px, "
        f"expected ~780px per operator spec"
    )


def test_toolbar_css_no_hardcoded_colors() -> None:
    """Every color in the toolbar CSS must source from a `var(--…)` token.
    The only acceptable hex literals are inside `var(--token, #fallback)`
    fallback slots; bare `#fff` / `white` / raw hex assignments are
    forbidden."""
    # Arrange
    css = _read(_TOOLBAR_CSS)
    # Strip block comments first so explanatory prose ("white in dark mode")
    # doesn't trigger the guard.
    comments_stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Strip every `var(--token, #fallback)` substring so the fallback hex
    # inside a var() call doesn't get caught — that's the documented
    # token-default pattern used across the codebase.
    no_var = re.sub(r"var\([^)]*\)", "", comments_stripped, flags=re.DOTALL)
    pattern = re.compile(
        r"(?<![\w-])" r"(#[0-9a-fA-F]{3,8}\b|\bwhite\b(?!-))",
    )
    # Act
    matches = pattern.findall(no_var)
    # Assert
    assert not matches, (
        f"hardcoded colors found in 07-toolbar-groups.css "
        f"(must use var(--…) tokens): {matches[:5]}"
    )


def test_toolbar_css_balanced_braces() -> None:
    """Edits did not corrupt brace nesting."""
    # Arrange
    # Act
    css = _read(_TOOLBAR_CSS)
    # Assert
    assert css.count("{") == css.count("}"), (
        f"unbalanced braces in {_TOOLBAR_CSS}: "
        f"{css.count('{')} opens vs {css.count('}')} closes"
    )


# ============================================================================
# Half B — template / HTML uses each surviving group class
# ============================================================================


@pytest.mark.parametrize(
    "selector_class",
    [
        "stx-cards-filterbar__group--search",
        "stx-cards-filterbar__primary",
        "stx-cards-filterbar__divider",
    ],
)
def test_template_renders_group_class(selector_class: str) -> None:
    """board_v3.html must mount each surviving group / primary / divider
    class on at least one element so the CSS rules actually find targets."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert selector_class in html, (
        f"board_v3.html missing class {selector_class!r} on any element"
    )


def test_template_loads_toolbar_css() -> None:
    """The toolbar CSS must be wired into the template tree."""
    # Arrange
    # Act
    head = _read(
        _REPO_ROOT
        / "src"
        / "scitex_cards"
        / "_django"
        / "templates"
        / "scitex_cards"
        / "_board_head.html"
    )
    # Assert
    assert "07-toolbar-groups.css" in head, (
        "_board_head.html never <link>s 07-toolbar-groups.css"
    )


def _view_section_body(html: str) -> str:
    m = re.search(
        r'data-id="view"[^>]*>(.*?data-id="filters")',
        html,
        flags=re.DOTALL,
    )
    assert m, "could not locate Details > View section in template"
    return m.group(1)


def test_template_view_section_block_exists() -> None:
    """The Details > View section wrapper must be in the template
    (moved 2026-10-22, operator: filterbar VIEW group → Details)."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    body = _view_section_body(html)
    # Assert
    assert body.strip(), "Details > View section is empty"


def test_template_view_section_contains_layout() -> None:
    """Details > View holds the Layout segmented control. Sort and Group
    are NOT here any more — they were removed with the Column layout."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    view_body = _view_section_body(html)
    # Assert
    assert 'class="filt-layout"' in view_body, "Details > View missing Layout"
    assert 'id="f-layout-timeline"' in view_body
    assert 'id="f-layout-wall"' in view_body
    assert 'id="f-layout-graph"' in view_body
    assert 'id="f-layout-matrix"' in view_body


def test_template_filterbar_view_group_is_gone() -> None:
    """The filterbar VIEW group wrapper must NOT be in the template any
    more — Layout moved to Details > View (operator 2026-10-22)."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act / Assert
    assert "stx-cards-filterbar__group--view" not in html, (
        "filterbar VIEW group is back — Layout belongs in Details > View"
    )


@pytest.mark.parametrize("gone", ['class="filt-sort"', 'class="filt-groupby"'])
def test_template_view_section_dropped_sort_and_group(gone: str) -> None:
    """Sort + Group only ever ordered/clustered COLUMN cards. The Column
    layout is gone, so they are too (operator TG 2026-07-13)."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    view_body = _view_section_body(html)
    # Assert
    assert gone not in view_body, f"Details > View still carries retired control {gone!r}"


def _primary_zone_match(html: str):
    return re.search(
        r"stx-cards-filterbar__primary[^>]*>(.*?)\{#\s*end PRIMARY",
        html,
        flags=re.DOTALL,
    )


def _search_group_match(html: str):
    return re.search(
        r"stx-cards-filterbar__group--search[^>]*>(.*?)\{#\s*end " r"\.fb-center",
        html,
        flags=re.DOTALL,
    )


def test_template_primary_zone_block_exists() -> None:
    """The PRIMARY action zone wrapper must still be in the template."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = _primary_zone_match(html)
    # Assert
    assert m, "could not locate PRIMARY zone block in template"


def test_template_primary_zone_contains_add_task() -> None:
    """The PRIMARY action zone must wrap the +Add Task button so it pops
    as the right-most, brand-accent element."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = _primary_zone_match(html)
    # Assert
    assert 'id="add-task-btn"' in m.group(1), (
        "PRIMARY zone missing the +Add Task button"
    )


def test_template_search_group_block_exists() -> None:
    """The SEARCH group wrapper must still be in the template."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = _search_group_match(html)
    # Assert
    assert m, "could not locate SEARCH group block in template"


def test_template_search_group_contains_search_input() -> None:
    """The SEARCH group must wrap the search input. The Filters popover is
    no longer here — the filters moved to the right-hand Details column."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = _search_group_match(html)
    # Assert
    assert 'id="f-search"' in m.group(1), "SEARCH group missing search input"


# ============================================================================
# Behavior preservation — the controls that STAYED are still there
# ============================================================================


@pytest.mark.parametrize(
    "needle",
    [
        'id="f-search"',  # Search input
        'id="f-layout-timeline"',  # Layout: Timeline (the DEFAULT)
        'id="f-layout-wall"',  # Layout: Wall
        'id="f-layout-graph"',  # Layout: Graph
        'id="add-task-btn"',  # + Add Task
    ],
)
def test_template_preserves_every_surviving_control(needle: str) -> None:
    """The declutter removes controls on purpose; it must not take the
    survivors with it. This guards against an accidental drop."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert needle in html, f"board_v3.html lost a surviving toolbar control: {needle!r}"


# ============================================================================
# Declutter — the retired controls are GONE (the load-bearing half)
# ============================================================================


@pytest.mark.parametrize(
    "needle",
    [
        'id="f-layout-column"',  # Column layout  — removed 2026-07-13
        'id="f-layout-table"',  # Table layout   — removed 2026-07-13
        'id="f-sort"',  # Sort           — column-only
        'id="f-groupby"',  # Group          — column-only
        'id="reload"',  # Reload         — "reload は要らない"
        'id="proj-hide-wrap"',  # hide-project
        'id="t-block"',  # big blocking-me display (legend conveys it)
        'id="filt-popover-wrap"',  # filters popover → moved to Details
    ],
)
def test_template_dropped_every_retired_control(needle: str) -> None:
    """Each of these left the header on 2026-07-13. A regression that
    re-introduces one is exactly the clutter the operator asked us to
    remove, so pin their ABSENCE."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert needle not in html, (
        f"retired toolbar control is back in board_v3.html: {needle!r}"
    )


def test_status_group_is_gone_from_the_template() -> None:
    """The whole STATUS cluster (recent-count + Reload + hide-project +
    blocking-me + hidden) left the header. Its wrapper must be gone too."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert "stx-cards-filterbar__group--status" not in html, (
        "the STATUS toolbar group is back in board_v3.html"
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


def test_recent_count_pill_moved_to_details_not_deleted() -> None:
    """ "114 new/24h ですが、それも details のほうで" — the counter MOVED. It must
    still exist somewhere in the template tree."""
    # Arrange
    # Act
    html = _read(_BOARD_PANE_TEMPLATE) + _read(_BOARD_FOOT_TEMPLATE)
    # Assert
    assert 'id="recent-count-pill"' in html, "the 'N new / 24 h' counter was deleted"


def test_details_stats_panel_hosts_the_moved_counter() -> None:
    """The counter's new home: the Details > Stats panel (renderStats), not
    the filterbar."""
    # Arrange
    # Act
    html = _read(_BOARD_PANE_TEMPLATE)
    # Assert
    assert 'id="details-stats"' in html, "Details > Stats panel is missing"
