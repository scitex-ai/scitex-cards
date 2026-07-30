#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What each delivery state is allowed to PAINT on the three-dot track.

Mirrors ``src/scitex_cards/_django/static/scitex_cards/chat/chat_receipts.js``.

The backend decides the state; this file pins that the rendering cannot betray
it. Three failures worth naming, because each would look like a working feature:

  - a filled "read" dot on a message nobody confirmed (the lie the whole
    feature forbids, and the one that would have hidden the DM outage),
  - "cannot tell" drawn like "confirmed", or drawn like "hasn't happened yet" —
    both are claims we are not entitled to make,
  - state carried by colour alone, which is no state at all for a colour-blind
    reader or a high-contrast display.

Runs the REAL module under node (no hand-ported copy), like test_chat_diff.py.
Assertions are on the module's OWN return values, never on a hardcoded HTML
string — a snapshot of markup pins the spelling, not the behaviour.
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
RECEIPTS_FILE = JS_DIR / "chat_receipts.js"
DIFF_FILE = JS_DIR / "chat_diff.js"


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(js: str) -> str:
    """Run a JS fragment against the real chat modules; return stdout."""
    assert RECEIPTS_FILE.is_file(), f"module under test missing: {RECEIPTS_FILE}"
    script = (
        f"const ChatReceipts = require({json.dumps(str(RECEIPTS_FILE))});\n"
        f"const ChatDiff = require({json.dumps(str(DIFF_FILE))});\n" + js
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _track(state: str, readers: list[str] | None = None) -> dict:
    """The track the module paints for one operator message in ``state``."""
    entry = {"state": state, "readers": readers or []}
    out = _run(
        "const m = {id: 'm1', from: 'operator'};\n"
        f"const r = {{m1: {json.dumps(entry)}}};\n"
        "console.log(JSON.stringify(ChatReceipts.trackFor(m, r)));"
    )
    return json.loads(out)


def _dots(state: str, readers: list[str] | None = None) -> dict:
    """``{step_name: dot_state}`` for one message."""
    return {s["name"]: s["dot"] for s in _track(state, readers)["steps"]}


# --------------------------------------------------------------------------- #
# The track the operator asked for                                             #
# --------------------------------------------------------------------------- #
def test_the_track_has_the_three_steps_the_operator_specified():
    """Sent -> Queued -> Read. Three states localise WHERE delivery died."""
    # Arrange
    expected = ["sent", "queued", "read"]

    # Act
    names = [s["name"] for s in _track("pending")["steps"]]

    # Assert
    assert names == expected


def test_a_message_the_store_knows_about_fills_the_first_dot():
    """The server returned a state for it, so its row exists."""
    # Arrange
    expected = "on"

    # Act
    dot = _dots("pending")["sent"]

    # Assert
    assert dot == expected


def test_a_message_the_store_knows_nothing_about_does_not_claim_durability():
    """ "Rendered" does not imply "stored", and MEASURED it does not.

    The thread endpoint returns the UNION of the pre-migration threads.json
    sidecar and dm_messages; on the live store 57 of the operator's 137 messages
    in one thread exist ONLY in the sidecar. Filling "sent" for those would
    assert durability for exactly the population that lacks it.
    """
    # Arrange
    forbidden = "on"

    # Act
    dot = _run(
        "const m = {id: 'sidecar-only', from: 'operator'};\n"
        "console.log(ChatReceipts.trackFor(m, {}).steps[0].dot);"
    )

    # Assert
    assert dot != forbidden


# --------------------------------------------------------------------------- #
# The read dot — the one that must never lie                                   #
# --------------------------------------------------------------------------- #
def test_an_unconfirmed_message_leaves_the_read_dot_empty():
    """Stored is not received. This is the state 138 live operator DMs are in."""
    # Arrange
    expected = "off"

    # Act
    dot = _dots("pending")["read"]

    # Assert
    assert dot == expected


def test_a_confirmed_message_fills_the_read_dot():
    """A receipt written BY the recipient is the one thing that fills it."""
    # Arrange
    expected = "on"

    # Act
    dot = _dots("received", ["agent-x"])["read"]

    # Assert
    assert dot == expected


def test_an_undeterminable_message_never_fills_the_read_dot():
    """ "Cannot tell" must not be readable as "confirmed" — the lie by
    resemblance."""
    # Arrange
    forbidden = "on"

    # Act
    dot = _dots("unknowable")["read"]

    # Assert
    assert dot != forbidden


def test_an_undeterminable_message_is_not_drawn_as_merely_not_yet():
    """ "Hasn't happened" is also a claim, and an unknown state cannot make it."""
    # Arrange
    not_yet = _dots("pending")["read"]

    # Act
    unknown = _dots("unknowable")["read"]

    # Assert
    assert unknown != not_yet


def test_a_message_the_server_said_nothing_about_is_not_confirmed():
    """A gap in the map is ignorance, and ignorance is never confirmation."""
    # Arrange
    out = _run(
        "const m = {id: 'ghost', from: 'operator'};\n"
        "console.log(ChatReceipts.trackFor(m, {}).state);"
    )

    # Act
    state = out

    # Assert
    assert state == "unknowable"


def test_an_unrecognised_state_degrades_to_cannot_tell():
    """A newer server's unknown state must fail toward silence, not toward a
    filled dot."""
    # Arrange
    track = _track("delivered-probably")

    # Act
    state = track["state"]

    # Assert
    assert state == "unknowable"


# --------------------------------------------------------------------------- #
# The queued dot is honest about not being implemented                         #
# --------------------------------------------------------------------------- #
def test_the_queued_dot_is_drawn_as_unknown_not_guessed():
    """The notification carries no message id, so this step is not observable.

    The only join available is (thread, ts, actor) — which is ALSO the inbox
    dedupe key while DM stamps are second-resolution, so it is many-to-one by
    construction, and was measured collapsing two live messages onto one
    notification. Filling this dot from that join would mark a message nothing
    was ever delivered for.
    """
    # Arrange
    expected = "unknown"

    # Act
    dot = _dots("received", ["agent-x"])["queued"]

    # Assert
    assert dot == expected


# --------------------------------------------------------------------------- #
# Where the track appears at all                                               #
# --------------------------------------------------------------------------- #
def test_no_track_at_all_when_the_server_sent_no_receipts():
    """An older server means the feature is absent, not that everything is
    unknown."""
    # Arrange
    out = _run(
        "const m = {id: 'm1', from: 'operator'};\n"
        "console.log(JSON.stringify(ChatReceipts.trackFor(m, null)));"
    )

    # Act
    track = json.loads(out)

    # Assert
    assert track is None


def test_the_track_rides_on_the_viewers_own_messages_only():
    """A track on an incoming bubble would only restate that the reader is
    reading."""
    # Arrange
    out = _run(
        "const m = {id: 'm1', from: 'agent-x'};\n"
        "const r = {m1: {state: 'received', readers: ['operator']}};\n"
        "console.log(JSON.stringify(ChatReceipts.trackFor(m, r)));"
    )

    # Act
    track = json.loads(out)

    # Assert
    assert track is None


# --------------------------------------------------------------------------- #
# The meaning must be reachable without a mouse                                #
# --------------------------------------------------------------------------- #
def test_the_confirmed_track_names_who_confirmed_it():
    """Hover, tap and screen reader all read this string, so it must say WHO."""
    # Arrange
    expected = "agent-x"

    # Act
    summary = _track("received", ["agent-x"])["summary"]

    # Assert
    assert expected in summary


def test_every_step_carries_its_own_wording():
    """A track whose meaning is one blob cannot explain WHICH step stalled."""
    # Arrange
    steps = _track("pending")["steps"]

    # Act
    labelled = [s for s in steps if s.get("label")]

    # Assert
    assert len(labelled) == 3


# --------------------------------------------------------------------------- #
# The signature the repaint depends on                                         #
# --------------------------------------------------------------------------- #
def test_the_receipt_signature_matches_the_diff_modules_copy():
    """chat_diff duplicates this function by design; the two must not drift.

    chat_diff.js keeps zero dependencies so the node tests can load it alone,
    which costs a duplicated signature helper. This is the pin that makes the
    duplication safe.
    """
    # Arrange
    entry = json.dumps({"state": "received", "readers": ["b", "a"]})

    # Act
    same = _run(
        f"const e = {entry};\n"
        "console.log(String(ChatReceipts.receiptSignature(e) ==="
        " ChatDiff.receiptSignature(e)));"
    )

    # Assert
    assert same == "true"


def test_confirmation_by_a_new_reader_changes_the_signature():
    """Who confirmed is part of the state, so a second ack still repaints."""
    # Arrange
    one = json.dumps({"state": "pending", "readers": ["a"]})
    two = json.dumps({"state": "pending", "readers": ["a", "b"]})

    # Act
    differ = _run(
        f"console.log(String(ChatReceipts.receiptSignature({one}) !=="
        f" ChatReceipts.receiptSignature({two})));"
    )

    # Assert
    assert differ == "true"


# EOF
