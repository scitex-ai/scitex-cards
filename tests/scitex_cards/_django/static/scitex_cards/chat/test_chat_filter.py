#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the DM agent-list fuzzy filter (no mocks).

Mirrors ``src/scitex_cards/_django/static/scitex_cards/chat/chat_filter.js``.

These tests ``require()`` the shipped module and run the REAL functions under
node — there is no hand-ported copy of the matching logic here, so the single
source of truth stays the file the browser loads.

WHAT THIS IS FOR. The operator's standing request, repeated: 「普通にあいまい検索で
フィルタはいつも入れてください；[SDK: the old UI package at the time] にもなければいけない話です」.
The board already consumes the SDK's Combobox over its six filter <select>s; the chat page's agent
list had no filter at all, and it is the list that grows without bound.

THE PROPERTY THAT MATTERS IS NOT "IT FILTERS". It is that it filters the SAME WAY
the board does, because the matcher is the SDK's and not ours. So the tests
below inject a fake `STX.Combobox.fuzzyMatch` scope and assert the module CALLS
it — a module that reimplemented subsequence matching internally would pass a
"finds dev-helper from dvhlp" test and fail these. (Injecting the scope is not a
mock of the thing under test: the module's own contract is "resolve the matcher
off the window at call time", and the scope is that argument.)
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
    / "chat_filter.js"
)

#: A realistic fleet: names that share long prefixes, which is precisely the
#: shape a substring filter handles badly and the operator has to look at.
AGENTS = [
    "dev-helper",
    "scitex-cards",
    "scitex-ui",
    "worker-telegrammer-orochi",
    "worker-telegrammer-ywata-note-win",
    "lead",
]


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(js: str) -> str:
    """Run a JS fragment against the real chat_filter.js; return stdout."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = f"const ChatFilter = require({json.dumps(str(JS_FILE))});\n" + js
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip()


#: A stand-in for the SDK's real export, with the same subsequence semantics
#: as `STX.Combobox.fuzzyMatch`. It also RECORDS its calls, which is how the
#: tests below tell "delegated" apart from "reimplemented".
_STX_SCOPE = """
const calls = [];
const scope = { STX: { Combobox: { fuzzyMatch(q, hay) {
  calls.push([q, hay]);
  let i = 0;
  for (const c of q) {
    const at = hay.indexOf(c, i);
    if (at === -1) return false;
    i = at + 1;
  }
  return true;
} } } };
"""


def _filter(query: str, scope: str = _STX_SCOPE, names: list[str] | None = None):
    """Filter AGENTS by `query` through the real module."""
    items = AGENTS if names is None else names
    return json.loads(
        _run(
            scope
            + f"const names = {json.dumps(items)};\n"
            + f"console.log(JSON.stringify("
            f"ChatFilter.filterNames(names, {json.dumps(query)}, scope)));"
        )
    )


# === delegation — the matcher is the SDK's, not a second copy ============


def test_it_calls_the_sdk_matcher() -> None:
    """A private reimplementation would never touch `STX.Combobox.fuzzyMatch`."""
    # Arrange
    # Act
    out = _run(
        _STX_SCOPE + f"ChatFilter.filterNames({json.dumps(AGENTS)}, 'lead', scope);\n"
        "console.log(JSON.stringify(calls.length > 0));"
    )
    # Assert
    assert json.loads(out) is True


def test_it_passes_both_query_and_candidate_to_the_matcher() -> None:
    """Argument order is (query, haystack) — reversed, everything still 'works'."""
    # Arrange
    # Act
    out = _run(
        _STX_SCOPE + "ChatFilter.filterNames(['dev-helper'], 'dev', scope);\n"
        "console.log(JSON.stringify(calls[0]));"
    )
    # Assert
    assert json.loads(out) == ["dev", "dev-helper"]


# === matching behaviour ====================================================


def test_a_subsequence_matches_where_a_substring_would_not() -> None:
    """'dvhlp' is not a substring of any agent name; it IS a subsequence."""
    # Arrange
    # Act
    kept = _filter("dvhlp")
    # Assert
    assert kept == ["dev-helper"]


def test_a_shared_prefix_keeps_both_siblings() -> None:
    """The whole point of the filter: narrowing, not guessing one winner."""
    # Arrange
    # Act
    kept = _filter("wtg")
    # Assert
    assert kept == [
        "worker-telegrammer-orochi",
        "worker-telegrammer-ywata-note-win",
    ]


def test_matching_ignores_case() -> None:
    """The operator types lowercase; agent names are not always lowercase."""
    # Arrange
    # Act
    kept = _filter("SciTeX-UI")
    # Assert
    assert "scitex-ui" in kept


def test_a_query_matching_nothing_keeps_nothing() -> None:
    """An honest empty result — the page says so rather than showing a blank."""
    # Arrange
    # Act
    kept = _filter("qqqq")
    # Assert
    assert kept == []


# === the empty query — a filter nobody typed in must not hide anything =====


def test_an_empty_query_keeps_every_agent() -> None:
    """The default state of the page."""
    # Arrange
    # Act
    kept = _filter("")
    # Assert
    assert kept == AGENTS


def test_a_whitespace_query_keeps_every_agent() -> None:
    """A stray space is not a filter — trimming it is not cosmetic."""
    # Arrange
    # Act
    kept = _filter("   ")
    # Assert
    assert kept == AGENTS


def test_a_null_query_keeps_every_agent() -> None:
    """Before the input exists, `value` can arrive as null/undefined."""
    # Arrange
    # Act
    out = _run(
        _STX_SCOPE + f"console.log(JSON.stringify("
        f"ChatFilter.filterNames({json.dumps(AGENTS)}, null, scope)));"
    )
    # Assert
    assert json.loads(out) == AGENTS


# === the fallback — degraded, and visibly so ===============================


def test_without_sdk_it_still_filters() -> None:
    """An old/absent SDK must cost behaviour, not the page."""
    # Arrange
    # Act
    kept = _filter("telegrammer", scope="const scope = {};\n")
    # Assert
    assert kept == [
        "worker-telegrammer-orochi",
        "worker-telegrammer-ywata-note-win",
    ]


def test_the_fallback_is_substring_only_not_a_second_fuzzy_matcher() -> None:
    """Two fuzzy matchers would mean two search behaviours in one app.

    This is the assertion that keeps the fallback honest: if someone "improves"
    it into a subsequence matcher, this goes red — and it should, because then
    an old SDK would look identical to a current one right up until the
    two implementations disagreed.
    """
    # Arrange
    # Act
    kept = _filter("dvhlp", scope="const scope = {};\n")
    # Assert
    assert kept == []


def test_a_partial_sdk_without_fuzzy_match_falls_back() -> None:
    """`STX.Combobox` present but no `fuzzyMatch` static — the 0.5.x shape."""
    # Arrange
    # Act
    kept = _filter(
        "scitex-ui",
        scope="const scope = { STX: { Combobox: function () {} } };\n",
    )
    # Assert
    assert kept == ["scitex-ui"]


# === the focus shortcut — Ctrl+K / Cmd+K, never bare `/` ====================
#
# Operator 2026-10-24: focusing the filter is Ctrl+K. A bare `/` would fire
# while the operator types a message and steal the keystroke, so the decision
# below must stay False for it no matter what else changes.


def _focus_key(ev: dict) -> bool:
    """Run the real `isFocusFilterKey` under node against a plain object."""
    return json.loads(
        _run(
            f"console.log(JSON.stringify(\n"
            f"ChatFilter.isFocusFilterKey({json.dumps(ev)})));"
        )
    )


def test_ctrl_k_focuses_the_filter() -> None:
    # Arrange
    # Act
    out = _focus_key({"key": "k", "ctrlKey": True})
    # Assert
    assert out is True


def test_cmd_k_focuses_the_filter() -> None:
    # Arrange
    # Act
    out = _focus_key({"key": "k", "metaKey": True})
    # Assert
    assert out is True


def test_shifted_k_still_focuses_the_filter() -> None:
    # Arrange
    # Act
    out = _focus_key({"key": "K", "ctrlKey": True, "shiftKey": True})
    # Assert
    assert out is True


def test_bare_slash_never_focuses_the_filter() -> None:
    """`/` with no modifier must not steal a keystroke from the composer."""
    # Arrange
    # Act
    out = _focus_key({"key": "/"})
    # Assert
    assert out is False


def test_ctrl_slash_does_not_focus_the_filter() -> None:
    # Arrange
    # Act
    out = _focus_key({"key": "/", "ctrlKey": True})
    # Assert
    assert out is False


def test_a_plain_k_does_not_focus_the_filter() -> None:
    """Typing `k` into the composer must not yank focus away."""
    # Arrange
    # Act
    out = _focus_key({"key": "k"})
    # Assert
    assert out is False


def test_alt_k_does_not_focus_the_filter() -> None:
    # Arrange
    # Act
    out = _focus_key({"key": "k", "ctrlKey": True, "altKey": True})
    # Assert
    assert out is False


def test_mount_binds_the_document_shortcut_and_focuses_the_input() -> None:
    """The decision above is dead unless mount() wires it: a document-level
    keydown that preventDefaults, focuses and selects the input."""
    # Arrange
    source = JS_FILE.read_text(encoding="utf-8")
    # Act
    bound = 'doc.addEventListener("keydown", onDocKeydown)' in source
    focuses = "input.focus()" in source and "input.select()" in source
    guarded = "isFocusFilterKey(event)" in source
    # Assert
    assert bound and focuses and guarded


def test_destroy_unbinds_the_document_shortcut() -> None:
    """A leaked document listener would focus a removed input after unmount."""
    # Arrange
    source = JS_FILE.read_text(encoding="utf-8")
    # Act
    unbound = 'doc.removeEventListener("keydown", onDocKeydown)' in source
    # Assert
    assert unbound
