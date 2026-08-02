#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The thread pane must repaint when only a RECEIPT changed.

Mirrors ``src/scitex_cards/_django/static/scitex_cards/chat/chat_diff.js``.

This is the failure this file exists to prevent, and it is the same one the
reactions fold already fixed once: a confirmation leaves a message's id, body,
from and ts untouched AND leaves its reactions untouched, so a fingerprint built
without it compares EQUAL, ``planRender`` answers ``noop``, and the eye never
appears until an unrelated message happens to arrive.

That bug would be invisible in the worst possible way. The whole point of the
eye is to show a message being confirmed while nothing else is happening — an
agent draining its inbox on a quiet thread. A pane that only repaints on new
traffic would report exactly nothing in exactly that case, and the operator
would conclude the agent was dead when it was not (or, worse, that it was alive
when the ack never came).

Also pins the compatibility half: called WITHOUT a receipts map,
``messageFingerprint`` and ``planRender`` behave exactly as before, so no
existing caller changed meaning.

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

MESSAGE = {"id": "m1", "body": "are you there", "from": "operator", "ts": "2026-07-29"}
PENDING = {"m1": {"state": "pending", "readers": []}}
RECEIVED = {"m1": {"state": "received", "readers": ["agent-x"]}}


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(js: str) -> str:
    """Run a JS fragment against the real chat_diff module; return stdout."""
    assert DIFF_FILE.is_file(), f"module under test missing: {DIFF_FILE}"
    script = f"const ChatDiff = require({json.dumps(str(DIFF_FILE))});\n" + js
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def test_a_confirmation_arriving_alone_changes_the_fingerprint():
    """Nothing but the receipt moved, and the pane must still notice."""
    # Arrange
    js = (
        f"const m = {json.dumps(MESSAGE)};\n"
        f"const before = ChatDiff.messageFingerprint(m, null, {json.dumps(PENDING)});\n"
        f"const after = ChatDiff.messageFingerprint(m, null, {json.dumps(RECEIVED)});\n"
        "console.log(String(before !== after));"
    )

    # Act
    changed = _run(js)

    # Assert
    assert changed == "true"


def test_a_confirmation_arriving_alone_forces_a_repaint():
    """The plan must not answer noop, or the eye never reaches the screen."""
    # Arrange
    js = (
        f"const m = {json.dumps(MESSAGE)};\n"
        f"const rendered = [ChatDiff.messageFingerprint(m, null,"
        f" {json.dumps(PENDING)})];\n"
        f"const plan = ChatDiff.planRender(rendered, [m], null,"
        f" {json.dumps(RECEIVED)});\n"
        "console.log(plan.mode);"
    )

    # Act
    mode = _run(js)

    # Assert
    assert mode == "rebuild"


def test_an_unchanged_receipt_still_leaves_the_pane_alone():
    """Repainting on every poll would destroy the operator's text selection."""
    # Arrange
    js = (
        f"const m = {json.dumps(MESSAGE)};\n"
        f"const rendered = [ChatDiff.messageFingerprint(m, null,"
        f" {json.dumps(PENDING)})];\n"
        f"const plan = ChatDiff.planRender(rendered, [m], null,"
        f" {json.dumps(PENDING)});\n"
        "console.log(plan.mode);"
    )

    # Act
    mode = _run(js)

    # Assert
    assert mode == "noop"


def test_omitting_the_receipts_map_reproduces_the_old_fingerprint():
    """Every existing caller and test keeps its exact previous meaning."""
    # Arrange
    js = (
        f"const m = {json.dumps(MESSAGE)};\n"
        "const bare = ChatDiff.messageFingerprint(m, null);\n"
        "const explicit = ChatDiff.messageFingerprint(m, null, null);\n"
        "console.log(String(bare === explicit));"
    )

    # Act
    same = _run(js)

    # Assert
    assert same == "true"


# EOF
