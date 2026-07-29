#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The drawer's mode switch, EXECUTED (no mocks).

Mirrors ``src/scitex_cards/_django/static/scitex_cards/chat/chat_drawer.js``.

WHY THIS FILE EXISTS RATHER THAN MORE OF ``test__chat_drawer_is_inert_when_closed``.
That file asserts against the SOURCE TEXT — "does `panel.inert` appear in the
file". Every one of its assertions stayed green through the entire lifetime of
defect 3, because the offending line was present and correct; what was wrong was
WHEN it ran. A scan cannot see a missing condition, only a missing string. So
these tests RUN the shipped module and read the properties it actually wrote.

WHAT DEFECT 3 WAS. ``chat.js`` passes ``#agents`` as the drawer panel, and above
the 720px breakpoint ``#agents`` is not a drawer — it is the permanently visible
agent sidebar. ``render()`` set ``inert`` and an inline ``visibility: hidden``
unconditionally from the open flag, and "closed" is the state at mount, so
loading the module blanked the desktop sidebar. An inline style beats the
stylesheet, so nothing in the CSS could win it back. The operator saw
"No agent selected." next to an empty sidebar while ``/dm/threads`` returned 15
agents and the tab title counted unread correctly — the API was healthy the
whole time, which is exactly why checking the API instead of the page missed it,
twice.

HOW MODE IS DECIDED. The hamburger ``#menu-btn`` is ``display: none`` in the
base stylesheet and ``display: block`` inside ``@media (max-width: 720px)``, so
its computed display already says which mode the page is in. The module reads
that instead of repeating 720 in JS, where it would drift from the CSS. These
tests therefore drive the trigger's display with those two literal values.

ON THE DOM HERE. Node has no DOM, and this repo has no browser in CI, so the
elements below are a small real implementation of the handful of DOM surfaces
the module touches — the ENVIRONMENT it runs in, not a stand-in for anything
under test. Nothing is stubbed out of ``chat_drawer.js``: the shipped file is
``require``d and its real ``mount``/``render`` run, and every assertion reads a
property that the module itself wrote. No interaction is asserted, only state.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS_FILE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
    / "chat_drawer.js"
)

# The two values the stylesheet actually produces for #menu-btn.
DESKTOP = "none"  # base rule: the hamburger is not shown
DRAWER = "block"  # @media (max-width: 720px)

# The DOM surfaces chat_drawer.js touches, implemented rather than recorded.
# `classList.toggle` honours the two-argument form because the module depends on
# it (defect 2), and `getComputedStyle` reports the element's own display
# because that is the only property the module reads from it.
_DOM = """
function element(display) {
  var classes = {};
  var el = {
    style: { display: display, visibility: "" },
    inert: false,
    attributes: {},
    classList: {
      toggle: function (name, force) {
        var on = arguments.length > 1 ? !!force : !classes[name];
        classes[name] = on;
        return on;
      },
      contains: function (name) {
        return !!classes[name];
      },
    },
    setAttribute: function (name, value) {
      el.attributes[name] = String(value);
    },
    addEventListener: function () {},
  };
  return el;
}

globalThis.window = {
  getComputedStyle: function (el) {
    return { display: el.style.display };
  },
};
globalThis.document = { addEventListener: function () {} };
"""


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _mount(trigger_display: str) -> dict:
    """Mount the REAL drawer with a trigger of ``trigger_display``.

    Returns what the module left on the panel after its mount-time ``render()``
    — the exact moment defect 3 fired.
    """
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = (
        _DOM
        + f"require({json.dumps(str(JS_FILE))});\n"
        + "var panel = element('block');\n"
        + "var scrim = element('none');\n"
        + f"var trigger = element({json.dumps(trigger_display)});\n"
        + "window.ChatDrawer.mount("
        + "{ panel: panel, scrim: scrim, trigger: trigger });\n"
        + "console.log(JSON.stringify({"
        + " visibility: panel.style.visibility,"
        + " inert: panel.inert,"
        + " expanded: trigger.attributes['aria-expanded'],"
        + " }));\n"
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip())


# === desktop: the panel is the permanent sidebar ===========================


def test_on_desktop_the_sidebar_is_not_hidden_after_mount():
    """The operator-visible half of defect 3: an empty agent sidebar.

    `visibility` is what took the list off the screen. An inline value here
    outranks the stylesheet, so this is the assertion that says the desktop
    branch must CLEAR the property rather than set a benign one.
    """
    # Arrange
    state = _mount(DESKTOP)

    # Act
    visibility = state["visibility"]

    # Assert
    assert visibility != "hidden"


def test_on_desktop_the_sidebar_is_not_inert_after_mount():
    """The half nobody would have reported.

    Asserted separately from visibility because neither implies the other: a
    desktop branch that cleared only `visibility` gives back a sidebar that
    looks completely fine and cannot be reached by keyboard or read by a screen
    reader. That version of the bug survives every screenshot.
    """
    # Arrange
    state = _mount(DESKTOP)

    # Act
    inert = state["inert"]

    # Assert
    assert inert is False


# === phone: the panel is a drawer, and defect 1 still stands ===============


def test_in_drawer_mode_a_closed_drawer_is_hidden():
    """The mobile behaviour the desktop fix must not cost us.

    `visibility` is what keeps the POINTER out of a drawer that a transform has
    moved off-screen but not removed.
    """
    # Arrange
    state = _mount(DRAWER)

    # Act
    visibility = state["visibility"]

    # Assert
    assert visibility == "hidden"


def test_in_drawer_mode_a_closed_drawer_is_inert():
    """`inert` is what keeps the KEYBOARD out of it — defect 1 itself.

    Without this, Tab from the header lands in an invisible agent list and the
    next Enter opens a thread the operator cannot see.
    """
    # Arrange
    state = _mount(DRAWER)

    # Act
    inert = state["inert"]

    # Assert
    assert inert is True


# EOF
