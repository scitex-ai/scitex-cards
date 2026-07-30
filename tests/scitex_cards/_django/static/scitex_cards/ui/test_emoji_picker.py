#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Behaviour tests for the composer's emoji picker (no mocks).

Mirrors ``src/scitex_cards/_django/static/scitex_cards/ui/emoji_picker.js``.

These tests ``require()`` the shipped module and run the REAL functions under
node — the same arrangement as ``test_chat_diff.py``. Nothing is hand-ported:
the file the browser loads is the file under test.

WHY THESE EXIST. The operator is migrating off Telegram and reported that the
board could only exchange text ("今まだテキストしかやりとりできていないです").
The picker's whole job is to put a character where the caret is, on a phone,
and every way that can go wrong is silent: the emoji lands at position 0
instead of the caret, or it overwrites the message, or a multi-codepoint
character is torn in half and renders as a stray box. Each of those is
pinned below.

The DOM half (``mount`` / ``autoMount``) is not exercised here: there is no
headless DOM in this environment and a hand-rolled fake one would be a mock
of the browser, which this suite does not do. What IS testable without a
browser — the caret arithmetic, the insertion contract and the emoji set —
is tested against the real module; the DOM wiring is pinned structurally by
``test_emoji_picker_contract.py``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo-relative path to the JS module under test. Resolved off this file's
# location so the test runs from any cwd, and so it reads the WORKTREE copy
# rather than whatever a shared venv happens to have installed.
JS_FILE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "ui"
    / "emoji_picker.js"
)


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(js: str) -> str:
    """Run a JS fragment against the real emoji_picker.js; return stdout."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = f"const Picker = require({json.dumps(str(JS_FILE))});\n" + js
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip()


def _eval(expression: str) -> object:
    """Evaluate a JS expression against the module and JSON-decode it."""
    return json.loads(_run(f"console.log(JSON.stringify({expression}));"))


def _splice(value: str, start, end, insert: str) -> dict:
    """Call the real ``spliceText`` with JSON-round-tripped arguments."""
    args = ", ".join(json.dumps(arg) for arg in (value, start, end, insert))
    return _eval(f"Picker.spliceText({args})")


# A field stand-in: NOT a mock of a textarea, but the duck type the module
# documents as its contract (value + selection offsets + setSelectionRange).
# Building it in JS keeps the assertions about the module's real behaviour.
_FIELD_JS = """
function makeField(value, start, end) {
  return {
    value: value,
    selectionStart: start,
    selectionEnd: end,
    setSelectionRange: function (a, b) {
      this.selectionStart = a;
      this.selectionEnd = b;
    },
  };
}
"""


def _insert(value: str, start, end, emoji: str, forced_range=None) -> dict:
    """Insert into a field stand-in and report the field afterwards."""
    args = ", ".join(json.dumps(arg) for arg in (value, start, end))
    forced = "undefined" if forced_range is None else json.dumps(forced_range)
    return json.loads(
        _run(
            _FIELD_JS
            + f"const field = makeField({args});\n"
            + f"Picker.insertEmoji(field, {json.dumps(emoji)}, {forced});\n"
            + "console.log(JSON.stringify({value: field.value, "
            "start: field.selectionStart, end: field.selectionEnd}));"
        )
    )


# === the emoji set ==========================================================


def test_the_set_is_small_enough_to_stay_a_grid() -> None:
    """The brief was a COMPACT grid, not a searchable database. A database
    needs a payload, and this board is served to a phone over a tunnel. If
    the set ever grows past a couple of screenfuls the design decision has
    been reversed by accident."""
    # Arrange
    # Act
    total = _eval("Picker.allEmoji().length")
    # Assert
    assert total <= 80


def test_every_group_carries_emoji() -> None:
    """An empty group renders a heading with nothing under it — a label for
    a section that does not exist."""
    # Arrange
    # Act
    sizes = _eval("Picker.EMOJI_GROUPS.map(function (g) { return g.emoji.length; })")
    # Assert
    assert min(sizes) > 0


def test_no_emoji_repeats() -> None:
    """Duplicates waste cells in a deliberately small grid, and a duplicate
    is the usual symptom of a copy-paste slip while editing the rows."""
    # Arrange
    # Act
    all_emoji = _eval("Picker.allEmoji()")
    # Assert
    assert len(all_emoji) == len(set(all_emoji))


def test_no_emoji_carries_whitespace() -> None:
    """The rows are stored space-separated. Whitespace inside an entry means
    the row was mis-split, which puts a blank cell in the grid and a stray
    space into the operator's message."""
    # Arrange
    all_emoji = _eval("Picker.allEmoji()")
    # Act
    with_space = [e for e in all_emoji if e.strip() != e or not e]
    # Assert
    assert with_space == []


def test_variation_selectors_survive_the_split() -> None:
    """THE regression a per-character split would cause: U+FE0F is what makes
    a codepoint render as a colour emoji rather than a monochrome text glyph.
    Split character-by-character, the heart becomes a bare U+2764 plus an
    orphan selector — two dud cells where one emoji should be."""
    # Arrange — the heart is stored as U+2764 U+FE0F.
    # Act
    all_emoji = _eval("Picker.allEmoji()")
    # Assert
    assert "❤️" in all_emoji


# === caret arithmetic =======================================================


def test_insert_lands_at_the_caret() -> None:
    """The whole point: the emoji goes where the operator is typing, not at
    either end of what they already wrote."""
    # Arrange
    # Act
    result = _splice("hello world", 5, 5, "!")
    # Assert
    assert result["value"] == "hello! world"


def test_caret_ends_after_the_inserted_text() -> None:
    """A second pick must add a second emoji rather than land on top of the
    first — which is what a caret left BEFORE the insertion would do."""
    # Arrange
    # Act
    result = _splice("hello world", 5, 5, "!")
    # Assert
    assert result["caret"] == 6


def test_a_selection_is_replaced_not_straddled() -> None:
    """Selected text is what the user asked to replace. Inserting around it
    would leave the selection in the message and put the emoji beside it."""
    # Arrange
    # Act
    result = _splice("abcdef", 1, 4, "X")
    # Assert
    assert result["value"] == "aXef"


def test_a_backwards_selection_is_normalised() -> None:
    """Dragging right-to-left is a real thing a user can hand us; an
    un-normalised range would slice with start > end and corrupt the
    message rather than edit it."""
    # Arrange
    # Act
    result = _splice("abcdef", 4, 1, "X")
    # Assert
    assert result["value"] == "aXef"


def test_an_unknown_caret_appends_instead_of_prepending() -> None:
    """Some fields report null for selectionStart. Treating that as 0 would
    silently PREPEND the emoji to a half-written message — the failure mode
    is invisible until the operator reads what they just sent."""
    # Arrange
    # Act
    result = _splice("already typed", None, None, "!")
    # Assert
    assert result["value"] == "already typed!"


def test_an_out_of_range_caret_is_clamped_to_the_text() -> None:
    """A stale offset (the field shrank since it was recorded) must not
    slice past the end and produce a value with a gap in it."""
    # Arrange
    # Act
    clamped = _eval("Picker.clampOffset(99, 3)")
    # Assert
    assert clamped == 3


# === insertion into a field =================================================


def test_insert_writes_the_new_value_back_to_the_field() -> None:
    """The splice is useless unless it reaches the field the operator is
    looking at."""
    # Arrange
    # Act
    field = _insert("hi", 2, 2, "!")
    # Assert
    assert field["value"] == "hi!"


def test_insert_moves_the_field_caret_after_the_emoji() -> None:
    """Typing must resume after the emoji, not before it."""
    # Arrange
    # Act
    field = _insert("hi", 2, 2, "!")
    # Assert
    assert field["start"] == 3


def test_an_explicit_range_overrides_the_live_selection() -> None:
    """THE touch case. A tap on the picker blurs the textarea, and a blurred
    field may report a caret of 0 — which would prepend every emoji picked
    on a phone. The picker therefore remembers the caret from while the
    field was focused and passes it in; that remembered range must win."""
    # Arrange — the field lies about its caret (reports 0, really was 5).
    # Act
    field = _insert("hello", 0, 0, "!", forced_range={"start": 5, "end": 5})
    # Assert
    assert field["value"] == "hello!"
