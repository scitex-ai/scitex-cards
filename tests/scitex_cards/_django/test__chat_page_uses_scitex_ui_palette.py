#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The chat page must take every colour from scitex-ui, never pick its own.

OPERATOR DIRECTIVE 2026-07-28, verbatim: "最適 ui を常に使ってください 勝手に変な色を
使ったりするのでおかしなことになる … scitex の世界観も壊れるので s-ui を使ってほしい".

THE BUG THAT PROVED THEM RIGHT, the same day: this page hard-coded its own
fixed-dark palette (#1e1e2e, #7c5cbf, …) and defined none of scitex-ui's tokens.
When scitex-ui 0.12.0 landed, its context-menu CSS read --text-primary /
--text-secondary, found neither, and fell back to the LIGHT-theme values —
rendering "Reply" and "Copy text" as near-invisible grey on a dark menu. The
items were live; they merely looked disabled, which is worse than broken
because nobody files a bug against something that looks intentional.

The lesson generalises past colour: a private palette does not just diverge in
style, it silently STARVES every base component the page later adopts, and the
damage surfaces in the COMPONENT rather than at the definition site. That is
why this is a test and not a comment — the rule has to be mechanical, because
the failure appears somewhere other than where the mistake is made.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import RequestFactory

from scitex_cards._django import views

#: Six-or-three-digit hex literals. `rgba(...)` is allowed: it is used for
#: shadows and scrims, which are opacity effects rather than palette choices.
#:
#: NOT preceded by `&` — an HTML numeric entity like `&#128206;` (the paperclip)
#: is a CHARACTER, not a colour, and a naive `#[0-9a-f]{6}` matches it. Caught
#: by this guard's own first run, which is the point of running it before
#: trusting it.
_HEX = re.compile(r"(?<![&\w])#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")

#: Multi-line `/* … */` blocks. The palette comment in chat.html deliberately
#: QUOTES the old hard-coded colours to explain why they are banned, so a
#: line-prefix skip is not enough — the continuation lines look like code.
#: A guard that flags the documentation of a rule as a violation of it is the
#: same vacuous shape as one that reads its own docstring, just inverted.
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

_TEMPLATE = Path(views.__file__).parent / "templates" / "scitex_cards" / "chat.html"


def _css_lines(text: str) -> list[str]:
    """Declaration lines only — comments explain the ban and may quote colours.

    A naive scan would match the very comment that documents the old palette,
    which is the vacuous-guard shape: a check reading what we wrote ABOUT the
    code instead of the code.
    """
    text = _BLOCK_COMMENT.sub(" ", text)
    out = []
    in_django_comment = False
    for line in text.splitlines():
        stripped = line.strip()
        if "{% comment %}" in stripped:
            in_django_comment = True
        if "{% endcomment %}" in stripped:
            in_django_comment = False
            continue
        if in_django_comment or stripped.startswith("//"):
            continue
        out.append(line)
    return out


@pytest.fixture
def rendered() -> str:
    """The chat page as actually served at a root mount."""
    return views.chat_page(RequestFactory().get("/chat")).content.decode("utf-8")


def test_the_page_declares_no_hex_colour_of_its_own():
    # Arrange
    source = _TEMPLATE.read_text(encoding="utf-8")

    # Act
    offenders = [ln.strip() for ln in _css_lines(source) if _HEX.search(ln)]

    # Assert - every colour must come through a scitex-ui token.
    assert offenders == []


def test_the_dark_theme_is_activated(rendered):
    """Without the attribute, base tokens resolve LIGHT onto a dark surface.

    theme.css defines its tokens twice, under [data-theme="light"] and
    [data-theme="dark"], and defaults to light when the attribute is absent.
    That default is what made the context menu unreadable.
    """
    # Arrange
    marker = 'data-theme="dark"'

    # Act
    present = marker in rendered

    # Assert
    assert present


def test_the_local_names_alias_base_tokens(rendered):
    """`--text` must be an alias onto base, not a colour of our own."""
    # Arrange
    alias = "--text: var(--text-primary)"

    # Act
    present = alias in rendered

    # Assert
    assert present


def test_the_scitex_ui_theme_stylesheet_is_linked(rendered):
    """The aliases resolve to nothing if base's theme is not loaded."""
    # Arrange
    needle = "shell/theme.css"

    # Act
    present = needle in rendered

    # Assert
    assert present


# EOF
