#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression pins for the Cards workspace: DM/chat LEFT pane + Board RIGHT.

Operator layout directive 2026-10-19: the board page shows DM/chat in the
left pane and the Board in the right pane, with DM open by DEFAULT, in
light mode as well as dark. Frontend only — no backend/URL change.

When a pin here fails, the layout regressed: do not "fix" this file to
match the new source unless the operator changed the directive. Each pin
names the file that owns the contract so the fix lands in the right place.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import scitex_cards

DJANGO = Path(scitex_cards.__file__).parent / "_django"
TEMPLATE = DJANGO / "templates" / "scitex_cards" / "board_v3.html"
STATIC = DJANGO / "static" / "scitex_cards" / "board_v3"

_HEX_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b")


@pytest.fixture(scope="module")
def board_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dm_js() -> str:
    return (STATIC / "dmPane.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dm_css() -> str:
    return (STATIC / "19-dm-pane.css").read_text(encoding="utf-8")


def test_dm_pane_markup_is_first_in_main(board_text: str):
    """DM aside precedes .board-area in DOM order — that order decides the
    desktop row (DM left) AND the phone stack (DM on top)."""
    assert 'id="dm-pane"' in board_text
    dm_pos = board_text.index('id="dm-pane"')
    board_pos = board_text.index('class="board-area"')
    assert dm_pos < board_pos, "DM pane must precede the Board in DOM order"


def test_template_hash_comments_stay_single_line(board_text: str):
    """Django's `{# #}` is single-line-only in this codebase (PR #173 leak):
    a multi-line span renders its body as literal text into <head>, which
    the browser then shows in the tab. The DM-pane link note violated this
    once — pin the whole template so no edit re-introduces the class."""
    spans = re.findall(r"\{#.*?#\}", board_text, flags=re.S)
    multi = [s for s in spans if "\n" in s]
    assert not multi, f"multi-line {{# #}} leaks into the page: {multi[:2]}"


def test_dm_pane_shell_names_itself_before_the_fetch(board_text: str):
    """Server-rendered shell (first paint) carries the pane identity, the
    collapse control, and the live regions dmPane.js fills."""
    assert 'aria-label="DM' in board_text or "aria-label=\"{% translate 'DM" in board_text
    assert 'id="dm-threads"' in board_text
    assert 'id="dm-thread"' in board_text
    assert "toggleDmPane()" in board_text
    assert 'aria-expanded="true"' in board_text
    assert 'aria-controls="dm-pane"' in board_text


def test_dm_pane_assets_are_linked(board_text: str):
    assert "19-dm-pane.css" in board_text
    assert "dmPane.js" in board_text


def test_dm_js_hits_the_existing_dm_endpoints(dm_js: str):
    assert "/dm/threads" in dm_js
    assert "/dm/thread/" in dm_js
    assert "mark_read" in dm_js  # poll-and-ack, per handlers/dm.py


def test_dm_js_polls_like_the_other_fleet_surfaces(dm_js: str):
    assert "DM_POLL_MS" in dm_js
    assert "30000" in dm_js


def test_dm_js_is_fail_loud_and_lossless(dm_js: str):
    """A failed send names the failure in-pane and restores the draft."""
    assert "draft kept" in dm_js
    assert "dm-pane__error" in dm_js


def test_dm_js_never_injects_server_html(dm_js: str):
    """Every server string lands via textContent — a hostile display name
    cannot inject markup into the operator's board."""
    assert "textContent" in dm_js
    code = re.sub(r"/\*.*?\*/", "", dm_js, flags=re.S)
    code = re.sub(r"//.*", "", code)
    assert "innerHTML" not in code


def test_dm_js_default_open_and_url_contract(dm_js: str):
    assert "dmDefaultOpen" in dm_js
    assert '"stx-cards-dm-open"' in dm_js or "'stx-cards-dm-open'" in dm_js
    assert "dmPeerFromUrl" in dm_js
    assert "dmClosedFromUrl" in dm_js


def test_dm_css_carries_no_private_palette(dm_css: str):
    """Light-mode contract: no hex literals — flipping board vars
    (--bg/--card-bg/--col-bg/--text/--text-muted/--border) plus the
    deliberately-unflipped semantics (--red/--purple). A literal added
    here would stay dark in light mode (the exact defect filed against
    the SDK shell on this card's parent)."""
    bad = _HEX_LITERAL.findall(dm_css)
    assert not bad, f"19-dm-pane.css hard-codes colours: {bad[:5]}"
    for token in ("--col-bg", "--card-bg", "--text", "--border"):
        assert f"var({token}" in dm_css


def test_dm_css_stacks_dm_first_on_phones(dm_css: str):
    assert "900px" in dm_css
    assert "flex-direction: column" in dm_css


def test_dm_css_collapse_keeps_a_rail(dm_css: str):
    """Collapsing shrinks to the header rail instead of display:none — the
    re-open control is never stranded on a closed pane."""
    assert ".collapsed" in dm_css
    assert "44px" in dm_css
