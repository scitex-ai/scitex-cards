#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Board chrome is monochrome except CTA and card-state colors.

Operator prompt-02 (scitex-stats parity): the board reads monochrome in
general; CTA colors and the card-state palette are the exceptions. Purple
(`var(--purple)` and the `rgb(109,76,173)` / `#8e6cff` / `#7c5cbf` /
`#6d4cad` literals) was the foreign accent across hover rings, focus
rings, washes, badges, counts, hints, selection rings, modal borders
and the touch cue. What stays chromatic on purpose:

- card STATE colors (the STATUS_STYLE SSOT: goal/done/in_progress/
  blocked/cancelled/deferred/failed, consumed as `--status-*` vars and
  the deferred violet) — data, not chrome;
- CTA family (`--text-link`, `--stx-brand` + fallbacks): Send, DWM --on,
  filter chip, + New project, Edit/Save, focus outlines that must stay
  visible (link-blue), unread/brand accents owned elsewhere.

Text-level sweep pin: fails LOUD naming the file, so a reintroduced
purple points at itself. Comments are stripped first, so prose that
merely MENTIONS purple (like this docstring's own history) cannot trip
it — only declarations count.
"""

from __future__ import annotations

import re
from pathlib import Path

from scitex_cards._django import views

_STATIC = (
    Path(views.__file__).resolve().parent / "static" / "scitex_cards" / "board_v3"
)

_PURPLE_PATTERNS = (
    "var(--purple)",
    "109, 76, 173",
    "109,76,173",
    "#8e6cff",
    "#7c5cbf",
    "#6d4cad",
)


def _without_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)


def _sheet_names() -> list[str]:
    return sorted(p.name for p in _STATIC.glob("*.css"))


def test_board_sheets_use_no_purple() -> None:
    """One assertion for the whole sweep (PA-307): the failure message
    names every offending file + line so the fix starts located."""
    # Arrange
    hits: list[str] = []
    # Act
    for name in _sheet_names():
        body = _without_comments((_STATIC / name).read_text(encoding="utf-8"))
        for i, line in enumerate(body.splitlines(), start=1):
            # The `:root` token DEFINITION stays as a safety net (an unknown
            # consumer resolving `var(--purple)` must not fall to unset);
            # only usages count.
            if re.match(r"\s*--purple\s*:", line):
                continue
            lowered = line.lower()
            if any(p in lowered for p in _PURPLE_PATTERNS):
                hits.append(f"{name}:{i}:{line.strip()[:100]}")
    # Assert
    assert not hits, (
        "purple survives in board chrome (monochrome rule, CTA + card "
        f"states exempt): {hits}"
    )


# EOF
