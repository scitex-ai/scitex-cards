#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board and the DM page must render the SAME header — one partial, one side.

TWO OPERATOR COMPLAINTS, 2026-07-29 (TG), and one root cause.

1. The switcher was on the LEFT on one page and the RIGHT on the other. The
   operator had already asked for LEFT and had to ask twice. (For the record,
   the page that was wrong was the DM page, not the board: the board rendered
   the switcher first in `.fb-left`, while chat.html's `header
   .stx-cards-switcher { margin-left: auto }` pushed it to the far right. The
   markup read switcher-last there, so BOTH layers said "right" — which is why
   reading either one alone had already produced a wrong diagnosis once.)

2. The headers did not line up. The board's bar was `padding: 8px 14px` with a
   48px floor; the DM page's was `padding: 10px 14px` with none.

THE DRIFT IS THE DEFECT, not either position. Both complaints have the same
shape: each page rolled its own header, so the two could disagree and nobody
would notice until the operator looked at them side by side. So the tests here
do not pin two positions that must each be right — they pin that there is only
ONE header to get right (`_page_header.html` + page-header.css) and that both
pages render it. A test that merely asserted "left" on each page would keep
passing the day someone adds a second header.

Rendered against the real views with ``RequestFactory`` — the convention the
sibling ``_django`` view tests already follow (no mocks, no fake templates).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: north, title: North Star, status: goal, depends_on: [build]}\n"
    "  - {id: build, title: Build It, status: in_progress, priority: 1}\n"
)

_DJANGO_DIR = Path(views.__file__).resolve().parent
_TEMPLATES = _DJANGO_DIR / "templates" / "scitex_cards"
_STATIC = _DJANGO_DIR / "static" / "scitex_cards"

_HEADER_PARTIAL = _TEMPLATES / "_page_header.html"
_HEADER_CSS = _STATIC / "page-header.css"

#: The partial's own opening tag. Nothing else may emit it — see
#: ``test_the_shared_header_markup_lives_in_exactly_one_template``.
_BAND_OPEN = '<div class="stx-cards-header">'

#: The include line each page must carry, spelled as it appears in the source.
_INCLUDE = '{% include "scitex_cards/_page_header.html"'

#: Innermost CSS rule blocks: `selector { declarations }`. Good enough for the
#: flat stylesheets here — a `@media` prelude simply becomes part of the
#: selector text, which is harmless because we only ask whether the selector
#: mentions the switcher.
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)


@pytest.fixture
def store():
    """Seed the canonical DB and reset the board cache around the test.

    The board page renders through the live board loader, so it needs a store
    it may read — an EXPLICIT, per-test one, never the fleet's.
    """
    from conftest import seed_db_from_doc

    from scitex_cards._yaml import safe_load

    doc = safe_load(_STORE_TEXT) or {}
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    _reset_cache()
    yield os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _reset_cache()


@pytest.fixture
def board_html(store):
    """The board home as the standalone server serves it."""
    request = RequestFactory().get(f"/?store={store}")
    return views.board_v3_page(request).content.decode("utf-8")


@pytest.fixture
def dm_html():
    """The DM page as the standalone server serves it."""
    return views.chat_page(RequestFactory().get("/dm")).content.decode("utf-8")


def _band(html: str) -> str:
    """The rendered identity band — switcher, heading, version — or ""."""
    start = html.find(_BAND_OPEN)
    if start == -1:
        return ""
    end = html.find("</div>", start)
    return html[start:end] if end != -1 else html[start:]


def _switcher_side(html: str) -> str:
    """Which end of the header the switcher sits at, read off the markup."""
    band = _band(html)
    switcher = band.find('class="stx-cards-switcher"')
    heading = band.find("<h1>")
    if switcher == -1 or heading == -1:
        return "absent"
    return "left" if switcher < heading else "right"


def _switcher_rules(css: str) -> list[str]:
    """Declaration blocks of every rule whose selector names the switcher."""
    css = _CSS_COMMENT.sub(" ", css)
    return [
        body
        for selector, body in _RULE.findall(css)
        if "stx-cards-switcher" in selector
    ]


def _page_css(html: str) -> str:
    """Every inline <style> block of a rendered page, concatenated."""
    return "\n".join(_STYLE_BLOCK.findall(html))


# --- ONE header, rendered by BOTH pages ------------------------------------


def test_both_page_templates_include_the_shared_header_partial():
    """The structural fix. Two hand-written headers is HOW the two drifted;
    naming the missing page in the failure beats a bare False."""
    # Arrange
    pages = ("board_v3.html", "chat.html")
    # Act
    including = [
        name
        for name in pages
        if _INCLUDE in (_TEMPLATES / name).read_text(encoding="utf-8")
    ]
    # Assert
    assert including == list(pages)


def test_both_pages_render_the_shared_header_partial(board_html, dm_html):
    """Source-level inclusion is not service: a template can include a partial
    inside a block the view never renders. This reads the served bytes."""
    # Arrange
    pages = {"board": board_html, "dm": dm_html}
    # Act
    rendering = [name for name, html in pages.items() if _BAND_OPEN in html]
    # Assert
    assert rendering == ["board", "dm"]


def test_the_shared_header_markup_lives_in_exactly_one_template():
    """ "Shared" means one file. A second template emitting the same band is a
    copy, and a copy is a drift waiting to be noticed by the operator."""
    # Arrange
    templates = sorted(_TEMPLATES.glob("*.html"))
    # Act
    emitting = [
        p.name for p in templates if _BAND_OPEN in p.read_text(encoding="utf-8")
    ]
    # Assert
    assert emitting == ["_page_header.html"]


# --- the switcher is on the LEFT, and both pages AGREE about it ------------


def test_the_switcher_is_on_the_left_of_both_headers(board_html, dm_html):
    """The operator's instruction and the agreement guard in one statement: a
    tuple, so a failure says WHICH page moved and where it moved to."""
    # Arrange
    pages = (board_html, dm_html)
    # Act
    sides = tuple(_switcher_side(html) for html in pages)
    # Assert
    assert sides == ("left", "left")


def test_no_page_pushes_the_switcher_away_with_an_auto_margin(board_html, dm_html):
    """THE regression that produced the split, and the reason markup order is
    not enough on its own: `margin-left: auto` moved the control while the
    template still read switcher-first, so the template looked correct."""
    # Arrange
    sources = {
        "board (inline)": _page_css(board_html),
        "dm (inline)": _page_css(dm_html),
        "page-switcher.css": (_STATIC / "page-switcher.css").read_text(
            encoding="utf-8"
        ),
        "page-header.css": _HEADER_CSS.read_text(encoding="utf-8"),
    }
    # Act
    offenders = sorted(
        name
        for name, css in sources.items()
        if any("margin-left: auto" in body for body in _switcher_rules(css))
    )
    # Assert
    assert offenders == []


# --- the header GEOMETRY is stated once ------------------------------------


def test_both_pages_load_the_shared_header_stylesheet(board_html, dm_html):
    """The partial alone does not make two headers the same height — the
    stylesheet holding that height does, and both pages must load it."""
    # Arrange
    pages = {"board": board_html, "dm": dm_html}
    # Act
    linking = [
        name for name, html in pages.items() if "scitex_cards/page-header.css" in html
    ]
    # Assert
    assert linking == ["board", "dm"]


def test_both_headers_carry_the_shared_headerbar_class(board_html, dm_html):
    """The class is what applies the shared geometry to each page's own bar
    element (`.filterbar` on the board, `<header>` on the DM page). Without it
    the partial renders into two differently-sized boxes again."""
    # Arrange
    pages = {"board": board_html, "dm": dm_html}
    # Act
    carrying = [name for name, html in pages.items() if "stx-cards-headerbar" in html]
    # Assert
    assert carrying == ["board", "dm"]


def test_the_header_height_is_stated_in_exactly_one_stylesheet():
    """The number that made the two headers different heights. Restating it on
    either page is the drift growing back, so only the shared sheet may say
    it. (Comments are stripped first: a file explaining the rule must not read
    as breaking it.)"""
    # Arrange — each page contributes only the CSS it actually declares: its
    # own stylesheet, or the <style> blocks of its template.
    sources = {
        "page-header.css": _HEADER_CSS.read_text(encoding="utf-8"),
        "01-filterbar.css": (_STATIC / "board_v3" / "01-filterbar.css").read_text(
            encoding="utf-8"
        ),
        "board_v3.html": _page_css((_TEMPLATES / "board_v3.html").read_text("utf-8")),
        "chat.html": _page_css((_TEMPLATES / "chat.html").read_text("utf-8")),
    }
    # Act
    stating = sorted(
        name for name, text in sources.items() if "48px" in _CSS_COMMENT.sub(" ", text)
    )
    # Assert
    assert stating == ["page-header.css"]


def test_the_shared_header_stylesheet_exists_where_both_templates_point():
    """A {% static %} link to a missing file renders fine and 404s silently in
    the browser — leaving both headers unstyled and, once again, unequal."""
    # Arrange
    css_path = _HEADER_CSS
    # Act
    exists = css_path.is_file()
    # Assert
    assert exists


def test_the_header_partial_exists_where_both_templates_include_it():
    """A missing include is a TemplateDoesNotExist at request time, i.e. an
    operator-visible 500 rather than a test failure — pin it here."""
    # Arrange
    partial_path = _HEADER_PARTIAL
    # Act
    exists = partial_path.is_file()
    # Assert
    assert exists


# EOF
