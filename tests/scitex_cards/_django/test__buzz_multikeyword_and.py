#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Buzz free-text takes space-separated keywords that AND-combine.

Prompt-02 (operator 2026-10-22): filters get free buzz filtering and the
placeholder reads "keyword1 keyword2 ...". Bare multi-word queries used to
match as ONE gapped subsequence (spaces skipped), so "blocked paper"
matched only cards holding those letters in that order and missed the
same two words reversed — order decided a keyword search.

What is pinned (source-read, no mocks):

  1. The board template's legacy `fuzzyMatch` splits the bare query on
     whitespace and requires EVERY token to match (AND), each as its own
     subsequence — the same rule `searchQuery.js` `_freeTextMatch`
     applies, so the two paths never disagree (node suite covers the
     behaviour directly).
  2. The Details Text input advertises the contract: placeholder
     "keyword1 keyword2 …".

One assertion per test (PA-307: STX-TQ007) + AAA markers (STX-TQ002).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("django")

_FOOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "_board_foot.html"
)
_PANE = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "_board_pane.html"
)


def _fuzzy_body() -> str:
    # Arrange
    src = _FOOT.read_text(encoding="utf-8")
    # Act
    m = re.search(
        r"function fuzzyMatch\(query, t\) \{(.*?)\n    \}\n\n    // ===",
        src,
        flags=re.DOTALL,
    )
    # Assert
    assert m is not None, "fuzzyMatch body not found in _board_foot.html"
    return m.group(1)


def test_bare_query_splits_into_independent_keywords() -> None:
    """Whitespace separates keywords — no single gapped subsequence."""
    # Arrange
    body = _fuzzy_body()
    # Act
    splits = "split(/\\s+/)" in body
    # Assert — one compound assertion (PA-307: single assert per test).
    assert splits and 'if (c === " ") continue' not in body


def test_every_keyword_must_match() -> None:
    """AND-combine: one vetoing keyword drops the card."""
    # Arrange
    body = _fuzzy_body()
    # Act
    ands = "tokens.every(" in body
    # Assert — one compound assertion (PA-307: single assert per test).
    assert ands


def test_details_text_placeholder_names_keyword_search() -> None:
    """The Text input placeholder reads keyword1 keyword2 …."""
    # Arrange
    html = _PANE.read_text(encoding="utf-8")
    # Act
    m = re.search(r'id="f-text"[^>]*placeholder="([^"]+)"', html, flags=re.DOTALL)
    # Assert — one compound assertion (PA-307: single assert per test).
    assert m is not None and m.group(1) == "keyword1 keyword2 …"


# EOF
