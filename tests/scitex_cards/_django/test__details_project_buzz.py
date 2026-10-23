#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Details-pane PROJECT buzz filter: the Combobox layer must be discoverable.

Operator 2026-10-23 (live screenshot of /apps/cards/#board): the PROJECT row
in Details > Filters rendered as a plain closed option list with NO free-text
filter visible. The SDK Combobox layer (overlay + autofocused search input +
fuzzy match) exists in code and binds all six filter selects — but nothing
pinned that the binding is DISCOVERABLE: a trigger that reads as a plain
value label gives the operator no affordance that typing narrows the list.

What is pinned (source-read, no mocks):

  1. The wrap exposes a search affordance on the trigger itself (a title
     naming free-text search), so the closed control advertises that typing
     narrows the list.
  2. The Combobox is constructed with a "Search …" placeholder — the input
     the operator meets on open names its own purpose.
  3. The overlay container is mounted as a sibling that the SDK positions
     absolutely (overlay, never a pane-shifting inline list).
  4. The binding covers f-project (the reported row) and re-runs whenever
     populateFilters() pushes fresh options.

One assertion per test (PA-307: STX-TQ007) + AAA markers (STX-TQ002).
"""

from __future__ import annotations

import re
from pathlib import Path

_FOOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "_board_foot.html"
)


def _wrap_body() -> str:
    # Arrange
    src = _FOOT.read_text(encoding="utf-8")
    # Act
    m = re.search(
        r"function _wrapSelectAsCombobox\(selectId\) \{(.*?)wrap\.remove\(\);\n      \}\n    \}",
        src,
        flags=re.DOTALL,
    )
    # Assert
    assert m is not None, "_wrapSelectAsCombobox body not found in _board_foot.html"
    return m.group(1)


def test_project_buzz_trigger_advertises_free_text_search() -> None:
    """The closed trigger names free-text search in its own title."""
    # Arrange
    body = _wrap_body()
    # Act
    titled = 'trigger.title' in body or 'trigger.setAttribute("title"' in body
    # Assert — one compound assertion (PA-307: single assert per test).
    assert titled, (
        "combobox trigger carries no title: the closed PROJECT row reads as "
        "a plain value label with no affordance that typing narrows the list"
    )


def test_project_buzz_input_names_its_purpose() -> None:
    """The Combobox search input placeholder names searching."""
    # Arrange
    body = _wrap_body()
    # Act
    ph = re.search(r'placeholder:\s*"([^"]+)"', body)
    # Assert — one compound assertion (PA-307: single assert per test).
    assert ph is not None and "Search" in ph.group(1), (
        "combobox placeholder missing or unnamed: the input the operator "
        "meets on open must name its own purpose ('Search …')"
    )


def test_project_buzz_mounts_an_overlay_container() -> None:
    """The menu mounts in a sibling container the SDK positions absolutely."""
    # Arrange
    body = _wrap_body()
    # Act
    mounted = 'className = "cb-container"' in body or "cb-container" in body
    # Assert — one compound assertion (PA-307: single assert per test).
    assert mounted, (
        "no cb-container mount: without the sibling container the Combobox "
        "has nowhere to render its overlay menu"
    )


def test_project_buzz_binding_covers_f_project_and_rebinds() -> None:
    """All surviving filter selects are bound, and populateFilters re-binds.

    f-project is DELIBERATELY absent (operator 2026-10-23 single-picker
    rule, #1140): the header picker owns project, so the Details row is
    gone. The buzz affordance covers the remaining selects — pin those,
    not the removed one."""
    # Arrange
    src = _FOOT.read_text(encoding="utf-8")
    # Act
    ids = re.search(r"FILTER_IDS = \[(.*?)\]", src, flags=re.DOTALL)
    rebind = "attachCombobox()" in src
    # Assert — one compound assertion (PA-307: single assert per test).
    assert (
        ids is not None
        and '"f-project"' not in ids.group(1)
        and '"f-host"' in ids.group(1)
        and '"f-agent"' in ids.group(1)
        and rebind
    ), "FILTER_IDS must bind the surviving selects (never the removed f-project) with a populateFilters rebind"
