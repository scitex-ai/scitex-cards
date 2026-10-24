#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Light-mode filter contrast + JA board chrome (operator screenshots).

TWO DEFECTS, ONE PR — both are "the board chrome lies to the operator":

1. LIGHT-MODE FILTER PILLS USE A DARK FILL (invisible text). The Details-pane
   filter dropdowns (``.filt``) carried ``background: #232a36`` while their
   text came from ``var(--text)`` — which the light theme flips to dark
   ``#1f2328``. Dark-on-dark: the operator's screenshot showed filter controls
   with unreadable text. The same hardcoded-white family stranded
   ``.filterbar h1`` and ``.col-name`` (``color: #fff``) as white-on-light.
   The fix reads every fill/text from the board palette variables (which the
   ``[data-theme="light"]`` block flips), so the contrast decision can never
   split across themes again.

2. BOARD CHROME ENTIRELY ENGLISH. The served board (root ``board_v3``) had no
   JA catalog at all — no ``locale/`` dir, no ``{% trans %}`` anywhere — so a
   JA-language operator reads every control in English. This adds the
   ``locale/ja`` catalog (board filterbar + Details pane + switcher chrome;
   status/blocker enum VALUES deliberately stay English: they are store
   vocabulary, not chrome) and wires the templates to it, including the
   JS-rebuilt ``<option>`` labels via the server-rendered ``STX_BOARD_T``
   dictionary (``populateFilters()`` rebuilds the selects on every load,
   which would otherwise wipe the server-rendered translations).

SCOPE NOTE. The drawer/modal bodies, tooltips and the legacy React SPA keep
their English: this PR covers the filter/legend/switcher chrome the
screenshots show, not a full-product translation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import scitex_cards
from scitex_cards._django import views

_APP = Path(scitex_cards.__file__).parent
_STATIC = Path(views.__file__).parent / "static" / "scitex_cards"
_TEMPLATES = Path(views.__file__).parent / "templates" / "scitex_cards"
_FILTERBAR = _STATIC / "board_v3" / "01-filterbar.css"
_BOARD_V3 = _TEMPLATES / "board_v3.html"
_SWITCHER = _TEMPLATES / "_page_switcher.html"
_JA_PO = _APP / "_django" / "locale" / "ja" / "LC_MESSAGES" / "django.po"


def _without_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)


def _rule_body(css: str, selector: str) -> str:
    """The declaration block for one CSS rule (fails LOUD on reshape)."""
    assert selector in css, f"filterbar CSS lost its {selector} rule"
    return css.split(selector, 1)[1].split("}", 1)[0]


# ── (1) light-mode filter contrast ──────────────────────────────────────────


@pytest.mark.parametrize("selector", [".filt {", ".filt option {"])
def test_filter_controls_pin_no_dark_fill(selector: str):
    """No ``.filt`` rule may pin the dark ``background`` literal again."""
    # Arrange
    css = _without_comments(_FILTERBAR.read_text(encoding="utf-8"))
    # Act
    body = _rule_body(css, selector)
    # Assert — a bare `background: #232a36` strands dark-on-dark in the
    # light theme; the same hex as a `var(--card-bg, …)` FALLBACK is fine
    # (a bare mount with no palette keeps the dark look, and the variable
    # wins whenever the board palette is present).
    assert not re.search(r"background:\s*#232a36", body, re.IGNORECASE)


@pytest.mark.parametrize("selector", [".filt {", ".filt option {"])
def test_filter_controls_read_fill_from_the_theme_palette(selector: str):
    """``.filt`` fills must come from the theme-flipping board palette."""
    # Arrange
    css = _without_comments(_FILTERBAR.read_text(encoding="utf-8"))
    # Act
    body = _rule_body(css, selector)
    # Assert
    assert "var(--card-bg" in body


@pytest.mark.parametrize("selector", [".filterbar h1 {", ".col-name {"])
def test_filterbar_title_and_column_names_follow_the_theme(selector: str):
    """``.filterbar h1`` / ``.col-name`` must not pin ``color: #fff``."""
    # Arrange
    css = _without_comments(_FILTERBAR.read_text(encoding="utf-8"))
    # Act
    body = _rule_body(css, selector)
    # Assert
    assert not re.search(r"color:\s*#fff\s*;", body, re.IGNORECASE)


def test_light_page_background_is_the_hub_token_not_white():
    """Operator 2026-10-24: the light page background is the hub's warm
    grey, never full white. The dark tiers already equal the hub tokens,
    so the light tiers map onto the same family."""
    # Arrange
    css = _without_comments(_FILTERBAR.read_text(encoding="utf-8"))
    # Act
    block = re.search(r'\[data-theme="light"\] \{(.*?)\}', css, flags=re.DOTALL)
    body = block.group(1) if block else ""
    # Assert — one compound assertion (PA-307).
    assert (
        block is not None
        and "#ffffff" not in body.lower()
        and "var(--bg-page" in body
        and "var(--bg-surface" in body
    )


# ── (2) JA board chrome ─────────────────────────────────────────────────────


def _po_entries(po_text: str) -> dict[str, str]:
    """Minimal ``msgid -> msgstr`` map (this catalog uses single-line entries)."""
    entries: dict[str, str] = {}
    msgid: str | None = None
    for line in po_text.splitlines():
        line = line.strip()
        if line.startswith('msgid "') and not line.startswith('msgid ""'):
            msgid = line[len('msgid "') : -1]
        elif line.startswith('msgstr "') and msgid is not None:
            entries[msgid] = line[len('msgstr "') : -1]
            msgid = None
    return entries


def test_ja_catalog_po_file_exists():
    """The JA catalog source must be tracked beside the app."""
    # Arrange — none; the pin inspects the tracked locale tree.
    # Act
    found = _JA_PO.exists()
    # Assert
    assert found, "locale/ja/LC_MESSAGES/django.po is missing"


def test_ja_catalog_is_compiled_to_mo():
    """The running app reads ``.mo`` — an uncompiled catalog renders English."""
    # Arrange — none; presence-only (checkouts do not preserve mtimes).
    # Act
    found = _JA_PO.with_suffix(".mo").exists()
    # Assert
    assert found, "django.mo is missing beside django.po"


def test_ja_catalog_has_the_full_chrome_set():
    """The catalog must cover filterbar + Details + switcher chrome."""
    # Arrange
    entries = _po_entries(_JA_PO.read_text(encoding="utf-8"))
    # Act
    count = len(entries)
    # Assert
    assert count >= 20, f"only {count} entries — the chrome set is larger"


def test_ja_catalog_leaves_no_msgid_untranslated():
    """Every catalogued msgid must carry its JA string."""
    # Arrange
    entries = _po_entries(_JA_PO.read_text(encoding="utf-8"))
    # Act
    missing = [msgid for msgid, msgstr in entries.items() if not msgstr]
    # Assert
    assert not missing, f"untranslated msgids: {missing}"


@pytest.mark.parametrize(
    "msgid",
    [
        "Layout",
        "+ Add Task",
        "Legend",
        "Filters",
        "All projects",
        "All statuses",
        "Clear all filters",
        "Board",
        "Direct messages",
    ],
)
def test_ja_catalog_covers_the_filter_chrome(msgid: str):
    """Spot-check the msgids the operator actually reads."""
    # Arrange
    entries = _po_entries(_JA_PO.read_text(encoding="utf-8"))
    # Act
    translated = entries.get(msgid)
    # Assert
    assert translated, f"no JA translation for {msgid!r}"


def _board_sources() -> str:
    """Board markup as served: board_v3.html plus its three partials.

    Since the unified page (operator 2026-10-20) chrome snippets (i18n
    markers, option dicts) live across _board_pane/_board_head/_board_foot —
    one source served standalone AND embedded.
    """
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in (
            _BOARD_V3,
            _BOARD_V3.parent / "_board_pane.html",
            _BOARD_V3.parent / "_board_head.html",
            _BOARD_V3.parent / "_board_foot.html",
        )
    )


def test_board_template_loads_the_i18n_library():
    """``board_v3.html`` cannot translate chrome without the i18n library."""
    # Arrange
    source = _BOARD_V3.read_text(encoding="utf-8")
    # Act — none; the pin inspects the served template text.
    # Assert
    assert "{% load static i18n %}" in source


@pytest.mark.parametrize(
    "snippet",
    [
        '{% translate "+ Add Task" %}',
        '{% translate "Legend" %}',
        '{% translate "Filters" %}',
        "STX_BOARD_T",
        'allProjects: "{% translate',
        "T.allStatuses",
        "blockingMeLabel",
    ],
)
def test_board_template_marks_chrome_snippets_for_translation(snippet: str):
    """Chrome — incl. the JS option dict — must go through translate."""
    # Arrange
    source = _board_sources()
    # Act — none; the pin inspects the served template text.
    # Assert
    assert snippet in source, f"board_v3.html lost {snippet!r}"


def test_switcher_loads_the_i18n_library():
    """The Board|DM switcher is shared chrome — it needs i18n too."""
    # Arrange
    source = _SWITCHER.read_text(encoding="utf-8")
    # Act — none; the pin inspects the served template text.
    # Assert
    assert "{% load i18n %}" in source


@pytest.mark.parametrize(
    "snippet",
    [
        '{% translate "Board" %}',
        '{% translate "Direct messages" %}',
    ],
)
def test_switcher_marks_shared_labels_for_translation(snippet: str):
    """The switcher labels must translate with the active language."""
    # Arrange
    source = _SWITCHER.read_text(encoding="utf-8")
    # Act — none; the pin inspects the served template text.
    # Assert
    assert snippet in source, f"_page_switcher.html lost {snippet!r}"


# EOF
