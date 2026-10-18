#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The selected-card highlight wears square corners (operator CCT4368).

The rectangular card highlight is ``.tl-card--selected`` (the multi-select
highlight for the simple per-task list, toggled by timelineSelect.js). It
inherits ``border-radius: 8px`` from ``.tl-card``; the operator ruled the
rounded highlight looks bad and ordered straight/square corners.

These pins read the stylesheet text only: no store, no PostgreSQL.
"""

from __future__ import annotations

import re
from pathlib import Path

_CSS = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "09-timeline.css"
)


def _selected_block() -> str:
    # Arrange + Act in the helper: read the file, cut the selected rule.
    text = _CSS.read_text(encoding="utf-8")
    match = re.search(
        r"\.tl-card--selected\s*\{(.*?)\}", text, re.DOTALL
    )
    # Assert
    assert match is not None
    return match.group(1)


def test_selected_rule_sets_square_corners():
    # Arrange
    block = _selected_block()
    # Act
    found = re.search(r"border-radius\s*:\s*0\s*;", block)
    # Assert
    assert found is not None


def test_selected_rule_names_the_operator_order():
    # Arrange
    text = _CSS.read_text(encoding="utf-8")
    # Act
    anchored = "CCT4368" in text
    # Assert
    assert anchored


def test_base_card_rule_is_present():
    # Arrange: the order covers the highlight only, not the resting card.
    text = _CSS.read_text(encoding="utf-8")
    # Act
    match = re.search(r"\.tl-card\s*\{(.*?)\}", text, re.DOTALL)
    # Assert
    assert match is not None


def test_base_card_keeps_its_rounding():
    # Arrange: the order covers the highlight only, not the resting card.
    text = _CSS.read_text(encoding="utf-8")
    # Act
    match = re.search(r"\.tl-card\s*\{(.*?)\}", text, re.DOTALL)
    # Assert
    assert "border-radius: 8px" in (match.group(1) if match else "")


def test_pins_are_nonvacuous():
    # Arrange
    text = _CSS.read_text(encoding="utf-8")
    # Act
    mentions = text.count("tl-card--selected")
    # Assert
    assert mentions >= 1
