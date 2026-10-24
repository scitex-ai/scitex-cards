#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""At phone widths the board canvas must keep a floor, or it is not shown.

THE DEFECT (measured 2026-10-24 on the served board at 390x844, live DOM):
`#columns` renders 390x16 with the 680px timeline raster clipped to a 2px
`.tl-scroll` — lanes, axis and dots exist in the DOM but paint into a slit,
so the phone shows a black void where the board should be. Desktop is fine
(1382x765) because the row-direction layout stretches the canvas tall.

THE MECHANISM: the phone sheet stacks `.main` vertically, and the
fixed-height panels (filterbar, Legend, Details, Board-settings, Stats) eat
its whole height. `.board-area` — `flex: 1` with a 0 basis — settles at 0px,
and `.tl-wrap` / `.tl-scroll` carry no flex to reclaim any of it.

These guards are text-level on purpose, like
`test__board_header_fits_a_phone.py`: the geometric proof (390x422 canvas,
446x357 scroll container with the fix injected) lives in the card's
evidence, while the invariants that make it true — a min-height floor on
`.board-area` plus flex reclaim on `.tl-wrap` / `.tl-scroll` inside the
phone sheet — are pinned here where CI can see them.
"""

from __future__ import annotations

import re
from pathlib import Path

from scitex_cards._django import views

_APP = Path(views.__file__).parent
_PHONE_SHEET = _APP / "static" / "scitex_cards" / "board_v3" / "05-responsive.css"


def _without_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)


def _phone_block() -> str:
    """The first `@media (max-width: 768px)` body — the phone overrides."""
    css = _without_comments(_PHONE_SHEET.read_text(encoding="utf-8"))
    match = re.search(r"@media \(max-width: 768px\) \{(.*?)\n\}", css, flags=re.DOTALL)
    return match.group(1) if match else ""


def _rule_body(phone: str, selector: str) -> str:
    """The declaration body for `selector` inside the phone block."""
    match = re.search(re.escape(selector) + r" \{(.*?)\}", phone, flags=re.DOTALL)
    return match.group(1) if match else ""


def test_the_phone_canvas_keeps_a_floor() -> None:
    """`.board-area` must not settle at 0px when the stacked panels eat `.main`.

    Two classes of specificity, not one: 13-details-pane.css (linked later)
    sets `.board-area { min-height: 0 }`, so a bare floor would lose the
    cascade and the phone would keep its 390x16 slit.
    """
    # Arrange
    phone = _phone_block()
    # Act
    body = _rule_body(phone, ".main .board-area")
    # Assert
    assert "min-height" in body, (
        "the phone sheet must floor `.main .board-area` (a bare "
        "`.board-area` loses to 13-details-pane.css; measured 390x16 "
        f"without the floor — got: {body.strip()[:120]!r})"
    )


def test_the_phone_raster_reclaims_the_floor() -> None:
    """The floor is dead unless `.tl-wrap` / `.tl-scroll` flex into it."""
    # Arrange
    phone = _phone_block()
    # Act
    wrap = _rule_body(phone, ".tl-wrap")
    scroll = _rule_body(phone, ".tl-scroll")
    # Assert
    assert "flex" in wrap and "flex" in scroll, (
        "the phone sheet must let `.tl-wrap` and `.tl-scroll` flex into the "
        f"canvas floor (got wrap={wrap.strip()[:80]!r} scroll={scroll.strip()[:80]!r})"
    )


# EOF
