#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""At 390px every header control must be INSIDE the viewport at rest.

THE DEFECT THESE PIN (measured 2026-09-17 on the served board, independently by
scitex-cards-gui's baseline slice and reproduced here): the phone sheet gave the
filter bar `overflow-x: auto` and set no `flex-wrap`, so the bar stayed the flex
default `nowrap` and the phone got a horizontally SCROLLABLE header rather than
a wrapped one — bar 606px inside a 390px viewport, the search field at
x=330..590, the ACTIVE layout toggle at x=374..443. Both were off-screen AT
REST: reachable only by discovering a scroll, which is not the same thing as
being on the screen.

THE MECHANISM, because the shape is the lesson and it repeats across this repo:

  * the sheet CONTAINED the rule it needed — `.filt-search { flex: 1 1 100% }`
    under a comment saying the search wraps to its own row — and that rule could
    not fire, because its PARENT (`.fb-center`) carries `min-width: 260px` in
    01-filterbar.css. That 260px is exactly the width measured on the input: a
    child cannot widen its parent, and a flex floor on a `nowrap` bar pushes
    the child past the edge instead of wrapping it.
  * so the phone sheet asserted a behaviour the computed layout did not have —
    the same class as the light-theme block that answered no `data-theme`, and
    the same class as the accent literals: a stylesheet making a claim nothing
    checked.

These guards are text-level on purpose: the geometric check (every control's
right edge <= viewport) needs a browser, so it is done in the PR's evidence,
while the invariants that make it true — wrap instead of scroll, no min-width
floor, selectors that exist in the markup — are pinned here where CI can see
them.
"""

from __future__ import annotations

import re
from pathlib import Path

from scitex_cards._django import views

_APP = Path(views.__file__).parent
_PHONE_SHEET = _APP / "static" / "scitex_cards" / "board_v3" / "05-responsive.css"
_TEMPLATE = _APP / "templates" / "scitex_cards" / "board_v3.html"


def _without_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)


def _phone_block() -> str:
    """The `@media (max-width: 768px)` body — the phone sheet's only override."""
    css = _without_comments(_PHONE_SHEET.read_text(encoding="utf-8"))
    match = re.search(r"@media \(max-width: 768px\) \{(.*?)\n\}", css, flags=re.DOTALL)
    return match.group(1) if match else ""


def test_the_phone_bar_wraps_instead_of_scrolling():
    """A scrollable header hides the controls a phone user actually needs."""
    # Arrange
    phone = _phone_block()
    # Act
    bar_rule = re.search(r"\.filterbar \{(.*?)\}", phone, flags=re.DOTALL)
    body = bar_rule.group(1) if bar_rule else ""
    # Assert
    assert "flex-wrap: wrap" in body and "overflow-x: auto" not in body, (
        "the phone `.filterbar` rule must wrap and must not be a scroll "
        f"container (got: {body.strip()[:120]!r}) — 'overflow-x: auto' is what "
        "put the search field and the active layout toggle off-screen at rest"
    )


def test_the_phone_sheet_removes_the_search_groups_width_floor():
    """`flex: 1 1 100%` on a child cannot beat a min-width on its parent."""
    # Arrange
    phone = _phone_block()
    # Act
    floor_removed = re.search(
        r"\.fb-center \{[^}]*min-width:\s*0", phone, flags=re.DOTALL
    )
    # Assert
    assert floor_removed, (
        "the phone block no longer sets `min-width: 0` on `.fb-center`; "
        "01-filterbar.css gives that group a 260px floor, which is the width "
        "measured on the input at 390px — the rule that was supposed to make "
        "the search wrap could never fire while the floor was there"
    )


def test_the_phone_sheets_search_selectors_exist_in_the_markup():
    """Dead CSS is a claim, not a rule — catch it where it is written."""
    # Arrange
    template = _TEMPLATE.read_text(encoding="utf-8")
    phone = _phone_block()
    # Act
    missing = [
        name
        for name in ("fb-center", "filt-search-wrap", "filt-search")
        if f".{name}" in phone and name not in template
    ]
    # Assert
    assert not missing, (
        f"the phone sheet styles {missing} but the board template contains no "
        "such class — a rule targeting nothing asserts a behaviour the page "
        "does not have, which is how the 390px defect stayed invisible"
    )


# EOF
