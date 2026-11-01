#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board tab reads "Board (N in last 1h)" (operator 2026-10-24).

N counts cards touched in the last hour, off the same `_newestTs` the
Details > Stats rows use — the tab and the stats are one fact rendered
twice. The base is captured once from the served title so a re-render
never compounds "(3) Board (2 in last 1h)"; zero renders the bare base,
the way the DM tab omits "(0)".

Source-pin tests over the board's CLIENT source, following the repo's
``test__board_live_badge_is_gated.py`` convention (the title logic is
browser-side; the pins keep the contract from silently regressing).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_cards._django import views  # noqa: E402

_FOOT = (
    Path(views.__file__).resolve().parent
    / "templates"
    / "scitex_cards"
    / "_board_foot.html"
)


def _source() -> str:
    assert _FOOT.is_file(), f"_board_foot.html missing: {_FOOT}"
    return _FOOT.read_text(encoding="utf-8")


def test_tab_title_counts_the_last_hour_off_newest_ts() -> None:
    """The count source must be `_newestTs`, not a second definition of new."""
    # Arrange
    source = _source()
    # Act
    idx = source.rfind("in last 1h")
    window = source[max(0, idx - 600) : idx]
    # Assert
    assert idx != -1 and "_newestTs" in window and "3600" in window


def test_tab_title_base_is_a_literal_never_captured() -> None:
    """The base must not be read from document.title.

    On the unified page the DM side rewrites the title on every poll
    ("Cards DM (3)"), so a capture-once read the DM title and the board
    tab compounded it ("Cards DM (3) (22 in last 1h)", 2026-10-31).
    A literal base can never compound and never pollute.
    """
    # Arrange
    source = _source()
    # Act
    literal = '_boardTabBase = "Board"' in source
    # Assert — one compound assertion (PA-307: single assert per test).
    assert literal and "document.title ||" not in source


def test_tab_title_omits_the_count_at_zero() -> None:
    """Zero renders the bare base — no "(0 in last 1h)" state."""
    # Arrange
    source = _source()
    # Act
    idx = source.rfind("in last 1h")
    window = source[max(0, idx - 900) : idx + 200]
    # Assert
    assert idx != -1 and "n > 0" in window


def test_tab_title_updates_on_every_stats_render() -> None:
    """The title must follow each refresh, not just the first paint."""
    # Arrange
    source = _source()
    # Act
    called = "renderBoardTabTitle(all)" in source
    # Assert
    assert called


# EOF
