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
import json
import subprocess

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


@pytest.fixture
def filter_status_functions():
    """The shipped functions, evaluated unchanged rather than reimplemented."""
    source = _FOOT.read_text(encoding="utf-8")
    start = source.index("    function _renderFilterMatchStatus(")
    end = source.index('    document.addEventListener("DOMContentLoaded"', start)
    return source[start:end]


@pytest.mark.parametrize(
    "visible,total,filters,honors,expected",
    [
        (0, 0, {"project": "fixture"}, True, {"hidden": True, "detail": ""}),
        (0, 10, {}, True, {"hidden": True, "detail": ""}),
        (0, 10, {"agent": "fixture"}, True, {"hidden": False, "detail": "0 matches"}),
        (2, 10, {"agent": "fixture"}, True, {"hidden": True, "detail": ""}),
        (0, 10, {"agent": "fixture"}, False, {"hidden": True, "detail": ""}),
    ],
)
def test_banner_keeps_empty_store_no_filter_and_nonhonoring_cases(
    filter_status_functions, visible, total, filters, honors, expected
) -> None:
    """Empty/no-filter/nonmatching views hide status; only zero matches show."""
    # Arrange
    script = filter_status_functions + """
      const args = JSON.parse(process.argv[1]);
      const STATE = {filters: args.filters};
      const status = {hidden: false}, detail = {textContent: 'stale text'};
      const document = {getElementById: id => ({
        'filter-match-status': status, 'filter-match-detail': detail})[id]};
      _renderFilterMatchStatus(args.visible, args.total, args.honors);
      console.log(JSON.stringify({hidden: status.hidden, detail: detail.textContent}));
    """
    args = json.dumps({"visible": visible, "total": total, "filters": filters,
                       "honors": honors})
    # Act
    result = subprocess.run(["node", "--input-type=commonjs", "-e", script, args],
                            check=True, capture_output=True, text=True, timeout=10)
    # Assert
    assert json.loads(result.stdout) == expected


# EOF
