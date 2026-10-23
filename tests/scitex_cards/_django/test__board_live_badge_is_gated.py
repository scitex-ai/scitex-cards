#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board must not claim LIVE before a read has succeeded.

OPERATOR DIRECTIVE (phone screenshot, 2026-09-17): "LIVE/real data before a
successful fetch" is listed as a defect. Measured on the running board at
390x844: ONE element reading exactly "LIVE" present while ZERO card nodes
exist in the DOM — the badge is static markup, not a statement about a read.

Supersedes the orphan pin drafted on feat/gui-project-board-mvp-20260917
(test__board_live_claim_is_gated.py, never landed): same intent, but pinned
against the CURRENT board family (board_v3.html shell + _board_pane.html +
_board_foot.html) instead of the pre-split template, and against the JS
outcome site (loadGraph) rather than a bare marker string.

THE GATE. ``loadGraph`` has three outcomes: success (cards paint), served
empty/error states (a panel explains), and thrown exceptions (error card).
Only the first means data arrived, so only it may light the badge:
- the badge renders in a NOT-live state (no bare "LIVE" as shipped markup);
- renderDetailsBoard() (_board_foot.html) is the single writer of
  #details-live, flipping to LIVE only when STATE.graph exists and
  reverting to CONNECTING on any failed read.

Source-pin tests over the board's CLIENT source, following the repo's
``test__board_v3_load_states.py`` convention (the badge logic is
browser-side; the pins keep the contract from silently regressing).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_cards._django import views  # noqa: E402

_DJANGO_DIR = Path(views.__file__).resolve().parent

_TEMPLATE = _DJANGO_DIR / "templates" / "scitex_cards" / "board_v3.html"

_BOARD_FAMILY = (
    _TEMPLATE,
    _TEMPLATE.parent / "_board_head.html",
    _TEMPLATE.parent / "_board_pane.html",
    _TEMPLATE.parent / "_board_foot.html",
)


@pytest.fixture
def board_source():
    """The board's client source: the template family as shipped."""
    return "\n".join(p.read_text(encoding="utf-8") for p in _BOARD_FAMILY)


def test_the_live_badge_is_not_live_by_default(board_source):
    """The badge must not ship reading bare "LIVE" — liveness is earned by a
    successful read, not by markup."""
    # Arrange
    source = board_source
    # Act
    shipped_dark = ">LIVE</span>" not in source
    # Assert
    assert shipped_dark


def test_a_successful_read_marks_the_badge_live(board_source):
    """One writer flips the badge, keyed on a read having succeeded.

    renderDetailsBoard() is the single writer of #details-live: LIVE only
    when STATE.graph exists (a read succeeded), CONNECTING otherwise.
    The pin holds both halves — the key (STATE.graph) and the two states
    — so neither the gate nor the dark default can silently regress."""
    # Arrange
    source = board_source
    # Act
    single_writer = source.count('getElementById("details-live")') == 1
    keyed_on_a_read = '!!(STATE.graph)' in source
    live_state = '"LIVE"' in source
    dark_state = '"CONNECTING"' in source
    gated = single_writer and keyed_on_a_read and live_state and dark_state
    # Assert
    assert gated
