#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The Details filter combobox cannot shift the pane (operator 2026-10-25).

THE DEFECT (live screenshot, compute-03-net): opening a Details filter
dropdown moves the details pane left, and the board reads as clipped.
Root cause, two halves, both Cards-owned scaffolding (built around each
<select.filt> in _board_foot.html — the SDK popover itself is untouched):

1. The trigger button (class `filt cb-trigger`, min-width 120px, full
   label text like "agent:scitex-infrastructure-lead") was NOT covered
   by the earlier ellipsis cap, which names `select` and `input.filt-text`
   only. An uncapped trigger widens its row and the pane follows it.
2. The popover container (`span.cb-container`, also ours) had no width
   bound, so a wide menu scrolls the page — which reads as clipped
   board content on the left edge.

Text-level pins (the geometric proof — pane 300→300px, page 1600→1600px
with a 32-char trigger injected, ellipsis confirmed via scrollWidth —
lives in the card's evidence); the invariants that make it true are
pinned here where CI can see them.
"""

from __future__ import annotations

import re
from pathlib import Path

from scitex_cards._django import views

_SHEET = (
    Path(views.__file__).resolve().parent
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "13-details-pane.css"
)


def _without_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)


def _sheet() -> str:
    assert _SHEET.is_file(), f"details sheet missing: {_SHEET}"
    return _without_comments(_SHEET.read_text(encoding="utf-8"))


def test_the_combobox_trigger_ellipsizes_like_the_select_it_replaces() -> None:
    """The cap rule must name `.cb-trigger` — `select` alone leaves the
    SDK-swapped trigger uncapped and the pane shifts with long labels."""
    # Arrange
    css = _sheet()
    # Act
    capped = ".details-row--filter .cb-trigger" in css
    # Assert
    assert capped


def test_the_combobox_wrap_cannot_force_width() -> None:
    # Arrange
    css = _sheet()
    # Act
    m = re.search(r"\.details-row--filter \.cb-wrap \{(.*?)\}", css, flags=re.DOTALL)
    body = m.group(1) if m else ""
    # Assert
    assert m is not None and "max-width" in body and "overflow" in body


def test_the_popover_container_cannot_exceed_the_viewport() -> None:
    # Arrange
    css = _sheet()
    # Act
    m = re.search(
        r"\.details-row--filter \.cb-container \{(.*?)\}", css, flags=re.DOTALL
    )
    body = m.group(1) if m else ""
    # Assert
    assert m is not None and "100vw" in body


# EOF
