#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The active-filter chip wears the brand CTA fill, never purple.

THE ELEMENT (element-inspector file 2026-10-24-163844, board page):
`span.filt-chip` — "project: ywatanabe/dotfiles ×" — computed
`backgroundColor: rgb(109, 76, 173)` (white on purple). Operator 2026-10-24:
that purple reads as foreign chrome. The fix is the same pairing the Send
rule and the DWM --on state already use: `--stx-brand` flips navy/gold
across themes while the text token stays readable on both — no new color,
no per-theme literals here.

The × dismiss button rides `currentColor` for the same reason: a
fixed-white × would wash out on the gold dark-mode fill.

Text-level pins (the geometric proof is a live computed-style read in the
card's evidence); no browser needed here.
"""

from __future__ import annotations

import re
from pathlib import Path

from scitex_cards._django import views

_SHEET = (
    Path(views.__file__).resolve().parent
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "04-collapse-and-groups.css"
)


def _without_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)


def _rule_body(selector: str) -> str:
    """The declaration body for `selector` in the sheet."""
    css = _without_comments(_SHEET.read_text(encoding="utf-8"))
    match = re.search(re.escape(selector) + r" \{(.*?)\}", css, flags=re.DOTALL)
    return match.group(1) if match else ""


def test_the_filter_chip_uses_no_purple() -> None:
    """The inspector-caught defect: white-on-purple active-filter chip."""
    # Arrange
    body = _rule_body(".filt-chip")
    # Act
    clean = "purple" not in body and "#7c5cbf" not in body and "#8e6cff" not in body
    # Assert
    assert clean, f".filt-chip must not reference purple ({body.strip()[:160]!r})"


def test_the_filter_chip_shares_the_brand_cta_pairing() -> None:
    """Same tokens as the Send rule: brand fill, readable text, both themes."""
    # Arrange
    body = _rule_body(".filt-chip")
    # Act
    paired = "var(--stx-brand" in body and "var(--bg" in body
    # Assert
    assert paired, f".filt-chip must pair --stx-brand with --bg ({body.strip()[:160]!r})"


def test_the_chip_dismiss_button_follows_the_chip_text() -> None:
    """A fixed-white × would wash out on the gold dark-mode fill."""
    # Arrange
    body = _rule_body(".filt-chip-clear")
    # Act
    follows = "currentColor" in body or "color: inherit" in body
    fixed_white = "color: #fff" in body
    # Assert
    assert follows and not fixed_white


_SEARCH_SHEET = (
    Path(views.__file__).resolve().parent
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "02-card.css"
)


def _search_rule_body(selector: str) -> str:
    """The declaration body for `selector` in the search-input sheet."""
    css = _without_comments(_SEARCH_SHEET.read_text(encoding="utf-8"))
    match = re.search(re.escape(selector) + r" \{(.*?)\}", css, flags=re.DOTALL)
    return match.group(1) if match else ""


def test_the_search_input_uses_no_purple_wash() -> None:
    """The header search wore the same rgb(109,76,173) wash in every state
    (idle, hover, focus border) — the live screenshot flags it beside the
    chip. It now reads the theme-aware input fill like .filt."""
    # Arrange
    idle = _search_rule_body(".filt-search")
    hover = _search_rule_body(".filt-search:hover")
    focus = _search_rule_body(".filt-search:focus")
    # Act
    clean = all(
        "109, 76, 173" not in body and "purple" not in body
        for body in (idle, hover, focus)
    )
    # Assert
    assert clean, (
        f".filt-search states must not reference purple "
        f"(idle={idle.strip()[:100]!r} hover={hover.strip()[:80]!r} "
        f"focus={focus.strip()[:100]!r})"
    )


def test_the_search_input_matches_the_filt_fill() -> None:
    """Same fill token as the sibling filter controls, both themes."""
    # Arrange
    body = _search_rule_body(".filt-search")
    # Act
    themed = "var(--card-bg" in body
    # Assert
    assert themed


def test_the_filter_chip_uses_no_bold() -> None:
    """Operator 2026-10-24: the chip reads as a label, not a call — no
    600+ weights anywhere in its rules."""
    # Arrange
    selectors = (
        ".filt-chip",
        ".filt-chip-key",
        ".filt-chip-val",
        ".filt-chip-clear",
    )
    # Act
    bodies = " ".join(_rule_body(selector) for selector in selectors)
    # Assert
    assert "font-weight: 600" not in bodies and "font-weight: 700" not in bodies


# EOF
