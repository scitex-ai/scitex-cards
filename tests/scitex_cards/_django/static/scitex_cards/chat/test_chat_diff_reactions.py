#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The thread pane must repaint when only the REACTIONS changed.

Mirrors ``src/scitex_cards/_django/static/scitex_cards/chat/chat_diff.js``.

This is the failure this file exists to prevent: a reaction leaves a message's
id, body, from and ts untouched, so a fingerprint built from those four compares
EQUAL, ``planRender`` answers ``noop``, and a reaction someone else added never
appears — until an unrelated message happens to arrive. The chip would look
broken on every device except the one that sent it.

Also pins the compatibility half: called WITHOUT a reactions map,
``messageFingerprint`` and ``planRender`` behave exactly as they did before, so
no existing caller changed meaning.

Runs the REAL module under node (no hand-ported copy), like test_chat_diff.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
)
DIFF_FILE = JS_DIR / "chat_diff.js"
ACTIONS_FILE = JS_DIR / "chat_actions.js"


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(js: str) -> str:
    """Run a JS fragment against the real chat modules; return stdout."""
    assert DIFF_FILE.is_file(), f"module under test missing: {DIFF_FILE}"
    script = (
        f"const ChatDiff = require({json.dumps(str(DIFF_FILE))});\n"
        f"const ChatActions = require({json.dumps(str(ACTIONS_FILE))});\n" + js
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip()


def _call(expr: str) -> object:
    """Evaluate an expression and JSON-decode the result."""
    return json.loads(_run(f"console.log(JSON.stringify({expr}));"))


_MSG = {
    "id": "m_001",
    "from": "agent-x",
    "body": "deploy is green",
    "ts": "2026-07-28T09:00:00Z",
}
_MESSAGES = json.dumps([_MSG])
_NO_REACTIONS = json.dumps({})
_ONE_REACTION = json.dumps({"m_001": {"\U0001f44d": ["operator"]}})
_TWO_REACTIONS = json.dumps({"m_001": {"\U0001f44d": ["operator", "agent-x"]}})


def _plan_after(before: str, after: str) -> dict:
    """Render with `before`'s reactions, then plan against `after`'s."""
    return _call(
        f"(function () {{"
        f"  var msgs = {_MESSAGES};"
        f"  var first = ChatDiff.planRender([], msgs, {before});"
        f"  return ChatDiff.planRender(first.fingerprints, msgs, {after});"
        f"}})()"
    )


# === the bug this prevents =================================================


def test_a_new_reaction_forces_a_repaint():
    # Arrange
    # Act
    plan = _plan_after(_NO_REACTIONS, _ONE_REACTION)
    # Assert
    # NOT "noop": the message is unchanged but the pane is now wrong.
    assert plan["mode"] == "rebuild"


def test_another_actor_joining_a_reaction_forces_a_repaint():
    # Arrange
    # Act
    plan = _plan_after(_ONE_REACTION, _TWO_REACTIONS)
    # Assert
    assert plan["mode"] == "rebuild"


def test_a_removed_reaction_forces_a_repaint():
    # Arrange
    # Act
    plan = _plan_after(_ONE_REACTION, _NO_REACTIONS)
    # Assert
    assert plan["mode"] == "rebuild"


def test_unchanged_reactions_still_plan_a_noop():
    # Arrange
    # Act
    plan = _plan_after(_ONE_REACTION, _ONE_REACTION)
    # Assert
    # the whole point of the diff is that an unchanged poll paints nothing.
    assert plan["mode"] == "noop"


# === compatibility =========================================================


def test_omitting_the_reactions_map_reproduces_the_old_fingerprint():
    # Arrange
    # Act
    same = _call(
        f"ChatDiff.messageFingerprint({json.dumps(_MSG)}) === "
        f"ChatDiff.messageFingerprint({json.dumps(_MSG)}, null)"
    )
    # Assert
    assert same is True


def test_plan_render_without_reactions_still_noops_on_an_unchanged_thread():
    # Arrange
    # Act
    plan = _call(
        f"(function () {{"
        f"  var msgs = {_MESSAGES};"
        f"  var first = ChatDiff.planRender([], msgs);"
        f"  return ChatDiff.planRender(first.fingerprints, msgs);"
        f"}})()"
    )
    # Assert
    assert plan["mode"] == "noop"


def test_a_message_map_call_is_not_confused_by_the_index_argument():
    # Arrange
    # Act
    # `messages.map(messageFingerprint)` passes (element, index) — the index
    # must not be mistaken for a reactions map now that a second arg exists.
    same = _call(
        f"JSON.stringify({_MESSAGES}.map(ChatDiff.messageFingerprint)) === "
        f"JSON.stringify({_MESSAGES}.map(function (m) {{"
        f"  return ChatDiff.messageFingerprint(m);"
        f"}}))"
    )
    # Assert
    assert same is True


# === the two signature implementations must agree ==========================


def test_the_duplicated_signature_implementations_agree():
    # Arrange
    reactions = json.dumps({"\U0001f44d": ["operator", "agent-x"], "✅": ["agent-y"]})
    # Act
    # chat_diff.js carries its own copy on purpose (it has no dependencies);
    # this is what stops the copy from drifting.
    same = _call(
        f"ChatDiff.messageFingerprint({json.dumps(_MSG)}, "
        f"{{'m_001': {reactions}}}).endsWith("
        f"ChatActions.reactionSignature({reactions}))"
    )
    # Assert
    assert same is True


# EOF
