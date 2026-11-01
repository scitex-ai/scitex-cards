#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A refused hide/pin answers with its reason, not a bare status.

Operator 2026-10-26: on the hub's SELECT-only mount, Hide answers 403 and
the error bar showed exactly "hidden 403" — the server's typed
`store_read_only` body (reason + credential hint) was thrown away by the
client, which built the message from leaf + status without reading the
response. Same for "pinned 403".

``postThreadFlag`` is the one write path for both flags, so the fix and
the pin live there once: on refusal it reads the body and appends
``refusal.error || refusal.reason``, falling back to leaf + status for
non-JSON refusals.

Source assertions over ``chat_menu.js``, the way
``test__dm_thread_ack_degrades.py`` pins the read-receipt half: the menu
touches the DOM and cannot be required under node.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("django")

_MENU_JS = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
    / "chat_menu.js"
)


def _source() -> str:
    assert _MENU_JS.is_file(), f"chat_menu.js missing: {_MENU_JS}"
    return _MENU_JS.read_text(encoding="utf-8")


def test_the_refused_flag_reads_the_refusal_body() -> None:
    """The typed reason must reach the bar, not just leaf + status."""
    # Arrange
    source = _source()
    # Act
    reads = "r.text()" in source and "refusal.error" in source and "refusal.reason" in source
    # Assert
    assert reads


def test_a_non_json_refusal_still_names_leaf_and_status() -> None:
    """Proxies and empty bodies must not turn a refusal into a parse crash."""
    # Arrange
    source = _source()
    # Act
    falls_back = "leaf.slice(1)" in source and "r.status" in source
    # Assert
    assert falls_back


def test_hide_and_pin_share_the_one_refusal_path() -> None:
    """One write path per flag — the fix applies to both or neither."""
    # Arrange
    source = _source()
    # Act
    shared = 'postHidden' in source and 'postPinned' in source and source.count("postThreadFlag(") >= 3
    # Assert
    assert shared


# EOF
