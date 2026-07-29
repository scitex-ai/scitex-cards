#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the long-message thresholds (no mocks).

Mirrors ``src/scitex_cards/_django/static/scitex_cards/chat/chat_longtext.js``.

These tests ``require()`` the shipped module and run the REAL functions under
node, the same way ``test_chat_diff.py`` does: the pure half of that file
(``isLong`` / ``summaryOf`` / ``fileNameFor``) touches no browser API, so node
can import the actual file and the single source of truth stays the file the
browser loads.

WHY THE THRESHOLD IS WORTH PINNING. The operator's complaint was that a long
body 「画面を埋め尽くす」 — fills the screen. A threshold that drifts upward is
indistinguishable from not having one, and nothing about the page would look
broken; it would just quietly stop clamping the messages it exists to clamp.
The boundary tests below are what make the number a decision rather than a
default.
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
    / "chat_longtext.js"
)


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(js: str) -> str:
    """Run a JS fragment against the real chat_longtext.js; return stdout."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = f"const LongText = require({json.dumps(str(JS_FILE))});\n" + js
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip()


def _call(expression: str, argument) -> str:
    """Evaluate ``LongText.<expression>(<argument>)`` and return its output."""
    return _run(f"console.log(LongText.{expression}({json.dumps(argument)}));")


def _constant(name: str) -> int:
    """One exported threshold, as an int."""
    return int(_run(f"console.log(LongText.{name});"))


def test_an_ordinary_message_is_not_long():
    """The clamp must be invisible for the traffic the board mostly carries."""
    # Arrange
    body = "Deployed 0.17.10 to the box and the health check is green."

    # Act
    verdict = _call("isLong", body)

    # Assert
    assert verdict == "false"


def test_a_body_over_the_character_budget_is_long():
    # Arrange
    body = "x" * (_constant("LONG_CHARS") + 1)

    # Act
    verdict = _call("isLong", body)

    # Assert
    assert verdict == "true"


def test_a_body_exactly_at_the_character_budget_is_left_alone():
    """The boundary is a decision: `>` not `>=`, and it is pinned here."""
    # Arrange
    body = "x" * _constant("LONG_CHARS")

    # Act
    verdict = _call("isLong", body)

    # Assert
    assert verdict == "false"


def test_a_short_but_many_lined_body_is_long():
    """The two criteria are INDEPENDENT, and this is the case that proves it.

    An element-inspector dump — the operator's own example — is already
    hard-wrapped, so it can be dozens of lines tall while its character count
    stays far under the budget. A character-only rule would sail past it.
    """
    # Arrange
    body = "\n".join(["a"] * (_constant("LONG_LINES") + 1))

    # Act
    verdict = _call("isLong", body)

    # Assert
    assert verdict == "true"


def test_clamping_always_buys_back_more_screen_than_the_controls_cost():
    """Both thresholds must sit at least twice the clamp height.

    Clamping adds a control row under the bubble. If the threshold sat just
    above the clamp, that row would cost about as much height as the clamp
    saved — a change that looks active and achieves nothing.
    """
    # Arrange
    clamp = _constant("CLAMP_LINES")

    # Act
    smallest_trigger = min(_constant("LONG_LINES"), _constant("LONG_CHARS") // 40)

    # Assert
    assert smallest_trigger >= 2 * clamp


def test_the_summary_reports_the_line_count():
    """The operator decides expand-or-download from this line alone."""
    # Arrange
    body = "\n".join(["line"] * 37)

    # Act
    summary = _call("summaryOf", body)

    # Assert
    assert summary.startswith("37 lines,")


def test_the_summary_reports_the_character_count():
    # Arrange
    body = "x" * 1234

    # Act
    summary = _call("summaryOf", body)

    # Assert
    assert summary.endswith("1,234 characters")


def test_the_download_name_carries_the_message_id():
    """Two downloads from one thread have to be tellable apart."""
    # Arrange
    message = {"id": "m_1a2b3c", "ts": "2026-07-28T11:00:00", "from": "operator"}

    # Act
    name = _run(f"console.log(LongText.fileNameFor({json.dumps(message)}));")

    # Assert
    assert name == "dm-m_1a2b3c.txt"


def test_the_download_name_cannot_carry_a_path():
    """The id reaches a filesystem, so a separator in it must not survive."""
    # Arrange
    message = {"id": "../../etc/passwd"}

    # Act
    name = _run(f"console.log(LongText.fileNameFor({json.dumps(message)}));")

    # Assert
    assert "/" not in name


# EOF
