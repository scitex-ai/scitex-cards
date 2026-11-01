#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The composer sits flush at the bottom — no dead strip under the row.

Operator 2026-10-26: remove the space below the div holding +, message and
send. Measured live: `#compose`'s own 6px bottom padding was the whole gap
(the form already reached the viewport). The base bottom padding is now 0;
ONLY the OS home-indicator inset remains, still gated behind `@supports`
so notched phones keep their clearance and desktops get none.

Source pins over the served template (the gap is a stylesheet fact, and a
screenshot is taken at fix time, not on every run).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_cards._django import views  # noqa: E402

_CHAT_HTML = (
    Path(views.__file__).resolve().parent
    / "templates"
    / "scitex_cards"
    / "chat.html"
)


def _css() -> str:
    assert _CHAT_HTML.is_file(), f"chat.html missing: {_CHAT_HTML}"
    html = _CHAT_HTML.read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", " ", html, flags=re.DOTALL)


def _compose_rule(css: str) -> str:
    match = re.search(r"#compose \{(.*?)\}", css, flags=re.DOTALL)
    assert match, "#compose rule missing"
    return match.group(1)


def test_the_composer_has_no_base_bottom_padding() -> None:
    """The 6px dead strip under the row is gone."""
    # Arrange
    body = _compose_rule(_css())
    # Act
    m = re.search(r"padding\s*:\s*([^;]+);", body)
    # Assert — one compound assertion (PA-307: single assert per test).
    assert m and m.group(1).strip() == "6px 12px 0"


def test_the_safe_area_inset_survives_for_notched_phones() -> None:
    """The OS inset is the only bottom padding a phone may add."""
    # Arrange
    css = _css()
    # Act
    gated = (
        "@supports (padding-bottom: env(safe-area-inset-bottom))" in css
        and "#compose { padding-bottom: env(safe-area-inset-bottom); }" in css
    )
    # Assert
    assert gated


# EOF
