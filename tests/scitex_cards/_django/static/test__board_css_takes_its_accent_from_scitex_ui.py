#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The SPA must take its accent from scitex-ui, never from its own hex literals.

OPERATOR REPORT (Hub mount, 2026-09-16/17): "mixed dark/light surfaces and
hardcoded accents". The hardcoded accents were measurable in this stylesheet:
the brand violet appeared as literal hex in ~25 rules (#6d4cad for text and
borders, #9b7fd6 for borders, #a78bfa in the dark token), plus three
accent-filled chips whose label was a bare `#fff`.

WHY THE LITERALS MATTER AND NOT JUST STYLE. board.css does map its `--stx-*`
variables onto scitex-ui's semantic variables — but only in ONE block, and the
rules below it then re-picked the same violet by hand. Two consequences, both
observed before in this repo:

  * the page cannot follow the shell's theme. In the LIGHT theme
    `--stx-accent` is overridden to the deeper violet (see the
    `[data-theme="light"]` block); a rule that says `#6d4cad` keeps the DARK
    value and paints a colour the theme never chose — the "mixed surfaces" the
    operator saw.
  * `color: #fff` on an accent fill is a CONTRAST decision taken against one
    theme only. On the dark theme's lavender (#a78bfa) white text is ~2:1 —
    below every legibility floor — while the theme's own "on-accent" colour is
    the readable one. The chat page paid for this same mistake when a private
    palette starved a scitex-ui component and its menu items rendered as
    near-invisible grey (see test__chat_page_uses_scitex_ui_palette.py).

WHAT IS DELIBERATELY NOT INCLUDED: the STATUS colours (#c0392b blocked,
#ffc107 awaiting-operator, #222 text on a JS-supplied status fill, recent.css's
warning/error oranges) are semantic, not brand, and the operator asked for them
to stay. This guard therefore pins the ACCENT family only — a rule that also
banned the status hexes would force the next author to weaken it.
"""

from __future__ import annotations

import re
from pathlib import Path

from scitex_cards._django import views

_STYLES = Path(views.__file__).parent / "frontend" / "src" / "styles"
_BOARD = _STYLES / "board.css"

#: Every literal that IS the brand accent, in its dark and light shade plus the
#: two the stylesheet used to spell by hand.
_ACCENT_LITERALS = ("#6d4cad", "#9b7fd6", "#a78bfa", "#6a4fb0")

#: The ONE place a hex is correct: the token block that defines the fallback
#: palette used when the shell stylesheet is absent (a bare SPA mount). A rule
#: that banned these would break the page outside the shell.
_TOKEN_DEFINITION = re.compile(r"^\s*--[a-z0-9-]+:\s*#[0-9a-fA-F]{3,6}\s*;")

#: `var(--token, #fallback)` — the SECOND sanctioned place for a hex, and for
#: the same reason as the token block: a bare SPA mount has no shell stylesheet
#: and the fallback is what keeps the page readable there. The declaration
#: still consumes the token, so it follows the theme whenever one is present,
#: which is precisely what a bare `#6d4cad` does not do.
_VAR_WITH_FALLBACK = re.compile(r"var\([^()]*\)")


def _accent_literals_outside_token_definitions() -> list[tuple[int, str]]:
    found = []
    for lineno, line in enumerate(_BOARD.read_text(encoding="utf-8").splitlines(), 1):
        if _TOKEN_DEFINITION.match(line):
            continue
        outside_fallbacks = _VAR_WITH_FALLBACK.sub("var(...)", line)
        if any(literal in outside_fallbacks.lower() for literal in _ACCENT_LITERALS):
            found.append((lineno, line.strip()))
    return found


def test_no_accent_hex_literal_outside_the_token_block():
    """The theme owns the accent; a rule must not re-pick it by hand."""
    # Arrange
    scan = _accent_literals_outside_token_definitions
    # Act
    offenders = scan()
    # Assert
    assert not offenders, (
        "board.css spells the brand accent by hand outside its token block "
        f"({offenders[:5]}) — such a rule cannot follow the shell's light "
        "theme and is what produced the mixed surfaces in the operator's report"
    )


def test_accent_filled_chips_use_the_themes_on_accent_colour():
    """`color: #fff` on an accent fill is contrast taken against one theme only."""
    # Arrange
    css = _BOARD.read_text(encoding="utf-8")
    # Act
    white_on_accent = re.findall(
        r"background:\s*var\(--stx-accent[^;]*\);\s*color:\s*(#[0-9a-fA-F]{3,6})",
        css,
    )
    # Assert
    assert not white_on_accent, (
        f"{white_on_accent} is a hardcoded label colour on an accent fill; use "
        "--stx-accent-on, which the theme keeps readable in BOTH themes"
    )


def test_the_accent_on_token_is_actually_used():
    """A token nobody consumes is a definition, not a fix."""
    # Arrange
    css = _BOARD.read_text(encoding="utf-8")
    # Act
    uses = css.count("var(--stx-accent-on)")
    # Assert
    assert uses >= 3, (
        f"only {uses} rule(s) use var(--stx-accent-on): the accent-filled "
        "chips are back to a hardcoded label colour"
    )


# EOF
