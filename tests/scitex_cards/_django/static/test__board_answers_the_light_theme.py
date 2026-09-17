#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board must ANSWER the theme, and a contrast decision needs both its sides.

THE DEFECT THESE PIN (measured on the served board 2026-09-17, reproduced
independently by scitex-cards-gui's baseline slice): with the light theme
selected, the board's CHROME kept its dark surfaces while the TEXT followed the
shell's light-theme token — the page title read rgb(51,51,51) on rgb(33,38,45)
= 1.20:1 against 12.88:1 in dark. The operator's words were "mixed dark/light
surfaces".

THE MECHANISM, because the shape of it is the lesson:

  * `board_v3/01-filterbar.css` defines the board's OWN palette in `:root`
    (--bg, --card-bg, --col-bg, --text, --text-muted, --border), all dark
    literals, and NO board_v3 stylesheet had a `[data-theme="light"]` rule at
    all — so `data-theme` reached the shell and stopped there.
  * `page-header.css` then forced the header's FOREGROUND to the shell's token
    (`.stx-cards-headerbar.filterbar { --stx-cards-header-fg: var(--text-primary) }`),
    which is the fix for an earlier bug of the same family ("a literal white
    here was invisible on the light theme"). It fixed the foreground and left
    the surface, so the light theme got dark-on-dark. A contrast decision has
    TWO sides; taking one from the shell and the other from the page guarantees
    they can disagree.

So the guards below are deliberately not "is the colour pretty": they check
that the board has ONE place that answers the theme, and that no rule can split
a foreground from its surface again.
"""

from __future__ import annotations

import re
from pathlib import Path

from scitex_cards._django import views

_STATIC = Path(views.__file__).parent / "static" / "scitex_cards"
_FILTERBAR = _STATIC / "board_v3" / "01-filterbar.css"
_PAGE_HEADER = _STATIC / "page-header.css"

#: The structural half of the board palette — the variables whose job is
#: surface/text/border. They are exactly the ones a theme switch must move.
_STRUCTURAL = ("--bg", "--card-bg", "--col-bg", "--text", "--text-muted", "--border")

#: Semantic colours. The operator ruled these stay: "blocked" must read the same
#: in either theme, so a light block that redefines them is a defect, not a
#: theme.
_SEMANTIC = ("--green", "--amber", "--red", "--gold", "--grey", "--purple")


def _without_comments(text: str) -> str:
    """Drop `/* … */` blocks before scanning.

    A guard that flags the DOCUMENTATION of a rule as a violation of it is the
    same vacuous shape as one that reads its own docstring — and here it is not
    hypothetical: the comment explaining why the shell-token override was
    removed QUOTES that rule verbatim, which is exactly the text the check
    looks for. (Same lesson, same remedy, as the chat-palette guard.)
    """
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)


def _light_block(text: str) -> str:
    """The `[data-theme="light"]` rule bodies, concatenated."""
    return "\n".join(re.findall(r'\[data-theme="light"\][^{]*\{([^}]*)\}', _without_comments(text)))


def test_the_board_palette_answers_the_light_theme():
    """One place must flip the board's surfaces, or the chrome stays dark."""
    # Arrange
    css = _FILTERBAR.read_text(encoding="utf-8")
    # Act
    missing = [name for name in _STRUCTURAL if f"{name}:" not in _light_block(css)]
    # Assert
    assert not missing, (
        f"the board palette declares {missing} in :root but not under "
        '[data-theme="light"] — a theme the board does not answer is how the '
        "operator got dark surfaces under light-theme text (1.20:1 measured)"
    )


def test_the_semantic_colours_are_not_redefined_by_the_theme():
    """Status colours are semantic: blocked must read the same in both themes."""
    # Arrange
    css = _FILTERBAR.read_text(encoding="utf-8")
    # Act
    redefined = [name for name in _SEMANTIC if f"{name}:" in _light_block(css)]
    # Assert
    assert not redefined, (
        f"the light block redefines the semantic colours {redefined}; the "
        "operator's ruling is that status colours stay as they are — flip the "
        "surfaces, not the meanings"
    )


def test_no_rule_takes_the_header_foreground_from_the_shell():
    """A foreground from the shell over a page surface is the hybrid surface."""
    # Arrange
    css = _without_comments(_PAGE_HEADER.read_text(encoding="utf-8"))
    # Act
    offenders = re.findall(r"--stx-cards-header-fg:\s*var\(--text-primary\)", css)
    # Assert
    assert not offenders, (
        "page-header.css forces the header foreground to the shell's token "
        "while the bar's SURFACE comes from the page palette — the exact rule "
        "that produced rgb(51,51,51) on rgb(33,38,45). The page must own both "
        "sides; `color: var(--stx-cards-header-fg, var(--text-primary))` is the "
        "fallback that covers a page declaring neither"
    )


# EOF
