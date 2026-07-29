#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Structural contract for the emoji picker (no mocks — read the sources).

Three properties are pinned here because none of them can be checked by
running the module, and all three are the kind of thing that breaks silently:

1. SELF-CONTAINMENT. The board is served over a tunnel to the operator's
   phone. Anything external — a CDN script, a webfont, a runtime fetch —
   does not load there, and an icon glyph that fails to resolve renders an
   empty box exactly where the affordance was. That is why the composer's
   attach button is a literal text paperclip, and why these emoji are
   literal characters. A single absolute URL added later would reintroduce
   the failure without any local symptom.

2. HARVESTABILITY. The picker is written to move into scitex-ui as a shared
   primitive. That only stays true while it depends on nothing about this
   page: no ``.msg``, no ``#compose``, no chat ids. The moment a rule reaches
   for a chat ancestor, lifting the file stops being a move and becomes a
   rewrite.

3. WIRING. chat.html mounts the picker declaratively, so nothing in the
   page's own JS references it. Delete the mount attribute or the script tag
   and the feature simply is not there — with no error anywhere to say so.

Plus one regression guard each for the two mistakes already made and fixed
while building it: the page's ``#compose button`` rules capturing the
picker's own buttons, and cancelling ``touchstart`` (which suppresses the
emulated click on iOS, i.e. taps insert nothing on the target device).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[6]
_UI_DIR = (
    _REPO_ROOT / "src" / "scitex_cards" / "_django" / "static" / "scitex_cards" / "ui"
)
_JS_FILE = _UI_DIR / "emoji_picker.js"
_CSS_FILE = _UI_DIR / "emoji_picker.css"


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    """Drop /* … */ and // … so prose about a hazard is not read as the
    hazard itself."""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", without_block)


def _strip_template_prose(source: str) -> str:
    """Drop {% comment %} blocks and CSS comments from a page.

    chat.html documents the very selector bug the wiring test below scans
    for, and prose describing a mistake must not read as the mistake. The
    CSS comments matter most: those survive into the served page."""
    without_django = re.sub(
        r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}",
        "",
        source,
        flags=re.DOTALL,
    )
    return re.sub(r"/\*.*?\*/", "", without_django, flags=re.DOTALL)


# The class names the module actually emits, harvested from the source so a
# rename shows up here rather than as an unstyled widget in the browser.
def _emitted_class_names() -> list[str]:
    js = _read(_JS_FILE)
    return sorted(set(re.findall(r'"(stx-emoji-picker[\w-]*)"', js)))


# === 1. self-containment ====================================================


@pytest.mark.parametrize("source_file", [_JS_FILE, _CSS_FILE])
def test_no_absolute_url_anywhere(source_file: Path) -> None:
    """An http(s) or protocol-relative URL is a request that will never
    complete over the operator's tunnel."""
    # Arrange
    source = _strip_comments(_read(source_file))
    # Act
    urls = re.findall(r"(?:https?:)?//[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", source)
    # Assert
    assert urls == []


def test_stylesheet_pulls_in_no_other_file() -> None:
    """An @import or a url() is a second request; over the tunnel it is a
    second thing to fail. Everything the picker needs is in this one file."""
    # Arrange
    css = _strip_comments(_read(_CSS_FILE))
    # Act
    pulls = re.findall(r"@import|url\s*\(", css)
    # Assert
    assert pulls == []


def test_module_makes_no_network_call() -> None:
    """The picker renders from a literal list. A fetch here would mean the
    grid is empty exactly when the network is the problem."""
    # Arrange
    js = _strip_comments(_read(_JS_FILE))
    # Act
    calls = re.findall(r"\bfetch\s*\(|XMLHttpRequest|EventSource", js)
    # Assert
    assert calls == []


def test_emoji_are_literal_characters_not_codepoint_escapes() -> None:
    """Literal characters are legible in the source and cannot be
    mis-assembled at runtime; an escape sequence hides a torn variation
    selector until it renders as a box on the phone."""
    # Arrange
    js = _read(_JS_FILE)
    # Act
    rows = re.search(r"var EMOJI_ROWS = \[(.*?)\n  \];", js, flags=re.DOTALL)
    # Assert
    assert rows is not None and "\\u" not in rows.group(1)


# === 2. harvestability ======================================================


@pytest.mark.parametrize(
    "chat_specific",
    [".msg", "#compose", "#messages", "#thread-pane", "#agents"],
)
def test_stylesheet_names_no_chat_specific_ancestor(chat_specific: str) -> None:
    """The component must style itself from its own class names only. A rule
    hung off a chat ancestor is a dependency that does not travel with the
    file into scitex-ui."""
    # Arrange
    # Act
    css = _strip_comments(_read(_CSS_FILE))
    # Assert
    assert chat_specific not in css


@pytest.mark.parametrize("chat_id", ["compose-body", "compose-send", "messages"])
def test_module_hardcodes_no_chat_element_id(chat_id: str) -> None:
    """The host page says which field to target (via the mount attribute);
    the module must not know the answer in advance."""
    # Arrange
    # Act
    js = _strip_comments(_read(_JS_FILE))
    # Assert
    assert chat_id not in js


@pytest.mark.parametrize("class_name", _emitted_class_names())
def test_every_emitted_class_has_a_rule(class_name: str) -> None:
    """A class the JS puts in the DOM with no rule behind it is either dead
    code or an unstyled element — and both look fine until someone opens the
    page."""
    # Arrange
    # Act
    css = _read(_CSS_FILE)
    # Assert
    assert f".{class_name}" in css


def test_palette_is_exposed_as_component_tokens() -> None:
    """The host page recolours the picker by setting these tokens. Without
    them it would have to override the component's rules one by one, which
    is the coupling this design exists to avoid."""
    # Arrange
    # Act
    css = _read(_CSS_FILE)
    # Assert
    assert "--stx-emoji-picker-surface:" in css


# === 3. theme + touch =======================================================


def test_stylesheet_follows_the_os_dark_preference() -> None:
    """Half the point of a token layer is that the widget is legible in both
    themes without the host page saying anything."""
    # Arrange
    # Act
    css = _read(_CSS_FILE)
    # Assert
    assert "@media (prefers-color-scheme: dark)" in css


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_an_explicit_theme_attribute_overrides_the_os(theme: str) -> None:
    """scitex-ui's contract is <html data-theme="…">. An explicit choice must
    beat the OS preference in BOTH directions, or a viewer who picked light
    on a dark laptop gets a dark widget on a light page."""
    # Arrange
    # Act
    css = _read(_CSS_FILE)
    # Assert
    assert f'[data-theme="{theme}"] .stx-emoji-picker' in css


def test_tap_targets_opt_out_of_the_double_tap_delay() -> None:
    """Without touch-action, a phone waits ~300ms after each tap in case a
    second one arrives — which reads as a laggy, unresponsive grid."""
    # Arrange
    # Act
    css = _read(_CSS_FILE)
    # Assert
    assert css.count("touch-action: manipulation") >= 2


def test_the_module_never_cancels_a_touch_event() -> None:
    """THE phone regression. Cancelling touchstart to stop the button
    stealing focus also suppresses the emulated click on iOS — the tap would
    then insert nothing on the one device this feature is for. Focus is held
    on mousedown only, and the caret is remembered instead."""
    # Arrange
    # Act
    js = _strip_comments(_read(_JS_FILE))
    # Assert
    assert "touchstart" not in js


# === 4. wiring into the SERVED chat page ===================================
#
# These assert on the RENDERED response, not on the template file. A template
# can hold correct markup and still serve none of it — a mistyped {% static %}
# path, a block that never renders — and reading the source would not show it.
# The page is what the operator's phone receives, so the page is what is
# pinned. (The store is untouched by this view; chat_page renders from the
# request path alone.)


def _rendered_chat_page() -> str:
    """The DM page exactly as a browser at the root mount receives it."""
    return views.chat_page(RequestFactory().get("/chat")).content.decode("utf-8")


def test_served_page_links_the_stylesheet() -> None:
    """Unstyled, the panel renders as sixty stacked buttons over the
    thread."""
    # Arrange
    # Act
    html = _rendered_chat_page()
    # Assert
    assert "scitex_cards/ui/emoji_picker.css" in html


def test_served_page_loads_the_module() -> None:
    """Nothing else references the picker, so a missing script tag removes
    the feature with no error anywhere to explain the absence."""
    # Arrange
    # Act
    html = _rendered_chat_page()
    # Assert
    assert "scitex_cards/ui/emoji_picker.js" in html


def test_served_page_mounts_the_picker_onto_the_composer_field() -> None:
    """The declarative mount IS the integration: this attribute names the
    field the emoji land in, and chat.js contains not one line about it."""
    # Arrange
    # Act
    html = _rendered_chat_page()
    # Assert
    assert 'data-stx-emoji-picker-for="compose-body"' in html


def test_the_targeted_composer_field_exists_on_the_page() -> None:
    """A mount attribute naming an id that is not there is skipped silently
    by design (one bad marker must not break the page's scripting) — which
    means a typo removes the picker and reports nothing."""
    # Arrange
    # Act
    html = _rendered_chat_page()
    # Assert
    assert 'id="compose-body"' in html


def test_the_page_does_not_restyle_the_pickers_buttons() -> None:
    """THE styling regression. Without the child combinator, the composer's
    own button rules also matched every button inside the picker — and an id
    selector outranks the component's classes, so the toggle and all sixty
    emoji rendered as accent-filled Send buttons. Scoping to direct children
    keeps the page's rules on the page's own buttons."""
    # Arrange
    html = _strip_template_prose(_rendered_chat_page())
    # Act
    unscoped = re.findall(r"#compose button", html)
    # Assert
    assert unscoped == []
