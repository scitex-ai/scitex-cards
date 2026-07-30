#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every USER-VISIBLE string says "DM" — and the /chat ROUTE keeps working.

Operator, 2026-07-29 (TG): 「あと、"chat" となってますが、"DM" でそろえると良いと
思います。」 — and earlier the same day, that 「"Direct Message"」 is the wording
they want for the longer form. The switcher had already been relabelled; what
they were still looking at in their screenshot was the BROWSER TAB reading
"Chat — SciTeX Cards v0.17.10". A tab title is a user-visible string, and it was
the last one saying Chat.

THE ROUTE WAS NOT PART OF THE RENAME, and half of this file exists to keep the
old one alive. Renaming a published URL is a MIGRATION, not a rename: the
operator has /chat bookmarked, agents reference it, and both spellings are
already pinned by ``test__chat_page_trailing_slash.py``. Renaming it to match a
LABEL would have broken every one of those to change a word. The same reasoning
still covers the JS module filenames (chat_*.js), the CSS classes and the
template filenames — none of them is a string the operator reads.

LATER THE SAME DAY the operator asked for ``/board`` and ``/dm`` as URLs
outright, and #616 published them as ALIASES — the guard that used to forbid a
``/dm`` page is replaced by ``test_the_dm_and_chat_routes_serve_the_SAME_surface``
(see its docstring for why the concern survives the reversal). THEN, with both
doors open, the switcher's links moved to the new names, which is the second
step of that same migration rather than a reversal of it: alias first, switch
after. So the two halves below are still in tension and both must hold —
visible text says DM, and ``/chat`` still SERVES (pinned in
``test__board_and_dm_routes.py`` and ``test__board_chat_switcher.py``). The rule
was never "no second URL", and never "the link may not move"; it was "no
LABEL-driven migration that costs a published one".

Rendered against the real views with ``RequestFactory`` — the convention the
sibling ``_django`` view tests already follow (no mocks, no fake templates).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402
from django.urls import resolve  # noqa: E402

from scitex_cards._django import views  # noqa: E402

_DJANGO_DIR = Path(views.__file__).resolve().parent
_PARTIAL = _DJANGO_DIR / "templates" / "scitex_cards" / "_page_switcher.html"

_URLCONF = "scitex_cards._django.urls"

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_ITEM_RE = re.compile(r"<a[^>]*stx-cards-switcher__item[^>]*>([^<]*)</a>")


@pytest.fixture
def chat_html():
    """The DM page as the standalone server serves it."""
    request = RequestFactory().get("/chat")
    return views.chat_page(request).content.decode("utf-8")


def _title_of(html: str) -> str:
    match = _TITLE_RE.search(html)
    return match.group(1).strip() if match else ""


def _switcher_labels(html: str) -> list[str]:
    return [m.group(1).strip() for m in _ITEM_RE.finditer(html)]


# --- the browser tab -------------------------------------------------------


def test_the_tab_title_says_dm(chat_html):
    """The exact string in the operator's screenshot, corrected."""
    # Arrange
    html = chat_html
    # Act
    title = _title_of(html)
    # Assert
    assert title.startswith("DM ")


def test_the_tab_title_no_longer_says_chat(chat_html):
    """The point of the request: 「"chat" となってますが」."""
    # Arrange
    html = chat_html
    # Act
    title = _title_of(html)
    # Assert
    assert "Chat" not in title


def test_the_tab_title_still_carries_the_version(chat_html):
    """Renaming the surface must not cost the version the operator quotes when
    reporting what they are looking at."""
    # Arrange
    html = chat_html
    # Act
    title = _title_of(html)
    # Assert
    assert "SciTeX Cards v" in title


# --- the switcher ----------------------------------------------------------


def test_the_switcher_second_item_is_labelled_dm(chat_html):
    """『Board | "DM"』 — their words."""
    # Arrange
    html = chat_html
    # Act
    labels = _switcher_labels(html)
    # Assert
    assert labels == ["Board", "DM"]


def test_the_switcher_has_no_item_labelled_chat(chat_html):
    """A second label for one surface is exactly the inconsistency reported."""
    # Arrange
    html = chat_html
    # Act
    labels = _switcher_labels(html)
    # Assert
    assert "Chat" not in labels


def test_the_switcher_landmark_is_announced_as_dm(chat_html):
    """`aria-label` is read aloud — an assistive user must not be told this is
    a "Board or Chat" switcher when the visible label says DM."""
    # Arrange
    html = chat_html
    # Act
    announced = 'aria-label="Board or DM"' in html
    # Assert
    assert announced


def test_the_switcher_tooltip_uses_the_longer_direct_message_wording(chat_html):
    """Their stated preference for the long form: 「"Direct Message" がよい」."""
    # Arrange
    html = chat_html
    # Act
    tooltip = 'title="Direct messages with the agents"' in html
    # Assert
    assert tooltip


def test_no_switcher_tooltip_still_says_chat(chat_html):
    """A tooltip is user-visible text too — it was the other half of the label."""
    # Arrange
    html = chat_html
    # Act
    stale = 'title="Chat' in html
    # Assert
    assert not stale


# --- the page heading ------------------------------------------------------


def test_the_page_heading_does_not_change_between_board_and_dm(chat_html):
    """The identity band names the PRODUCT, so it reads the same on both pages.

    SUPERSEDES ``test_the_page_heading_uses_the_direct_message_wording``, which
    pinned ``<h1>Direct messages</h1>``. Operator, 2026-07-30: 「ボードの上で
    張った部分は DM でも同じにしてください」 then 「header が変わると変です」 —
    they sent the board's header and asked for it verbatim here, because a
    heading whose text changes as you move between Board and DM reads as the
    page breaking rather than as navigation.

    THE DM WORDING WAS NOT DROPPED, and the three tests around this one are why
    this change is safe: the switcher item still reads "DM" and is the element
    that highlights, its tooltip still reads "Direct messages with the agents",
    and the browser tab still reads "DM — SciTeX Cards v…". Their 2026-07-29
    request was "not chat, use DM wording"; this one is about the heading
    CHANGING. Both hold once the band names the product and the switcher names
    the page.
    """
    # Arrange
    html = chat_html

    # Act
    heading = "<h1>SciTeX Cards</h1>" in html

    # Assert
    assert heading


def test_the_page_still_identifies_itself_as_dm_somewhere_visible(chat_html):
    """The control that says WHICH page you are on must still say DM.

    Guards the half the change above could have quietly cost: with the heading
    now naming the product on both pages, the switcher is the only visible thing
    left distinguishing them. If it ever stopped saying DM, the two pages would
    become indistinguishable — which is a worse version of the complaint that
    prompted the change.
    """
    # Arrange
    html = chat_html

    # Act
    identified = ">DM</a>" in html

    # Assert
    assert identified


# --- the ROUTE is NOT renamed ----------------------------------------------


def test_the_chat_route_still_resolves(chat_html):
    """THE guard on this whole change. /chat is published: bookmarked by the
    operator, referenced by agents. A label change may not move it."""
    # Arrange
    path = "/chat"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is views.chat_page


def test_the_slashed_chat_route_still_resolves():
    """The spelling the operator actually types (see
    test__chat_page_trailing_slash.py for the 404 that taught us)."""
    # Arrange
    path = "/chat/"
    # Act
    match = resolve(path, urlconf=_URLCONF)
    # Assert
    assert match.func is views.chat_page


def test_the_dm_and_chat_routes_serve_the_SAME_surface():
    """REPLACES ``test_no_dm_page_route_was_invented``, which asserted that no
    ``/dm`` page alias existed at all.

    That guard was correct for the change it was written against — a LABEL
    change may not drag a published URL along with it — and is superseded now:
    on 2026-07-29 the operator asked for ``/board`` and ``/dm`` DIRECTLY,
    typed both, and got a JSON 404 that reads as the board being broken. A
    guard written to protect against one instruction does not outrank a later,
    explicit instruction to the contrary. But the concern underneath it does
    not evaporate, so it is RESTATED here rather than dropped — deleting a
    guard because it went red is how a rule gets lost.

    The old concern was "two URLs for one surface, one of which every existing
    link ignores". Half of that is now WANTED: two doors onto one room. What
    is pinned here is the half that still matters — the two doors must open on
    the SAME room. ``/chat`` keeping its own view while ``/dm`` quietly grew a
    second, slightly different one is exactly the drift this file exists to
    catch. The other half (every existing link still works) is
    ``test_the_chat_route_still_resolves`` above, plus the alias tests in
    ``test__board_and_dm_routes.py`` and the SERVE assertions in
    ``test__board_chat_switcher.py`` — which matter MORE now than when this was
    written, because the switcher's links have since moved to ``/dm`` and
    nothing this app emits exercises ``/chat`` any more.
    """
    # Arrange
    paths = ("/chat", "/dm")
    # Act
    served = {resolve(path, urlconf=_URLCONF).func for path in paths}
    # Assert
    assert served == {views.chat_page}


def test_the_switcher_href_points_at_the_dm_route(chat_html):
    """REPLACES ``test_the_switcher_href_still_points_at_the_chat_route``, which
    pinned ``/chat``.

    That pin was the label-vs-URL guard: a LABEL change may not drag a
    published URL along with it. It is not overridden here, it is spent — the
    operator asked for the URL itself next, verbatim 「url も
    http://127.0.0.1:8051/board http://127.0.0.1:8051/dm とした方が良いと思いま
    すけどね」, and #616 had already published ``/dm`` as an ALIAS, so pointing
    the link at it costs no bookmark. What the old pin was really protecting —
    that ``/chat`` keeps working — is pinned in
    ``test__board_chat_switcher.py`` as a SERVE assertion and above as a
    resolve assertion, which is where it belongs now that no link exercises it.
    """
    # Arrange
    html = chat_html
    # Act
    hrefs = re.findall(r'href="([^"]*)"[^>]*>DM</a>', html)
    # Assert
    assert hrefs == ["/dm"]


def test_the_switcher_partial_documents_the_migration_it_completes():
    """The next reader will see a renamed URL under a rule that says published
    URLs are not renamed, and will reasonably think one of them is wrong. The
    reason the two agree — alias first, THEN switch — lives beside the code."""
    # Arrange
    partial_path = _PARTIAL
    # Act
    source = partial_path.read_text(encoding="utf-8")
    # Assert
    assert "MIGRATION" in source


# EOF
