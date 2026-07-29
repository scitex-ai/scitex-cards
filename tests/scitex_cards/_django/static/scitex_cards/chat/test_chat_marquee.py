#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for drag-rectangle selection and forward provenance (no mocks).

These ``require()`` the shipped ``chat_actions.js`` and run the REAL geometry
under node, the same arrangement as ``test_chat_actions.py``. There is
deliberately no hand-ported copy of the logic here.

WHAT IS AND IS NOT PINNED HERE, stated plainly. The pure decisions — which
messages a rectangle catches, in what order, and what a forwarded body says —
are exercised below. The DOM half (a drag that starts on text must not enter
selection mode) is NOT: this repo ships no jsdom, and hand-rolling a DOM stub
to stand in for one would be a mock. That half was verified by driving the
live board with a real browser instead, which is also what the covering card
demanded ("VERIFY BY DOING, not by unit test alone").
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_CHAT_JS_DIR = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
)
JS_FILE = _CHAT_JS_DIR / "chat_actions.js"
MARQUEE_FILE = _CHAT_JS_DIR / "chat_marquee.js"


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _call(expr: str) -> object:
    """Evaluate a ChatActions expression against the real module."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = (
        f"const ChatActions = require({json.dumps(str(JS_FILE))});\n"
        f"console.log(JSON.stringify({expr}));"
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip())


# Three stacked messages, as the thread paints them: top, middle, bottom.
_BOXES = json.dumps(
    [
        {"id": "m_top", "rect": {"left": 10, "top": 0, "right": 300, "bottom": 40}},
        {"id": "m_mid", "rect": {"left": 10, "top": 50, "right": 300, "bottom": 90}},
        {"id": "m_bot", "rect": {"left": 10, "top": 100, "right": 300, "bottom": 140}},
    ]
)


# === rectangle geometry ====================================================


def test_a_drag_up_and_left_spans_the_same_box_as_the_reverse_drag():
    # Arrange
    down_right = "ChatActions.marqueeRect({x:10,y:10},{x:90,y:70})"
    up_left = "ChatActions.marqueeRect({x:90,y:70},{x:10,y:10})"
    # Act
    same = _call(f"[{down_right}, {up_left}]")
    # Assert
    assert same[0] == same[1]


def test_a_rectangle_grazing_a_tall_message_still_catches_it():
    # Arrange
    band = "{left:0,top:60,right:400,bottom:62}"
    tall = "{left:10,top:0,right:300,bottom:500}"
    # Act
    caught = _call(f"ChatActions.rectsOverlap({band}, {tall})")
    # Assert
    assert caught is True


def test_a_rectangle_that_misses_a_message_does_not_catch_it():
    # Arrange
    away = "{left:0,top:900,right:400,bottom:950}"
    msg = "{left:10,top:0,right:300,bottom:40}"
    # Act
    caught = _call(f"ChatActions.rectsOverlap({away}, {msg})")
    # Assert
    assert caught is False


def test_a_rectangle_covering_two_messages_selects_exactly_those_two():
    # Arrange
    rect = "{left:0,top:30,right:400,bottom:95}"
    # Act
    ids = _call(f"ChatActions.idsWithin({_BOXES}, {rect})")
    # Assert
    assert ids == ["m_top", "m_mid"]


def test_a_rectangle_dragged_upward_returns_messages_in_thread_order():
    # Arrange — the drag ends ABOVE where it began, so the pointer met the
    # bottom message first; the result must still read top-to-bottom.
    rect = "ChatActions.marqueeRect({x:200,y:130},{x:20,y:10})"
    # Act
    ids = _call(f"ChatActions.idsWithin({_BOXES}, {rect})")
    # Assert
    assert ids == ["m_top", "m_mid", "m_bot"]


def test_a_rectangle_touching_nothing_selects_nothing():
    # Arrange
    rect = "{left:0,top:900,right:10,bottom:910}"
    # Act
    ids = _call(f"ChatActions.idsWithin({_BOXES}, {rect})")
    # Assert
    assert ids == []


# === adding to an existing selection =======================================


def test_a_second_drag_adds_to_the_first_drags_catch():
    # Arrange
    first = '["m_top"]'
    second = '["m_bot"]'
    # Act
    merged = _call(f"ChatActions.mergeIds({first}, {second})")
    # Assert
    assert merged == ["m_top", "m_bot"]


def test_redragging_over_an_already_selected_message_does_not_duplicate_it():
    # Arrange
    held = '["m_top","m_mid"]'
    again = '["m_mid","m_bot"]'
    # Act
    merged = _call(f"ChatActions.mergeIds({held}, {again})")
    # Assert
    assert merged == ["m_top", "m_mid", "m_bot"]


# === forward provenance ====================================================

_MSG = {
    "id": "m_001",
    "from": "scitex-dev",
    "body": "release is green",
    "ts": "2026-07-29T09:00:00Z",
}


def test_a_forward_names_the_original_recipient_as_well_as_the_sender():
    # Arrange
    message = json.dumps(_MSG)
    # Act
    body = _call(f"ChatActions.forwardBody({message}, 'operator')")
    # Assert
    assert body.startswith(
        "[forwarded from scitex-dev to operator, 2026-07-29T09:00:00Z]"
    )


def test_a_forward_still_carries_the_original_text():
    # Arrange
    message = json.dumps(_MSG)
    # Act
    body = _call(f"ChatActions.forwardBody({message}, 'operator')")
    # Assert
    assert body.endswith("release is green")


def test_a_forward_with_no_known_recipient_omits_the_to_rather_than_guessing():
    # Arrange
    message = json.dumps(_MSG)
    # Act
    body = _call(f"ChatActions.forwardBody({message}, '')")
    # Assert
    assert body.startswith("[forwarded from scitex-dev, 2026-07-29T09:00:00Z]")


def test_the_recipient_of_a_message_the_viewer_wrote_is_the_thread_peer():
    # Arrange
    mine = json.dumps({**_MSG, "from": "operator"})
    # Act
    to = _call(f"ChatActions.forwardOriginalTo({mine}, 'scitex-dev', 'operator')")
    # Assert
    assert to == "scitex-dev"


def test_the_recipient_of_a_message_the_peer_wrote_is_the_viewer():
    # Arrange
    theirs = json.dumps(_MSG)
    # Act
    to = _call(f"ChatActions.forwardOriginalTo({theirs}, 'scitex-dev', 'operator')")
    # Assert
    assert to == "operator"


def test_forwarding_a_forward_that_names_a_recipient_does_not_stack_a_banner():
    # Arrange
    once = json.dumps(
        {**_MSG, "body": "[forwarded from a to b, 2026-07-01T00:00:00Z]\nhello"}
    )
    # Act
    twice = _call(f"ChatActions.forwardBody({once}, 'operator')")
    # Assert
    assert twice.count("[forwarded from") == 1


def test_a_banner_written_without_a_recipient_is_still_recognised_as_forwarded():
    # Arrange — the shape claude-code-telegrammer writes, and every forward
    # this board sent before the recipient existed.
    legacy = "[forwarded from someone, 2026-07-01T00:00:00Z]\nhello"
    # Act
    seen = _call(f"ChatActions.isForwarded({json.dumps(legacy)})")
    # Assert
    assert seen is True


# === the start position decides ============================================

# A real `closest`: it WALKS a parent chain and matches any of the comma
# separated selectors, rather than replaying a recorded answer. A canned stub
# would let the predicate pass by agreeing with the test instead of by working.
_DOM = """
function node(classes, parent) {
  var self = {
    classes: classes || [],
    parent: parent || null,
    closest: function (sel) {
      var wanted = sel.split(",").map(function (s) { return s.trim(); });
      var at = self;
      while (at) {
        for (var i = 0; i < wanted.length; i += 1) {
          var w = wanted[i];
          if (w.charAt(0) === "." && at.classes.indexOf(w.slice(1)) !== -1)
            return at;
          if (w.charAt(0) !== "." && at.classes.indexOf(w) !== -1) return at;
        }
        at = at.parent;
      }
      return null;
    },
  };
  return self;
}
"""


def _marquee(js: str) -> object:
    """Evaluate against the real chat_marquee.js, with a walking DOM."""
    assert MARQUEE_FILE.is_file(), f"module under test missing: {MARQUEE_FILE}"
    script = (
        f"const M = require({json.dumps(str(MARQUEE_FILE))}).ChatMarquee;\n"
        + _DOM
        + f"console.log(JSON.stringify({js}));"
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip())


def test_a_drag_starting_on_message_text_leaves_the_text_selection_alone():
    # Arrange — the operator's case 2: pointer went down inside the bubble.
    target = 'node(["bubble"], node(["msg"], node(["messages"])))'
    # Act
    keeps_native = _marquee(f"M.startsTextSelection({target})")
    # Assert
    assert keeps_native is True


def test_a_drag_starting_on_blank_space_is_claimed_by_the_rectangle():
    # Arrange — inside the list but outside any bubble.
    target = 'node(["msg"], node(["messages"]))'
    # Act
    keeps_native = _marquee(f"M.startsTextSelection({target})")
    # Assert
    assert keeps_native is False


def test_a_drag_starting_deep_inside_the_text_still_leaves_selection_alone():
    # Arrange — a link nested in the bubble, so the rule must walk UP to find it.
    target = 'node(["code"], node(["bubble"], node(["msg"])))'
    # Act
    keeps_native = _marquee(f"M.startsTextSelection({target})")
    # Assert
    assert keeps_native is True


def test_a_drag_starting_on_a_reaction_chip_does_not_start_a_rectangle():
    # Arrange
    target = 'node(["chip"], node(["reactions"], node(["msg"])))'
    # Act
    keeps_native = _marquee(f"M.startsTextSelection({target})")
    # Assert
    assert keeps_native is True


def test_a_drag_starting_on_the_show_all_control_does_not_start_a_rectangle():
    # Arrange — the control PR #621 had to rescue from selection mode. A
    # rectangle starting on it would take the gesture the same way.
    target = 'node(["button"], node(["longtext-tools"], node(["msg"])))'
    # Act
    keeps_native = _marquee(f"M.startsTextSelection({target})")
    # Assert
    assert keeps_native is True


# === touch is deliberately excluded ========================================


def test_the_marquee_binds_no_touch_handlers_so_a_phone_can_still_scroll():
    # Arrange
    source = MARQUEE_FILE.read_text(encoding="utf-8")
    # Act
    touch_bindings = [n for n in ("touchstart", "touchmove", "touchend") if n in source]
    # Assert
    assert touch_bindings == []


def test_the_marquee_ignores_a_touch_pointer_rather_than_competing_with_scroll():
    # Arrange
    source = MARQUEE_FILE.read_text(encoding="utf-8")
    # Act
    guards = source.count('pointerType === "touch"')
    # Assert
    assert guards == 1
