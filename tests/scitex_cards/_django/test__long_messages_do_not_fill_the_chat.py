#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A long DM body must not fill the screen — and must not lose a byte doing it.

OPERATOR, 2026-07-28, verbatim: 「長い文章はテキストファイルに変換してアップロード・
ダウンロードできるように、画面を埋め尽くさないように」 — a long body should be
available as a text file, and must not fill the screen. Their own thread is the
evidence: one pasted element-inspector dump ran to dozens of lines, took the
whole viewport, and pushed the actual conversation out of it.

TWO PROPERTIES, and the second is the one that is easy to lose. The obvious fix
— slice the string and show the head — is fewer lines of code and DESTROYS the
tail: find-in-page stops seeing it, select-all stops copying it, and the page
has quietly become lossier than an append-only store. So the clamp is a CSS
``max-height`` over the FULL text, and the tests below pin both halves: that
the height is bounded, and that nothing is cut to bound it.

The numeric thresholds themselves are exercised under node in
``tests/…/static/scitex_cards/chat/test_chat_longtext.py``; this file pins the
wiring on the SERVED page and the properties that only the source can show.
There is no browser in CI, so a test that pretended to observe the rendering
would be worse than none.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402

_CHAT_DIR = Path(views.__file__).parent / "static" / "scitex_cards" / "chat"
_MODULE = _CHAT_DIR / "chat_longtext.js"


def _strip_comments(source: str) -> str:
    """Drop ``/* … */`` and ``// …``.

    The module documents the destructive alternative it rejected, by name.
    Prose about a hazard must not be read as the hazard.
    """
    without_block = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", without_block)


@pytest.fixture
def page() -> str:
    """The chat page exactly as a browser at the root mount receives it."""
    return views.chat_page(RequestFactory().get("/chat")).content.decode("utf-8")


@pytest.fixture
def code() -> str:
    """The long-text module's declarations, prose removed."""
    return _strip_comments(_MODULE.read_text(encoding="utf-8"))


def test_the_page_loads_the_long_text_module(page: str):
    """chat.js builds every bubble through it, so a missing script tag is not
    a degraded page — it is a thread that renders no message bodies at all."""
    # Arrange
    needle = "scitex_cards/chat/chat_longtext.js"

    # Act
    present = needle in page

    # Assert
    assert present


def test_the_module_is_loaded_before_the_orchestrator(page: str):
    """Plain ``defer`` scripts run in document order, so the order in the
    template IS the dependency graph."""
    # Arrange
    module_at = page.index("chat/chat_longtext.js")

    # Act
    orchestrator_at = page.index("chat/chat.js")

    # Assert
    assert module_at < orchestrator_at


def test_the_bubble_is_built_by_the_module():
    """The seam. If chat.js goes back to building a plain bubble itself, every
    guard in this file still passes and the clamp is simply gone."""
    # Arrange
    source = (_CHAT_DIR / "chat.js").read_text(encoding="utf-8")

    # Act
    delegates = "longtext.bubbleFor(" in source

    # Assert
    assert delegates


def test_the_bubble_receives_the_whole_body(code: str):
    """NON-DESTRUCTIVE, pinned at the assignment. The node that is clamped is
    the node that holds the complete text."""
    # Arrange
    assignment = "bubble.textContent = text;"

    # Act
    present = assignment in code

    # Assert
    assert present


def test_the_module_never_cuts_the_body(code: str):
    """The cheap wrong fix, banned mechanically.

    ``slice`` / ``substring`` / ``substr`` on the body is how a preview gets
    built, and it looks identical on screen to a CSS clamp until someone tries
    to copy the part that was thrown away.
    """
    # Arrange
    cutters = re.compile(r"\.(?:slice|substring|substr)\s*\(")

    # Act
    offenders = cutters.findall(code)

    # Assert
    assert offenders == []


def test_the_clamp_bounds_height_rather_than_content(page: str):
    """``max-height`` + ``overflow: hidden`` is what makes expanding a class
    flip rather than a re-fetch, and what keeps find-in-page working."""
    # Arrange
    rule = re.search(r"\.msg \.bubble\.clamped\s*\{([^}]*)\}", page)

    # Act
    body = rule.group(1) if rule else ""

    # Assert
    assert "max-height" in body and "overflow: hidden" in body


def test_the_css_budget_matches_the_modules_own_clamp(page: str, code: str):
    """ANTI-DRIFT. The JS decides WHETHER to clamp from a line count; the CSS
    decides HOW FAR from another. Two numbers, one fact — so if they ever
    disagree the module would clamp bodies the stylesheet shows in full, or
    leave a control row under a bubble that was never shortened."""
    # Arrange
    in_css = re.search(r"--longtext-lines:\s*(\d+)", page)

    # Act
    in_js = re.search(r"CLAMP_LINES\s*=\s*(\d+)", code)

    # Assert
    assert (
        in_css is not None and in_js is not None and in_css.group(1) == in_js.group(1)
    )


def test_the_controls_sit_outside_the_clipped_bubble(code: str):
    """Inside, ``overflow: hidden`` would clip the very button that undoes the
    clipping — the control would be invisible exactly when it is needed."""
    # Arrange
    fragment_used = "createDocumentFragment" in code

    # Act
    tools_appended_to_fragment = "fragment.appendChild(tools)" in code

    # Assert
    assert fragment_used and tools_appended_to_fragment


def test_the_download_is_offered_without_a_round_trip(code: str):
    """The board is served over a tunnel to a phone. The full text is already
    in memory, so a request to fetch it back would be a new way to fail with
    no new information."""
    # Arrange
    network = re.compile(r"\bfetch\s*\(|XMLHttpRequest|EventSource")

    # Act
    calls = network.findall(code)

    # Assert
    assert calls == []


def test_the_download_url_is_released_after_use(code: str):
    """The pane rebuilds every node whenever a poll diverges. An object URL
    minted per long message per rebuild and never revoked leaks the whole body
    repeatedly for the life of the page."""
    # Arrange
    needle = "URL.revokeObjectURL"

    # Act
    present = needle in code

    # Assert
    assert present


def test_expansion_survives_a_repaint(code: str):
    """The thread repaints on divergence. Without state kept outside the DOM,
    an expanded message would snap shut within five seconds — which reads as
    the page having LOST the text the operator just opened."""
    # Arrange
    needle = "expanded[key] = open"

    # Act
    present = needle in code

    # Assert
    assert present


def test_the_page_styles_the_clamp_controls(page: str):
    """A class the module puts in the DOM with no rule behind it is an
    unstyled control — and it looks fine until someone opens the page."""
    # Arrange
    needle = ".msg .longtext-btn"

    # Act
    present = needle in page

    # Assert
    assert present


# EOF
