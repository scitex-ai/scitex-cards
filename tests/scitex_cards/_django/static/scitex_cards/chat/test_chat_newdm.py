#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the sidebar's new-message input (no mocks).

Mirrors ``src/scitex_cards/_django/static/scitex_cards/chat/chat_newdm.js``:
the operator types any agent name (the datalist offers the polled roster,
free form still works), Enter opens that thread through the opener chat.js
hands over, and the input clears + blurs as if it had been a row click.

The module's own contract is "the caller owns the element lookup" —
``mount`` takes the ``input`` and ``datalist`` as collaborators — so the
fake objects below are test doubles for the BROWSER, not mocks of the
module: the real keydown handler, the real ``normalizeName`` and the real
datalist render run unchanged.

Run under node (same wrapper idiom as ``test_chat_filter.py``); each
``_run`` is a fresh node process, so the module's singleton ``bound`` state
can never leak between tests.
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
    / "chat_newdm.js"
)

# The fake browser. Each node process gets a fresh pair.
_FAKE_DOM = """
function fakeInput() {
  const handlers = {};
  const input = {
    value: "",
    blurred: 0,
    addEventListener(type, fn) { handlers[type] = fn; },
    removeEventListener(type, fn) { if (handlers[type] === fn) delete handlers[type]; },
    blur() { this.blurred++; },
    key(event) { if (handlers["keydown"]) handlers["keydown"](event); },
    detached() { return !handlers["keydown"]; },
  };
  return input;
}
function fakeDatalist() {
  const nodes = [];
  let seq = 0;
  const dl = {
    get firstChild() { return nodes.length ? nodes[0] : null; },
    removeChild(n) { nodes.splice(nodes.indexOf(n), 1); },
    appendChild(n) { nodes.push(n); },
    ownerDocument: { createElement: (tag) => ({ tag, id: ++seq }) },
    options() { return nodes.map((n) => n.value); },
    ids() { return nodes.map((n) => n.id); },
  };
  return dl;
}
function enter(event) {
  const e = { key: "Enter", prevented: false, preventDefault() { this.prevented = true; } };
  event(e);
  return e;
}
"""


def _node() -> str:
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(js: str) -> str:
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = (
        f"const ChatNewDm = require({json.dumps(str(JS_FILE))});\n"
        + _FAKE_DOM
        + js
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip()


# --- normalizeName ----------------------------------------------------------


def test_normalize_trims_and_collapses_whitespace() -> None:
    # Arrange
    js = "console.log(JSON.stringify(ChatNewDm.normalizeName('  agent\\t--   x ')));"
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) == "agent -- x"


def test_normalize_of_blank_is_null() -> None:
    # Arrange
    js = "console.log(JSON.stringify(ChatNewDm.normalizeName('   ')));"
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) is None


def test_normalize_of_null_is_null() -> None:
    # Arrange
    js = "console.log(JSON.stringify(ChatNewDm.normalizeName(null)));"
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) is None


# --- optionNames ------------------------------------------------------------


def test_option_names_dedupe_in_first_seen_order() -> None:
    # Arrange
    js = (
        "console.log(JSON.stringify(ChatNewDm.optionNames("
        "[{name: 'a'}, {name: 'b'}, {name: 'a'}, {name: '  b '}, {name: ''}])))"
    )
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) == ["a", "b"]


def test_option_names_skip_malformed_rows() -> None:
    # Arrange
    js = (
        "console.log(JSON.stringify(ChatNewDm.optionNames("
        "[null, {name: null}, {name: 'ok'}, 'not-an-object'])))"
    )
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) == ["ok"]


# --- mount: Enter opens the thread ------------------------------------------


def test_enter_fires_the_opener_with_the_normalized_name() -> None:
    # Arrange
    js = """
const opened = [];
const input = fakeInput(); const dl = fakeDatalist();
ChatNewDm.setOpener((name) => opened.push(name));
ChatNewDm.mount({input, datalist: dl});
input.value = "  agent-b  ";
input.key(enter((e) => e));
console.log(JSON.stringify(opened));
"""
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) == ["agent-b"]


def test_enter_clears_the_input() -> None:
    # Arrange
    js = """
const input = fakeInput(); const dl = fakeDatalist();
ChatNewDm.setOpener(() => {});
ChatNewDm.mount({input, datalist: dl});
input.value = "agent-b";
input.key(enter((e) => e));
console.log(JSON.stringify(input.value));
"""
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) == ""


def test_enter_blurs_the_input() -> None:
    # Arrange
    js = """
const input = fakeInput(); const dl = fakeDatalist();
ChatNewDm.setOpener(() => {});
ChatNewDm.mount({input, datalist: dl});
input.value = "agent-b";
input.key(enter((e) => e));
console.log(input.blurred);
"""
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) == 1


def test_enter_is_prevented() -> None:
    # Arrange — input.key dispatches the handler mount() registered, with a
    # spy event standing in for the browser's.
    js = """
const input = fakeInput(); const dl = fakeDatalist();
ChatNewDm.setOpener(() => {});
ChatNewDm.mount({input, datalist: dl});
input.value = "agent-b";
const e = {key: "Enter", prevented: false, preventDefault() { this.prevented = true; }};
input.key(e);
console.log(JSON.stringify(e.prevented));
"""
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) is True


def test_enter_with_a_blank_value_is_a_noop() -> None:
    # Arrange
    js = """
const opened = [];
const input = fakeInput(); const dl = fakeDatalist();
ChatNewDm.setOpener((name) => opened.push(name));
ChatNewDm.mount({input, datalist: dl});
input.value = "   ";
input.key(enter((e) => e));
console.log(JSON.stringify(opened));
"""
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) == []


def test_enter_without_an_opener_keeps_the_value() -> None:
    # Arrange
    js = """
const input = fakeInput(); const dl = fakeDatalist();
ChatNewDm.mount({input, datalist: dl});
input.value = "agent-b";
input.key(enter((e) => e));
console.log(JSON.stringify(input.value));
"""
    # Act
    out = _run(js)
    # Assert
    # No opener was handed over, so the handler returns before clearing:
    # the operator's text must not be swallowed.
    assert json.loads(out) == "agent-b"


# --- mount: Escape ----------------------------------------------------------


def test_escape_clears_the_draft() -> None:
    # Arrange
    js = """
const input = fakeInput(); const dl = fakeDatalist();
ChatNewDm.mount({input, datalist: dl});
input.value = "agent-b";
const e = {key: "Escape", preventDefault() {}};
input.key(e);
console.log(JSON.stringify(input.value));
"""
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) == ""


# --- mount: the datalist ----------------------------------------------------


def test_set_agents_populates_the_datalist() -> None:
    # Arrange
    js = """
const input = fakeInput(); const dl = fakeDatalist();
ChatNewDm.mount({input, datalist: dl});
ChatNewDm.setAgents([{name: "a"}, {name: "b"}, {name: "a"}]);
console.log(JSON.stringify(dl.options()));
"""
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) == ["a", "b"]


def test_repolling_the_same_roster_does_not_rewrite_the_options() -> None:
    # Arrange
    js = """
const input = fakeInput(); const dl = fakeDatalist();
ChatNewDm.mount({input, datalist: dl});
const roster = [{name: "a"}, {name: "b"}];
ChatNewDm.setAgents(roster);
const before = dl.ids();
ChatNewDm.setAgents(roster);
console.log(JSON.stringify({
  unchanged: dl.ids().every((id, i) => id === before[i]),
  count: dl.ids().length,
}));
"""
    # Act
    out = _run(js)
    # Assert
    # The list polls every ~10s; an unchanged roster must not rebuild the
    # datalist under the operator's cursor (the flicker guard).
    assert json.loads(out) == {"unchanged": True, "count": 2}


def test_a_changed_roster_rewrites_the_options() -> None:
    # Arrange
    js = """
const input = fakeInput(); const dl = fakeDatalist();
ChatNewDm.mount({input, datalist: dl});
ChatNewDm.setAgents([{name: "a"}, {name: "b"}]);
const before = dl.ids();
ChatNewDm.setAgents([{name: "b"}, {name: "c"}]);
console.log(JSON.stringify({
  options: dl.options(),
  rewritten: dl.ids().some((id, i) => id !== before[i]),
}));
"""
    # Act
    out = _run(js)
    # Assert
    assert json.loads(out) == {"options": ["b", "c"], "rewritten": True}


# --- mount: guards ----------------------------------------------------------


def test_mount_without_elements_returns_null() -> None:
    # Arrange
    js = """
const r = ChatNewDm.mount({input: null, datalist: null});
console.log(r === null ? "null" : "mounted");
"""
    # Act
    out = _run(js)
    # Assert
    assert out == "null"


def test_destroy_detaches_the_keydown_handler() -> None:
    # Arrange
    js = """
const opened = [];
const input = fakeInput(); const dl = fakeDatalist();
ChatNewDm.setOpener((name) => opened.push(name));
const handle = ChatNewDm.mount({input, datalist: dl});
handle.destroy();
console.log(input.detached() ? "detached" : "still-bound");
"""
    # Act
    out = _run(js)
    # Assert
    assert out == "detached"
