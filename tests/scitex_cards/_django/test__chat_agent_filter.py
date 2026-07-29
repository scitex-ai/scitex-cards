#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The agent list must SHIP a fuzzy filter, and it must survive the poll.

OPERATOR STANDING REQUEST, repeated: 「普通にあいまい検索でフィルタはいつも入れてくだ
さい；scitex-ui にもなければいけない話です」 — a fuzzy-search filter belongs on every
list, and the matcher belongs in scitex-ui rather than being re-typed per page.

The board honoured it; this page did not. Its agent list is one flat column
holding every agent the fleet has ever registered, and the only way to find one
was to scroll. The matching logic itself is tested under node against the real
module (``static/scitex_cards/chat/test_chat_filter.py``); what is pinned HERE is
delivery and structure, because this feature has three ways to be correct and
still absent:

  1. THE INPUT IS NEVER SERVED. It is written in the template rather than created
     by JS precisely so this is assertable against the rendered response.

  2. THE INPUT IS SERVED AND THEN DESTROYED. ``renderAgents`` clears its
     container on every 5s poll. An input inside that container is wiped four
     seconds after the operator types in it — a bug that a screenshot taken
     immediately after typing cannot see. ``#agent-list`` exists to be the wiped
     part, and the assertions below pin that the input is NOT inside it and that
     chat.js renders into it.

  3. THE FILTER SILENTLY UN-APPLIES. Rows are hidden after render, so the same
     poll that rebuilds the list also un-hides everything. The MutationObserver
     is the only thing that makes the filter hold, so it is pinned as a property
     rather than left to review.

Structure is asserted against the RENDERED RESPONSE where the question is "does
the operator get this?", and against module source where the question is "is the
wiring one-directional?" — this repo has no browser in CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402

_CHAT_JS_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
)


@pytest.fixture()
def page() -> str:
    """The rendered /chat page — what the browser is actually served."""
    return views.chat_page(RequestFactory().get("/chat")).content.decode("utf-8")


@pytest.fixture()
def chat_js() -> str:
    return (_CHAT_JS_DIR / "chat.js").read_text(encoding="utf-8")


@pytest.fixture()
def filter_js() -> str:
    return (_CHAT_JS_DIR / "chat_filter.js").read_text(encoding="utf-8")


def _uncommented(source: str) -> str:
    """Source with comments stripped.

    Both modules DOCUMENT the defects they prevent, quoting the shapes they ban.
    A naive scan would match the explanation and call it the implementation —
    the vacuous-guard shape, inverted.
    """
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", " ", source, flags=re.MULTILINE)


# === (1) it is served ======================================================


def test_the_page_serves_a_filter_input(page: str) -> None:
    """Written in the template so its delivery is assertable at all."""
    # Arrange / Act / Assert
    assert 'id="agent-filter"' in page


def test_the_filter_input_is_a_search_field(page: str) -> None:
    """`type=search` is what gives phone keyboards the search affordance."""
    # Arrange
    match = re.search(r"<input[^>]*id=\"agent-filter\"[^>]*>", page, re.DOTALL)
    # Act / Assert
    assert match and 'type="search"' in match.group(0)


def test_the_filter_input_is_labelled(page: str) -> None:
    """It has no visible <label>; without this it is an unnamed box to a reader."""
    # Arrange
    match = re.search(r"<input[^>]*id=\"agent-filter\"[^>]*>", page, re.DOTALL)
    # Act / Assert
    assert match and 'aria-label="Filter agents"' in match.group(0)


def test_the_page_serves_the_filter_module(page: str) -> None:
    """Markup with no behaviour is a dead control."""
    # Arrange / Act / Assert
    assert "chat/chat_filter.js" in page


def test_the_page_serves_the_scitex_ui_matcher(page: str) -> None:
    """The whole point: the matcher is base's, so search means one thing here."""
    # Arrange / Act / Assert
    assert "scitex_ui/js/app/combobox.js" in page


def test_the_matcher_loads_before_the_filter_module(page: str) -> None:
    """Both are `defer`, so document order IS execution order."""
    # Arrange / Act
    combobox = page.index("scitex_ui/js/app/combobox.js")
    chat_filter = page.index("chat/chat_filter.js")
    # Assert
    assert combobox < chat_filter


# === (2) it survives the poll ==============================================


def test_the_page_serves_a_separate_list_container(page: str) -> None:
    """`#agent-list` is the part `renderAgents` is allowed to clear."""
    # Arrange / Act / Assert
    assert 'id="agent-list"' in page


def test_the_filter_input_is_not_inside_the_rebuilt_list(page: str) -> None:
    """The defect this whole structure exists to prevent.

    Inside `#agent-list`, the input is destroyed by the next 5s poll — the
    operator's query vanishes mid-typing and the page looks like it "reset
    itself". Ordering the two ids in the document is enough to see it: the
    input must be served BEFORE the list opens.
    """
    # Arrange / Act
    input_at = page.index('id="agent-filter"')
    list_at = page.index('id="agent-list"')
    # Assert
    assert input_at < list_at


def test_chat_js_renders_into_the_list_not_the_nav(chat_js: str) -> None:
    """`$agents` must resolve to `#agent-list` — the clearable half."""
    # Arrange
    code = _uncommented(chat_js)
    # Act / Assert
    assert 'var $agents = document.getElementById("agent-list");' in code


def test_chat_js_still_slides_the_nav_as_the_drawer(chat_js: str) -> None:
    """Sliding `#agent-list` instead would strand the filter row off-drawer."""
    # Arrange
    code = _uncommented(chat_js)
    # Act / Assert
    assert 'var $agentsPane = document.getElementById("agents");' in code
    assert re.search(r"panel:\s*\$agentsPane", code)


def test_chat_js_mounts_the_filter_over_input_and_list(chat_js: str) -> None:
    """Mounted with the two seams it needs and nothing else."""
    # Arrange
    code = _uncommented(chat_js)
    # Act / Assert
    assert re.search(
        r"ChatFilter\.mount\(\{\s*input:\s*\$agentFilter,\s*list:\s*\$agents",
        code,
    )


def test_chat_js_mounting_the_filter_is_optional(chat_js: str) -> None:
    """Same contract as ChatDrawer/ChatMenu — a missing module degrades."""
    # Arrange
    code = _uncommented(chat_js)
    # Act / Assert
    assert "if (window.ChatFilter)" in code


# === (3) it holds across a rebuild =========================================


#
# WHAT THESE CAN AND CANNOT SEE — read before trusting them. They are STRUCTURAL
# pins on the source, because CI here has no browser and no DOM implementation:
# nothing in this repo can actually rebuild a list and watch the filter re-apply.
# That gap is not hypothetical. The first version of this section asserted only
# that the strings "MutationObserver" and "observe(list, {childList: true"
# appeared, and a mutation probe walked straight through it: changing the guard
# to `null && scope.MutationObserver` disabled the observer completely, left both
# strings in place, and the tests stayed green while a jsdom run of the real page
# showed the filter silently un-applying on the next repaint (all six agents back
# on screen with "wtg" still in the box).
#
# So they pin the guard EXPRESSION rather than the vocabulary. A substring scan
# asks "is the word there?"; that is not the question. The behaviour itself was
# verified out-of-band under jsdom against the rendered page, and the mutation
# probe below is the honest statement of how much of it these can hold.


def test_the_filter_reapplies_when_the_list_is_rebuilt(filter_js: str) -> None:
    """The observer must be constructed from an UNCONDITIONAL capability check.

    Pinned as the exact expression: anything else in that guard (a flag, a
    `false &&`, a config lookup) is a way for the observer to never be built
    while every keyword a looser scan looks for stays in the file.
    """
    # Arrange
    code = _uncommented(filter_js)
    # Act / Assert
    assert re.search(
        r"var Observer\s*=\s*scope\s*&&\s*scope\.MutationObserver\s*;",
        code,
    ), "the observer's capability check is no longer `scope && scope.MutationObserver`"


def test_the_observer_is_actually_started_on_the_list(filter_js: str) -> None:
    """Constructing one and never calling `observe` watches nothing."""
    # Arrange
    code = _uncommented(filter_js)
    # Act / Assert
    assert re.search(r"observer\s*=\s*new Observer\(", code)
    assert re.search(r"observer\.observe\(\s*list\s*,\s*\{\s*childList:\s*true", code)


def test_the_observer_reapplies_the_filter(filter_js: str) -> None:
    """An observer whose callback does not re-filter is decoration."""
    # Arrange
    code = _uncommented(filter_js)
    # Act / Assert
    assert re.search(
        r"new Observer\(function \(\) \{\s*apply\(\);\s*\}\)",
        code,
    )


def test_the_observer_watches_only_childlist(filter_js: str) -> None:
    """The handler writes `style` on children; observing attributes would loop."""
    # Arrange
    code = _uncommented(filter_js)
    # Act / Assert
    assert "attributes: true" not in code
    assert "subtree: true" not in code


# === the matcher is consumed, not copied ===================================


def test_the_module_reads_the_matcher_off_scitex_ui(filter_js: str) -> None:
    """A private subsequence matcher would make this page disagree with the board."""
    # Arrange
    code = _uncommented(filter_js)
    # Act / Assert
    assert "Combobox.fuzzyMatch" in code
