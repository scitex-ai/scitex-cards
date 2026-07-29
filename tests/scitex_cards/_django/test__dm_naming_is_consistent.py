#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every USER-VISIBLE string says "DM" — and the /chat ROUTE is untouched.

Operator, 2026-07-29 (TG): 「あと、"chat" となってますが、"DM" でそろえると良いと
思います。」 — and earlier the same day, that 「"Direct Message"」 is the wording
they want for the longer form. The switcher had already been relabelled; what
they were still looking at in their screenshot was the BROWSER TAB reading
"Chat — SciTeX Cards v0.17.10". A tab title is a user-visible string, and it was
the last one saying Chat.

THE ROUTE IS NOT PART OF THE RENAME, and half of this file exists to keep it
that way. Renaming a published URL is a MIGRATION, not a rename: the operator
has /chat bookmarked, agents reference it, and both spellings are already pinned
by ``test__chat_page_trailing_slash.py``. Renaming it to match a LABEL would
break every one of those to change a word. The same reasoning covers the JS
module filenames (chat_*.js), the CSS classes and the template filenames —
none of them is a string the operator reads.

So the two halves below are deliberately in tension, and both must hold:
visible text says DM; the URL still says chat.

LATER THE SAME DAY the operator asked for ``/dm`` as a URL outright — so a
``/dm`` route now EXISTS, and the guard that used to forbid one has been
replaced by ``test_the_dm_and_chat_routes_serve_the_SAME_surface`` (see its
docstring for why the concern survives the reversal). This does not soften the
paragraph above: ``/chat`` was ADDED to, not renamed, and every link this app
emits still says chat. The rule was never "no second URL"; it was "no
LABEL-driven migration of a published one".

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


def test_the_page_heading_uses_the_direct_message_wording(chat_html):
    """The <h1> the operator reads on the page itself."""
    # Arrange
    html = chat_html
    # Act
    heading = "<h1>Direct messages</h1>" in html
    # Assert
    assert heading


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
    ``test__board_and_dm_routes.py``; and the link targets themselves are
    unchanged, which ``test_the_switcher_href_still_points_at_the_chat_route``
    below still pins.
    """
    # Arrange
    paths = ("/chat", "/dm")
    # Act
    served = {resolve(path, urlconf=_URLCONF).func for path in paths}
    # Assert
    assert served == {views.chat_page}


def test_the_switcher_href_still_points_at_the_chat_route(chat_html):
    """The label moved; the destination did not."""
    # Arrange
    html = chat_html
    # Act
    hrefs = re.findall(r'href="([^"]*)"[^>]*>DM</a>', html)
    # Assert
    assert hrefs == ["/chat"]


def test_the_switcher_partial_documents_why_the_url_is_not_renamed():
    """The next reader will see "chat" in an href under a "DM" label and reach
    for consistency. The reason it must not be renamed lives beside it."""
    # Arrange
    partial_path = _PARTIAL
    # Act
    source = partial_path.read_text(encoding="utf-8")
    # Assert
    assert "MIGRATION" in source


# EOF
