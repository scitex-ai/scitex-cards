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


# ── (1) light-mode filter contrast ──────────────────────────────────────────


def test_filter_controls_take_their_fill_from_the_theme_palette():
    """``.filt``/``.filt option`` must not pin a dark ``background`` literal."""
    css = _without_comments(_FILTERBAR.read_text(encoding="utf-8"))
    for selector in (".filt {", ".filt option {"):
        body = css.split(selector, 1)[1].split("}", 1)[0]
        # A bare `background: #232a36` strands dark-on-dark in the light theme;
        # the same hex as a `var(--card-bg, …)` FALLBACK is fine (a bare mount
        # with no palette keeps the dark look, and the variable wins whenever
        # the board palette is present — which is what flips with the theme).
        assert not re.search(r"background:\s*#232a36", body, re.IGNORECASE), (
            f"{selector} pins a dark fill while its text follows var(--text) — "
            "dark-on-dark in the light theme (the reported invisible text)"
        )
        assert "var(--card-bg" in body, (
            f"{selector} must read its fill from the theme-flipping palette"
        )


def test_filterbar_title_and_column_names_follow_the_theme():
    """``.filterbar h1`` / ``.col-name`` must not pin ``color: #fff``."""
    css = _without_comments(_FILTERBAR.read_text(encoding="utf-8"))
    for selector in (".filterbar h1 {", ".col-name {"):
        body = css.split(selector, 1)[1].split("}", 1)[0]
        assert not re.search(r"color:\s*#fff\s*;", body, re.IGNORECASE), (
            f"{selector} pins white text — white-on-light in the light theme"
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


def test_ja_catalog_exists_compiled_and_fully_translated():
    """The JA catalog must exist, be compiled, and leave no msgid empty."""
    assert _JA_PO.exists(), "locale/ja/LC_MESSAGES/django.po is missing"
    assert _JA_PO.with_suffix(".mo").exists(), (
        "django.mo is missing beside django.po — the running app would "
        "render English (presence-only: checkouts do not preserve mtimes)"
    )
    entries = _po_entries(_JA_PO.read_text(encoding="utf-8"))
    assert len(entries) >= 20, (
        f"only {len(entries)} entries — the filterbar/Details/switcher "
        "chrome set is larger than that"
    )
    missing = [msgid for msgid, msgstr in entries.items() if not msgstr]
    assert not missing, f"untranslated msgids: {missing}"


def test_ja_catalog_covers_the_filter_chrome():
    """Spot-check the msgids the operator actually reads."""
    entries = _po_entries(_JA_PO.read_text(encoding="utf-8"))
    for msgid in (
        "Layout",
        "+ Add Task",
        "Legend",
        "Filters",
        "All projects",
        "All statuses",
        "Clear all filters",
        "Board",
        "Direct messages",
    ):
        assert entries.get(msgid), f"no JA translation for {msgid!r}"


def test_board_template_wraps_its_chrome_in_translate():
    """The served board must mark its chrome for translation (incl. the JS dict)."""
    source = _BOARD_V3.read_text(encoding="utf-8")
    assert "{% load static i18n %}" in source
    for snippet in (
        '{% translate "+ Add Task" %}',
        '{% translate "Legend" %}',
        '{% translate "Filters" %}',
        "STX_BOARD_T",
        'allProjects: "{% translate',
        "T.allStatuses",
        "blockingMeLabel",
    ):
        assert snippet in source, f"board_v3.html lost {snippet!r}"


def test_switcher_marks_its_labels_for_translation():
    """The Board|DM switcher is shared chrome — it must translate too."""
    source = _SWITCHER.read_text(encoding="utf-8")
    assert "{% load i18n %}" in source
    for snippet in (
        '{% translate "Board" %}',
        '{% translate "Direct messages" %}',
    ):
        assert snippet in source, f"_page_switcher.html lost {snippet!r}"


# EOF
