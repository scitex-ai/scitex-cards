#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hide/unhide list decisions for the DM agent list (no mocks).

Mirrors ``src/scitex_cards/_django/static/scitex_cards/chat/chat_visibility.js``.

These tests ``require()`` the shipped module and run the REAL functions under
node — the same contract ``test_chat_diff.py`` follows, and for the same
reason: a hand-ported copy of the logic would pass while the file the browser
loads was broken. ``chat_hide.js`` owns the DOM and cannot be required under
node; its wiring is asserted against the source in
``tests/.../test__dm_sidebar_project_slot.py`` instead.

What is pinned:

  1. ``partition`` — hidden rows separate from visible ones; a missing or
     non-boolean flag counts as visible (older payloads must not hide rows
     nobody hid).
  2. ``hiddenUnread`` — the disclosure's unread count reads the SAME
     per-row ``unread`` field the badges render.
  3. ``swipeDecision`` — left past half the action snaps open, anything
     else snaps shut.
  4. ``disclosureLabel`` — "Hidden (N)", unread riding along only when
     nonzero (no "(0) state").
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("django")

# Repo-relative path to the JS module under test. Resolved off this file's
# location so the test runs from any cwd.
JS_FILE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
    / "chat_visibility.js"
)


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(js: str) -> str:
    """Run a JS fragment against the real chat_visibility.js; return stdout."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = f"const ChatVisibility = require({json.dumps(str(JS_FILE))});\n" + js
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip()


def test_partition_separates_hidden_from_visible():
    # Arrange
    agents = [
        {"name": "a", "hidden": False},
        {"name": "b", "hidden": True},
        {"name": "c", "hidden": True},
    ]
    # Act
    out = _run(
        f"const agents = {json.dumps(agents)};\n"
        "const p = ChatVisibility.partition(agents);\n"
        "console.log(JSON.stringify({v: p.visible.map(x => x.name),"
        " h: p.hidden.map(x => x.name)}));"
    )
    # Assert
    assert json.loads(out) == {"v": ["a"], "h": ["b", "c"]}


def test_partition_treats_a_missing_flag_as_visible():
    # Arrange — a pre-v16 payload carries no flag at all.
    agents = [{"name": "a"}, {"name": "b", "hidden": 0}]
    # Act
    out = _run(
        f"const agents = {json.dumps(agents)};\n"
        "const p = ChatVisibility.partition(agents);\n"
        "console.log(JSON.stringify(p.hidden.length));"
    )
    # Assert
    assert json.loads(out) == 0


def test_hidden_unread_sums_the_badge_field():
    # Arrange
    hidden = [
        {"name": "b", "unread": 2},
        {"name": "c", "unread": 0},
        {"name": "d"},
    ]
    # Act
    out = _run(
        f"const hidden = {json.dumps(hidden)};\n"
        "console.log(JSON.stringify(ChatVisibility.hiddenUnread(hidden)));"
    )
    # Assert
    assert json.loads(out) == 2


def test_swipe_opens_left_past_half_the_action():
    # Arrange — pure function, no setup.
    # Act
    out = _run(
        "console.log(JSON.stringify(["
        "ChatVisibility.swipeDecision(-36),"
        " ChatVisibility.swipeDecision(-37),"
        " ChatVisibility.swipeDecision(-10),"
        " ChatVisibility.swipeDecision(0),"
        " ChatVisibility.swipeDecision(40)]));"
    )
    # Assert
    assert json.loads(out) == ["open", "open", "close", "close", "close"]


def test_disclosure_label_hides_a_zero_unread():
    # Arrange — pure function, no setup.
    # Act
    out = _run(
        "console.log(JSON.stringify(["
        "ChatVisibility.disclosureLabel(3, 0),"
        " ChatVisibility.disclosureLabel(2, 5)]));"
    )
    # Assert
    assert json.loads(out) == ["Hidden (3)", "Hidden (2) \u2022 5 unread"]


# EOF
