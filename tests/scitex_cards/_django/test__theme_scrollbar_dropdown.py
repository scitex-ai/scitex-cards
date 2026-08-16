"""CSS-contract tests for the themed scrollbar + <select> fix.

Operator complaint via lead a2a `510a58d4` (2026-06-14): on the scitex-cards
board at http://127.0.0.1:8051 the SCROLLBAR and DROPDOWNS / SELECTS
rendered WHITE in dark mode. The fix lives in two CSS surfaces:

  * React board:   src/scitex_cards/_django/frontend/src/styles/board.css
  * Vanilla board: src/scitex_cards/_django/static/scitex_cards/board_v3/
                   00-theme-scrollbar-select.css (NEW, loaded first)

This module pins the LOAD-BEARING CSS CONTRACT — visual verification is
the operator's job; what we assert here is that the rules and selectors
that make the theming WORK are present and use `var(--…)` tokens (no
hardcoded `#fff` / `white`). Mocks-free: we just open the source CSS
files and grep them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# --- repo paths -----------------------------------------------------------
# Compute relative to THIS test file so the suite is portable (works inside
# worktrees, monorepo subdirs, CI checkouts). The tests live four levels
# below the repo root: tests/scitex_cards/_django/<this>.
_REPO_ROOT = Path(__file__).resolve().parents[3]

_BOARD_REACT_CSS = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "frontend"
    / "src"
    / "styles"
    / "board.css"
)
_BOARD_V3_THEME_CSS = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "00-theme-scrollbar-select.css"
)
_BOARD_V3_TEMPLATE = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "board_v3.html"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected CSS surface missing: {path}"
    return path.read_text(encoding="utf-8")


# ============================================================================
# Half A — themed scrollbar
# ============================================================================


def test_react_board_declares_scrollbar_color() -> None:
    """React board must declare a `scrollbar-color` rule."""
    # Arrange
    css = _read(_BOARD_REACT_CSS)
    # Act
    matches = re.findall(r"scrollbar-color\s*:\s*[^;]+;", css)
    # Assert
    assert matches, "board.css missing scrollbar-color declaration"


def test_react_board_scrollbar_color_uses_token() -> None:
    """The `scrollbar-color` value must reference a CSS custom property,
    not a hardcoded literal."""
    # Arrange
    css = _read(_BOARD_REACT_CSS)
    # Act
    matches = re.findall(r"scrollbar-color\s*:\s*[^;]+;", css)
    # Assert
    assert all("var(--" in decl for decl in matches)


def test_react_board_webkit_scrollbar_present_css_contains() -> None:
    """React board must register ::-webkit-scrollbar pseudo-element rules."""
    # Arrange
    # Act
    css = _read(_BOARD_REACT_CSS)
    # Assert
    assert "::-webkit-scrollbar" in css, "board.css missing ::-webkit-scrollbar"


def test_react_board_webkit_scrollbar_present_css_contains_2() -> None:
    """React board must register ::-webkit-scrollbar pseudo-element rules."""
    # Arrange
    # Act
    css = _read(_BOARD_REACT_CSS)
    # Assert
    assert (
        "::-webkit-scrollbar-thumb" in css
    ), "board.css missing ::-webkit-scrollbar-thumb"


def test_react_board_webkit_scrollbar_present_css_contains_3() -> None:
    """React board must register ::-webkit-scrollbar pseudo-element rules."""
    # Arrange
    # Act
    css = _read(_BOARD_REACT_CSS)
    # Assert
    assert (
        "::-webkit-scrollbar-thumb:hover" in css
    ), "board.css missing ::-webkit-scrollbar-thumb:hover"


def test_react_board_webkit_scrollbar_present_css_contains_4() -> None:
    """React board must register ::-webkit-scrollbar pseudo-element rules."""
    # Arrange
    # Act
    css = _read(_BOARD_REACT_CSS)
    # Assert
    assert (
        "::-webkit-scrollbar-track" in css
    ), "board.css missing ::-webkit-scrollbar-track"


def test_react_board_global_scrollbar_fallback() -> None:
    """A GLOBAL fallback rule must target `.stx-todo-board, .stx-todo-board *`
    so any scrollable element inherits themed chrome — pre-empts elements
    we'd otherwise miss (lead a2a 510a58d4 KEY INSIGHT)."""
    # Arrange
    # Act
    css = _read(_BOARD_REACT_CSS)
    # Assert
    assert ".stx-todo-board," in css and ".stx-todo-board *" in css, (
        "board.css missing global `.stx-todo-board, .stx-todo-board *` "
        "scrollbar fallback"
    )


def test_board_v3_global_scrollbar_file_present_is_file() -> None:
    """The board_v3 global theming file must exist and be load-bearing."""
    # Arrange
    # Act
    # Assert
    css = _read(_BOARD_V3_THEME_CSS)
    # Modern scrollbar properties
    # WebKit pseudo-elements
    assert (
        _BOARD_V3_THEME_CSS.is_file()
    ), f"new global theming file missing: {_BOARD_V3_THEME_CSS}"


def test_board_v3_global_scrollbar_file_present_css_contains() -> None:
    """The board_v3 global theming file must exist and be load-bearing."""
    # Arrange
    # Act
    # Assert
    css = _read(_BOARD_V3_THEME_CSS)
    # Modern scrollbar properties
    # WebKit pseudo-elements
    assert "scrollbar-width" in css, "missing scrollbar-width"


def test_board_v3_global_scrollbar_file_present_css_contains_2() -> None:
    """The board_v3 global theming file must exist and be load-bearing."""
    # Arrange
    # Act
    # Assert
    css = _read(_BOARD_V3_THEME_CSS)
    # Modern scrollbar properties
    # WebKit pseudo-elements
    assert "scrollbar-color" in css, "missing scrollbar-color"


def test_board_v3_global_scrollbar_file_present_css_contains_3() -> None:
    """The board_v3 global theming file must exist and be load-bearing."""
    # Arrange
    # Act
    # Assert
    css = _read(_BOARD_V3_THEME_CSS)
    # Modern scrollbar properties
    # WebKit pseudo-elements
    assert "::-webkit-scrollbar" in css


def test_board_v3_global_scrollbar_file_present_css_contains_4() -> None:
    """The board_v3 global theming file must exist and be load-bearing."""
    # Arrange
    # Act
    # Assert
    css = _read(_BOARD_V3_THEME_CSS)
    # Modern scrollbar properties
    # WebKit pseudo-elements
    assert "::-webkit-scrollbar-thumb" in css


def test_board_v3_global_scrollbar_file_present_css_contains_5() -> None:
    """The board_v3 global theming file must exist and be load-bearing."""
    # Arrange
    # Act
    # Assert
    css = _read(_BOARD_V3_THEME_CSS)
    # Modern scrollbar properties
    # WebKit pseudo-elements
    assert "::-webkit-scrollbar-thumb:hover" in css


def test_board_v3_global_scrollbar_file_present_css_contains_6() -> None:
    """The board_v3 global theming file must exist and be load-bearing."""
    # Arrange
    # Act
    # Assert
    css = _read(_BOARD_V3_THEME_CSS)
    # Modern scrollbar properties
    # WebKit pseudo-elements
    assert "::-webkit-scrollbar-track" in css


def test_board_v3_template_loads_global_theme_first_theme_idx() -> None:
    """The template must <link> 00-theme-scrollbar-select.css BEFORE
    01-filterbar.css so its rules form the global fallback, and per-file
    overrides win on specificity."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    theme_idx = html.find("00-theme-scrollbar-select.css")
    # Act
    filterbar_idx = html.find("01-filterbar.css")
    # Assert
    assert theme_idx != -1, "template never links 00-theme-scrollbar-select.css"


def test_board_v3_template_loads_global_theme_first_filterbar_idx() -> None:
    """The template must <link> 00-theme-scrollbar-select.css BEFORE
    01-filterbar.css so its rules form the global fallback, and per-file
    overrides win on specificity."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    theme_idx = html.find("00-theme-scrollbar-select.css")
    # Act
    filterbar_idx = html.find("01-filterbar.css")
    # Assert
    assert filterbar_idx != -1, "template never links 01-filterbar.css"


def test_board_v3_template_loads_global_theme_first_theme_idx_2() -> None:
    """The template must <link> 00-theme-scrollbar-select.css BEFORE
    01-filterbar.css so its rules form the global fallback, and per-file
    overrides win on specificity."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    theme_idx = html.find("00-theme-scrollbar-select.css")
    # Act
    filterbar_idx = html.find("01-filterbar.css")
    # Assert
    assert (
        theme_idx < filterbar_idx
    ), "00-theme-scrollbar-select.css must be linked BEFORE 01-filterbar.css"


# ============================================================================
# Half B — themed <select> / dropdowns
# ============================================================================


def test_react_board_select_rule_present_css_contains() -> None:
    """The React board must style `.stx-todo-board select` with token-bound
    background + color so vanilla dropdowns stop falling through to OS
    white."""
    # Arrange
    # Act
    css = _read(_BOARD_REACT_CSS)
    # Assert
    # Pull the rule block out and verify it binds to scitex-ui tokens.
    m = re.search(r"\.stx-todo-board select\s*\{([^}]*)\}", css, flags=re.DOTALL)
    block = m.group(1)
    assert (
        ".stx-todo-board select" in css
    ), "board.css missing `.stx-todo-board select` rule"


def test_react_board_select_rule_present_m() -> None:
    """The React board must style `.stx-todo-board select` with token-bound
    background + color so vanilla dropdowns stop falling through to OS
    white."""
    # Arrange
    # Act
    css = _read(_BOARD_REACT_CSS)
    # Assert
    # Pull the rule block out and verify it binds to scitex-ui tokens.
    m = re.search(r"\.stx-todo-board select\s*\{([^}]*)\}", css, flags=re.DOTALL)
    block = m.group(1)
    assert m is not None, "could not extract `.stx-todo-board select` block"


def test_react_board_select_rule_present_case_3() -> None:
    """The React board must style `.stx-todo-board select` with token-bound
    background + color so vanilla dropdowns stop falling through to OS
    white."""
    # Arrange
    # Act
    css = _read(_BOARD_REACT_CSS)
    # Assert
    # Pull the rule block out and verify it binds to scitex-ui tokens.
    m = re.search(r"\.stx-todo-board select\s*\{([^}]*)\}", css, flags=re.DOTALL)
    block = m.group(1)
    assert (
        "background:" in block and "var(--" in block
    ), f"select background must use a var(--…) token, got block: {block!r}"


def test_react_board_select_rule_present_case_4() -> None:
    """The React board must style `.stx-todo-board select` with token-bound
    background + color so vanilla dropdowns stop falling through to OS
    white."""
    # Arrange
    # Act
    css = _read(_BOARD_REACT_CSS)
    # Assert
    # Pull the rule block out and verify it binds to scitex-ui tokens.
    m = re.search(r"\.stx-todo-board select\s*\{([^}]*)\}", css, flags=re.DOTALL)
    block = m.group(1)
    assert (
        "color:" in block and "var(--" in block
    ), f"select color must use a var(--…) token, got block: {block!r}"


def test_react_board_select_focus_state() -> None:
    """Select must register a `:focus`/`:focus-visible` rule using the
    accent token so the active filter pops in either theme."""
    # Arrange
    # Act
    css = _read(_BOARD_REACT_CSS)
    # Assert
    assert (
        ".stx-todo-board select:focus" in css
        or ".stx-todo-board select:focus-visible" in css
    ), "board.css missing select focus state"


def test_board_v3_select_rule_present_css_contains() -> None:
    """Vanilla board_v3 surface must style `body select` so every <select>
    in the filterbar (status, project, agent, sort) gets dark chrome."""
    # Arrange
    # Act
    css = _read(_BOARD_V3_THEME_CSS)
    # Assert
    # Token-bound background + color
    m = re.search(r"body select\s*\{([^}]*)\}", css, flags=re.DOTALL)
    block = m.group(1)
    assert "body select" in css, "00-theme-scrollbar-select.css missing select rule"


def test_board_v3_select_rule_present_m() -> None:
    """Vanilla board_v3 surface must style `body select` so every <select>
    in the filterbar (status, project, agent, sort) gets dark chrome."""
    # Arrange
    # Act
    css = _read(_BOARD_V3_THEME_CSS)
    # Assert
    # Token-bound background + color
    m = re.search(r"body select\s*\{([^}]*)\}", css, flags=re.DOTALL)
    block = m.group(1)
    assert m, "could not extract `body select` block"


def test_board_v3_select_rule_present_block_contains() -> None:
    """Vanilla board_v3 surface must style `body select` so every <select>
    in the filterbar (status, project, agent, sort) gets dark chrome."""
    # Arrange
    # Act
    css = _read(_BOARD_V3_THEME_CSS)
    # Assert
    # Token-bound background + color
    m = re.search(r"body select\s*\{([^}]*)\}", css, flags=re.DOTALL)
    block = m.group(1)
    assert (
        "var(--" in block
    ), f"body select rule must use var(--…) tokens, got: {block!r}"


def test_board_v3_option_rule_present() -> None:
    """Best-effort <option> styling so the popover lines up with the
    closed state in Chromium / Firefox."""
    # Arrange
    # Act
    css = _read(_BOARD_V3_THEME_CSS)
    # Assert
    assert "body select option" in css, "missing `body select option` rule"


# ============================================================================
# Contract guard — no hardcoded #fff / white in the scrollbar/dropdown rules
# ============================================================================


_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", flags=re.DOTALL)


def _scrollbar_or_select_rules(css: str) -> list[str]:
    """Extract all CSS rule blocks whose selector matches scrollbar /
    `select` element / `option` element, so the hardcoded-color guard
    only looks at the surfaces this PR is responsible for. Avoids false
    positives on unrelated existing rules (e.g. badges, `.x--selected`
    multi-select modifiers, comments mentioning the word "selector")."""
    # Strip comments first so a sentence like "absolute selector …" in a
    # block comment doesn't bleed into the regex's selector capture.
    css_nc = _CSS_COMMENT_RE.sub("", css)
    rules: list[str] = []
    for m in re.finditer(r"([^{}]+)\{([^{}]+)\}", css_nc, flags=re.DOTALL):
        selector = m.group(1).strip().lower()
        body = m.group(2)
        # Tokenize: split on commas + whitespace so we evaluate each
        # individual selector, then check whether ANY of them targets a
        # scrollbar pseudo-element, the `select` element, or the `option`
        # element. Use a regex that matches `select` / `option` only when
        # they appear as a bare element (preceded by start, whitespace,
        # comma, or `>` / `+` / `~` combinator).
        is_select_or_option_rule = bool(
            re.search(r"(?:^|[\s>+~,])(select|option)\b", selector)
        )
        is_scrollbar_rule = "scrollbar" in selector
        if is_scrollbar_rule or is_select_or_option_rule:
            rules.append(body)
    return rules


@pytest.mark.parametrize(
    "path",
    [
        _BOARD_REACT_CSS,
        _BOARD_V3_THEME_CSS,
    ],
)
def test_scrollbar_or_select_rules_are_extracted(path: Path) -> None:
    """At least one scrollbar/select rule body is found to scan."""
    # Arrange
    # Act
    rules = _scrollbar_or_select_rules(_read(path))
    # Assert
    assert rules, f"no scrollbar/select rules extracted from {path}"


@pytest.mark.parametrize(
    "path",
    [
        _BOARD_REACT_CSS,
        _BOARD_V3_THEME_CSS,
    ],
)
def test_no_hardcoded_white_in_scrollbar_or_select_rules(path: Path) -> None:
    """No `#fff` / `#ffffff` / bare `white` keyword in scrollbar / select /
    option rule bodies — every color must be a `var(--…)` token (with a
    documented dark-mode hex fallback inside the var() call only)."""
    # Arrange
    rules = _scrollbar_or_select_rules(_read(path))
    pattern = re.compile(
        r"(?<!var\(--)"  # not inside a var(--…, …) fallback slot
        r"(?<![\w-])"
        r"(#fff(?:fff)?\b|white\b)",
        flags=re.IGNORECASE,
    )
    # Act
    # `var(--x, #fff)` is acceptable (documented dark-mode default); strip
    # every `var(--…, …)` substring before matching so only raw
    # assignments are flagged.
    raw_hits = [
        pattern.search(re.sub(r"var\(--[^)]*\)", "", body, flags=re.DOTALL))
        for body in rules
    ]
    # Assert
    assert not any(raw_hits)


# ============================================================================
# Sanity — both files still parse as balanced-brace CSS
# ============================================================================


@pytest.mark.parametrize("path", [_BOARD_REACT_CSS, _BOARD_V3_THEME_CSS])
def test_css_braces_are_balanced(path: Path) -> None:
    """Edits did not corrupt brace nesting."""
    # Arrange
    # Act
    css = _read(path)
    # Assert
    assert css.count("{") == css.count("}"), (
        f"unbalanced braces in {path}: "
        f"{css.count('{')} opens vs {css.count('}')} closes"
    )
