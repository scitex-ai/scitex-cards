#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression pins for the Cards workspace: DM/chat LEFT pane + Board RIGHT.

Operator layout directive 2026-10-19: the board page shows DM/chat in the
left pane and the Board in the right pane, with DM open by DEFAULT, in
light mode as well as dark. Frontend only — no backend/URL change.

When a pin here fails, the layout regressed: do not "fix" this file to
match the new source unless the operator changed the directive. Each pin
names the file that owns the contract so the fix lands in the right place.

One assertion per test + AAA markers (STX-TQ002/TQ007, per PA-307): each
test below asserts exactly one outcome; multi-fact contracts are split
into one test per fact.
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


def _strip_js_comments(code: str) -> str:
    """Return JS with block/line comments removed (string content kept)."""
    no_block = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return re.sub(r"//.*", "", no_block)


def _css_uses_var(css: str, token: str) -> bool:
    """Whether the stylesheet references a board variable for a token."""
    return f"var({token}" in css


def test_dm_pane_markup_exists_in_main(board_text: str):
    """The DM aside is server-rendered inside board_v3.html."""
    # Arrange
    markup = board_text
    # Act
    present = 'id="dm-pane"' in markup
    # Assert
    assert present is True, "board_v3.html lost the dm-pane aside"


def test_dm_pane_markup_is_first_in_main(board_text: str):
    """DM aside precedes .board-area in DOM order — that order decides the
    desktop row (DM left) AND the phone stack (DM on top)."""
    # Arrange
    markup = board_text
    # Act
    dm_pos = markup.index('id="dm-pane"')
    board_pos = markup.index('class="board-area"')
    # Assert
    assert dm_pos < board_pos, "DM pane must precede the Board in DOM order"


def test_template_hash_comments_stay_single_line(board_text: str):
    """Django's `{# #}` is single-line-only in this codebase (PR #173 leak):
    a multi-line span renders its body as literal text into <head>, which
    the browser then shows in the tab. The DM-pane link note violated this
    once — pin the whole template so no edit re-introduces the class."""
    # Arrange
    markup = board_text
    # Act
    spans = re.findall(r"\{#.*?#\}", markup, flags=re.S)
    multi = [s for s in spans if "\n" in s]
    # Assert
    assert not multi, f"multi-line {{# #}} leaks into the page: {multi[:2]}"


def test_dm_pane_shell_labels_itself(board_text: str):
    """First paint carries the pane identity dmPane.js fills."""
    # Arrange
    markup = board_text
    # Act
    labelled = 'aria-label="DM' in markup or "aria-label=\"{% translate 'DM" in markup
    # Assert
    assert labelled is True, "board_v3.html dm-pane shell lost its label"


def test_dm_pane_shell_has_threads_region(board_text: str):
    """First paint carries the live thread-list region."""
    # Arrange
    markup = board_text
    # Act
    present = 'id="dm-threads"' in markup
    # Assert
    assert present is True, "board_v3.html lost the dm-threads region"


def test_dm_pane_shell_has_thread_region(board_text: str):
    """First paint carries the live open-thread region."""
    # Arrange
    markup = board_text
    # Act
    present = 'id="dm-thread"' in markup
    # Assert
    assert present is True, "board_v3.html lost the dm-thread region"


def test_dm_pane_shell_has_collapse_control(board_text: str):
    """First paint carries the collapse control."""
    # Arrange
    markup = board_text
    # Act
    present = "toggleDmPane()" in markup
    # Assert
    assert present is True, "board_v3.html lost the toggleDmPane control"


def test_dm_pane_shell_opens_by_default(board_text: str):
    """DM is open by DEFAULT per the operator directive."""
    # Arrange
    markup = board_text
    # Act
    present = 'aria-expanded="true"' in markup
    # Assert
    assert present is True, "board_v3.html dm-pane no longer opens by default"


def test_dm_pane_shell_points_at_the_pane(board_text: str):
    """The collapse control declares which pane it controls."""
    # Arrange
    markup = board_text
    # Act
    present = 'aria-controls="dm-pane"' in markup
    # Assert
    assert present is True, "board_v3.html lost aria-controls=dm-pane"


def test_dm_pane_links_the_dm_css(board_text: str):
    # Arrange
    markup = board_text
    # Act
    linked = "19-dm-pane.css" in markup
    # Assert
    assert linked is True, "board_v3.html no longer links 19-dm-pane.css"


def test_dm_pane_links_the_dm_js(board_text: str):
    # Arrange
    markup = board_text
    # Act
    linked = "dmPane.js" in markup
    # Assert
    assert linked is True, "board_v3.html no longer links dmPane.js"


def test_dm_js_lists_threads_via_the_dm_endpoint(dm_js: str):
    # Arrange
    code = dm_js
    # Act
    hits = "/dm/threads" in code
    # Assert
    assert hits is True, "dmPane.js no longer hits /dm/threads"


def test_dm_js_opens_a_thread_via_the_dm_endpoint(dm_js: str):
    # Arrange
    code = dm_js
    # Act
    hits = "/dm/thread/" in code
    # Assert
    assert hits is True, "dmPane.js no longer hits /dm/thread/"


def test_dm_js_acks_reads_via_mark_read(dm_js: str):
    """Poll-and-ack, per handlers/dm.py."""
    # Arrange
    code = dm_js
    # Act
    hits = "mark_read" in code
    # Assert
    assert hits is True, "dmPane.js no longer acks via mark_read"


def test_dm_js_defines_the_poll_interval(dm_js: str):
    # Arrange
    code = dm_js
    # Act
    present = "DM_POLL_MS" in code
    # Assert
    assert present is True, "dmPane.js lost the DM_POLL_MS interval"


def test_dm_js_polls_on_the_fleet_cadence(dm_js: str):
    """Same 30s cadence as the other fleet surfaces."""
    # Arrange
    code = dm_js
    # Act
    present = "30000" in code
    # Assert
    assert present is True, "dmPane.js left the 30s fleet poll cadence"


def test_dm_js_names_send_failure_in_pane(dm_js: str):
    """A failed send names the failure in-pane."""
    # Arrange
    code = dm_js
    # Act
    present = "dm-pane__error" in code
    # Assert
    assert present is True, "dmPane.js lost the in-pane send-failure slot"


def test_dm_js_restores_the_draft_after_failure(dm_js: str):
    """A failed send restores the draft — lossless."""
    # Arrange
    code = dm_js
    # Act
    present = "draft kept" in code
    # Assert
    assert present is True, "dmPane.js no longer restores the draft on failure"


def test_dm_js_renders_server_strings_as_text(dm_js: str):
    """Every server string lands via textContent — a hostile display name
    cannot inject markup into the operator's board."""
    # Arrange
    code = dm_js
    # Act
    present = "textContent" in code
    # Assert
    assert present is True, "dmPane.js stopped rendering server strings as text"


def test_dm_js_has_no_inner_html_sink(dm_js: str):
    """No innerHTML outside comments — the injection sink stays closed."""
    # Arrange
    code = dm_js
    # Act
    executable = _strip_js_comments(code)
    clean = "innerHTML" not in executable
    # Assert
    assert clean is True, "dmPane.js gained an innerHTML sink"


def test_dm_js_reads_the_default_open_flag(dm_js: str):
    # Arrange
    code = dm_js
    # Act
    present = "dmDefaultOpen" in code
    # Assert
    assert present is True, "dmPane.js lost the dmDefaultOpen flag"


def test_dm_js_uses_the_open_state_key(dm_js: str):
    # Arrange
    code = dm_js
    # Act
    present = '"stx-cards-dm-open"' in code or "'stx-cards-dm-open'" in code
    # Assert
    assert present is True, "dmPane.js lost the stx-cards-dm-open state key"


def test_dm_js_reads_the_peer_from_the_url(dm_js: str):
    # Arrange
    code = dm_js
    # Act
    present = "dmPeerFromUrl" in code
    # Assert
    assert present is True, "dmPane.js lost the dmPeerFromUrl contract"


def test_dm_js_reads_the_closed_flag_from_the_url(dm_js: str):
    # Arrange
    code = dm_js
    # Act
    present = "dmClosedFromUrl" in code
    # Assert
    assert present is True, "dmPane.js lost the dmClosedFromUrl contract"


def test_dm_css_carries_no_hex_literals(dm_css: str):
    """Light-mode contract: no hex literals — a literal added here would
    stay dark in light mode (the exact defect filed against the SDK shell
    on this card's parent)."""
    # Arrange
    css = dm_css
    # Act
    bad = _HEX_LITERAL.findall(css)
    # Assert
    assert not bad, f"19-dm-pane.css hard-codes colours: {bad[:5]}"


def test_dm_css_flips_columns_via_board_var(dm_css: str):
    # Arrange
    css = dm_css
    # Act
    uses = _css_uses_var(css, "--col-bg")
    # Assert
    assert uses is True, "19-dm-pane.css stopped flipping via --col-bg"


def test_dm_css_flips_cards_via_board_var(dm_css: str):
    # Arrange
    css = dm_css
    # Act
    uses = _css_uses_var(css, "--card-bg")
    # Assert
    assert uses is True, "19-dm-pane.css stopped flipping via --card-bg"


def test_dm_css_flips_text_via_board_var(dm_css: str):
    # Arrange
    css = dm_css
    # Act
    uses = _css_uses_var(css, "--text")
    # Assert
    assert uses is True, "19-dm-pane.css stopped flipping via --text"


def test_dm_css_flips_borders_via_board_var(dm_css: str):
    # Arrange
    css = dm_css
    # Act
    uses = _css_uses_var(css, "--border")
    # Assert
    assert uses is True, "19-dm-pane.css stopped flipping via --border"


def test_dm_css_has_the_phone_breakpoint(dm_css: str):
    # Arrange
    css = dm_css
    # Act
    present = "900px" in css
    # Assert
    assert present is True, "19-dm-pane.css lost the 900px phone breakpoint"


def test_dm_css_stacks_vertically_on_phones(dm_css: str):
    """DM stacks first (on top) on phones."""
    # Arrange
    css = dm_css
    # Act
    present = "flex-direction: column" in css
    # Assert
    assert present is True, "19-dm-pane.css lost the phone column stack"


def test_dm_css_collapse_uses_a_class(dm_css: str):
    """Collapsing shrinks to the header rail instead of display:none — the
    re-open control is never stranded on a closed pane."""
    # Arrange
    css = dm_css
    # Act
    present = ".collapsed" in css
    # Assert
    assert present is True, "19-dm-pane.css lost the .collapsed state"


def test_dm_css_collapse_keeps_a_rail(dm_css: str):
    """The collapsed pane keeps a 44px header rail."""
    # Arrange
    css = dm_css
    # Act
    present = "44px" in css
    # Assert
    assert present is True, "19-dm-pane.css lost the 44px collapse rail"
