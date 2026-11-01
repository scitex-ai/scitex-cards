#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The empty-state banner and the rendered list must agree (operator 2026-10-25).

THE DEFECT (live screenshot, compute-03-net): a beige banner says active
filters narrow 8909 tasks to none while the list below renders 965 of
them. Root cause, two halves:

1. `renderSimple` (timeline.js) filtered by time-window only — never the
   filterbar predicate — while the banner counts the filtered `visible`
   set. Same data, two predicates, guaranteed disagreement.
2. The agent/project rasters pull their OWN /timeline feed and ignore the
   filterbar entirely, so a "0 matches" banner over a full raster is the
   opposite lie. The banner now only speaks for views that render
   `visible` (simple, wall, graph, matrix); raster parity is a follow-up,
   carded separately — not silently claimed here.

Source pins (both halves are browser-side JS); the geometric proof —
filter to zero in Simple view, banner shows AND list empties — lives in
the card's evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_cards._django import views  # noqa: E402

_STATIC = Path(views.__file__).resolve().parent / "static" / "scitex_cards"
_TIMELINE = _STATIC / "board_v3" / "timeline.js"
_TEMPLATES = Path(views.__file__).resolve().parent / "templates" / "scitex_cards"
_FOOT = _TEMPLATES / "_board_foot.html"


def test_simple_list_honors_the_filterbar_predicate() -> None:
    """`renderSimple` must apply the shared `passes()` — time window alone
    is what let 965 rows render under a "0 matches" banner."""
    # Arrange
    source = _TIMELINE.read_text(encoding="utf-8")
    # Act
    honours = 'typeof passes === "function" ? passes(r.t) : true' in source
    # Assert
    assert honours


def test_banner_speaks_only_for_views_rendering_visible() -> None:
    """The raster timelines ignore the filterbar, so the banner must stay
    silent there instead of contradicting a full raster."""
    # Arrange
    source = _FOOT.read_text(encoding="utf-8")
    # Act
    gated = "honorsFilters" in source and "tlView ===" in source
    # Assert
    assert gated


def test_banner_keeps_its_empty_store_and_no_filter_cases() -> None:
    """The gate must not swallow the two original cases: a truly empty
    store, and zero matches with no filter active, both hide the banner."""
    # Arrange
    source = _FOOT.read_text(encoding="utf-8")
    # Act
    keeps = "totalCount > 0" in source and "filtersAreActive" in source
    # Assert
    assert keeps


# EOF
