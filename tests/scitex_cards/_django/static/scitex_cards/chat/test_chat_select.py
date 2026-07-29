#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Which controls selection mode must let through (no mocks).

Mirrors ``src/scitex_cards/_django/static/scitex_cards/chat/chat_select.js``.

These tests ``require()`` the shipped module and run the REAL ``isViewControl``
under node, the same way ``test_chat_longtext.py`` runs the real thresholds.
Only the DOM surface the predicate touches — ``closest`` — is supplied, and it
is IMPLEMENTED (walk a class chain) rather than recorded, so a test cannot pass
by agreeing with a canned answer.

WHY THIS RULE IS WORTH PINNING. Selection mode installs a CAPTURE-phase click
listener on the message list and calls ``stopPropagation``, so by construction
it swallows every click inside a message before any descendant handler sees it.
That is deliberate — it stops a tap from also following an attachment link or
flipping a reaction. But it swallowed the long-text "Show all" toggle too, and
the operator hit it immediately (2026-07-29): 「ショーオールをクリックすると、
それに対応するメッセージが選択されてしまって、エクスパンドすることができません」
— a long message could not be expanded for as long as you were selecting.

The distinction the predicate draws is NOT "is it a control" — it is what the
control DOES. Navigating away and mutating the thread are a genuine second
answer to one gesture and stay blocked; changing how much of a message you can
SEE is not, and is arguably a prerequisite for deciding whether to select it.
Those two directions are what the tests below hold apart, because a future
reader tempted to "simplify" this into `closest("button")` would silently
re-break the reaction chips.
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
    / "chat_select.js"
)

# A click target as the browser presents it to the listener: the element itself
# plus the ancestor classes `closest` would find walking up. Implemented, not
# recorded — `closest` answers from the chain, so passing a different chain
# genuinely changes the answer.
_DOM = """
function targetWithin(chain) {
  return {
    closest: function (selector) {
      var want = selector.replace(/^\\./, "");
      return chain.indexOf(want) === -1 ? null : { matched: want };
    },
  };
}
"""


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _is_view_control(chain: list[str]) -> bool:
    """Run the REAL ``isViewControl`` against a target inside ``chain``."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = (
        _DOM
        + f"var Select = require({json.dumps(str(JS_FILE))}).ChatSelect;\n"
        + f"console.log(Select.isViewControl(targetWithin({json.dumps(chain)})));\n"
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip() == "true"


def test_the_long_text_expand_control_is_let_through_while_selecting():
    # Arrange
    chain = ["longtext-btn", "longtext-tools", "msg"]

    # Act
    exempt = _is_view_control(chain)

    # Assert
    assert exempt is True


def test_the_message_body_itself_is_not_let_through_and_still_selects():
    # Arrange
    chain = ["bubble", "msg"]

    # Act
    exempt = _is_view_control(chain)

    # Assert
    assert exempt is False


def test_an_attachment_link_stays_blocked_because_it_navigates_away():
    # Arrange
    chain = ["attachment-link", "bubble", "msg"]

    # Act
    exempt = _is_view_control(chain)

    # Assert
    assert exempt is False


def test_a_reaction_chip_stays_blocked_because_it_mutates_the_thread():
    # Arrange
    chain = ["reaction-chip", "msg"]

    # Act
    exempt = _is_view_control(chain)

    # Assert
    assert exempt is False


# EOF
