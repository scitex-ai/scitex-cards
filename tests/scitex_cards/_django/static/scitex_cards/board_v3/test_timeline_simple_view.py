#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the timeline simple-view card renderer (no mocks).

Exercises ``src/scitex_cards/_django/static/scitex_cards/board_v3/
timeline.js`` by running the SHIPPED file under node — the same arrangement
as the ``chat/`` module tests and ``test_card_helpers.py``: a hand-ported
copy of the logic would pass while the file the browser loads was broken.

The timeline bundle is an IIFE publishing to ``window`` (no module.exports),
so the harness builds a minimal DOM-free sandbox: ``STATE`` (the graph
nodes the view projects), ``TL`` state is internal (``window._TL``),
``escapeHtml`` + ``openDetail`` stubs, a ``fetch`` stub returning the raster
payload, and a ``document`` stub inert enough for the module's only
import-time touch (none — asserted below, same rule that makes extraction
worth anything in test_card_helpers.py).

Pinned (card: operator report 2026-10-22, "comments is not defined"):

  1. ``renderSimple`` renders N cards for N nodes in the window.
  2. The comment count reads the node's own ``comments`` list — the
     regression: the count referenced a bare ``comments`` binding that
     does not exist, so EVERY simple-view render threw ReferenceError.
  3. ``last_comment`` wins over the list tail; missing both renders the
     no-comments placeholder.

The ``//`` comment lines and the ``"no comments yet"`` COPY are not bare
``comments`` tokens and must not trip the guard: the token scan below
excludes string literals and whole-line comments.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

JS_FILE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "timeline.js"
)

_GEO_FILE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "timelineGeo.js"
)

_HARNESS = """\
const fs = require("fs");
const src = fs.readFileSync(JS_FILE, "utf8");
// Strip the live-refresh timer: it fires only while the Timeline layout is
// active and would keep node alive past the assertion.
const code = src.replace(/setInterval[\s\S]*?, TL_LIVE_MS\);/, "");
// Sibling pure geometry (timelineGeo.js): capture the REAL ms/relTime so
// the harness exercises the same time projection the browser loads — the
// fallback `ms: () => null` would filter EVERY node out of the window and
// the tests would pass vacuously on an empty list.
const geoSrc = fs.readFileSync(GEO_FILE, "utf8");
const store = {};
const sandbox = {
  console,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  fetch: () => Promise.resolve({ json: () => Promise.resolve({ events: [], edges: [], lanes: [] }) }),
  document: { querySelector: () => null },
  localStorage: {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  },
  window: {},
  STATE: NODES_STATE,
  escapeHtml: (s) => String(s),
  openDetail: () => {},
  render: () => {},
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
const vm = require("vm");
vm.createContext(sandbox);
vm.runInContext(geoSrc, sandbox);
vm.runInContext(code + String.fromCharCode(10) + "this.__render = window._renderTimelineView; this.__setView = window.setTimelineView;", sandbox);
sandbox.__setView("simple");
sandbox.__canvas = { innerHTML: "" };
vm.runInContext("__render(__canvas);", sandbox);
console.log(JSON.stringify({ html: sandbox.__canvas.innerHTML }));
"""


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _render(nodes: list[dict]) -> str:
    """Render the timeline simple view for ``nodes``; return the HTML."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = (
        f"const JS_FILE = {json.dumps(str(JS_FILE))};\n"
        f"const GEO_FILE = {json.dumps(str(_GEO_FILE))};\n"
        f"const NODES_STATE = {{ graph: {{ nodes: {json.dumps(nodes)} }}, layout: 'timeline' }};\n"
        + _HARNESS
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(proc.stdout.strip())["html"]


def _nodes(n: int = 2, **over: object) -> list[dict]:
    # Clock-relative fixture (fix 2026-10-23): the simple view only renders
    # nodes active inside the 24h window, so a hardcoded calendar date ages
    # out and the whole file goes red overnight (release gate 2026-10-23).
    # Stamp one hour ago — safely inside any window the view offers.
    fresh = (
        datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    ).isoformat()
    base = {
        "id": "t1",
        "status": "in_progress",
        "title": "first",
        "last_activity": fresh,
        "comments": [
            {"author": "a", "text": "one"},
            {"author": "b", "text": "two"},
        ],
    }
    base.update(over)
    out = [dict(base)]
    for i in range(1, n):
        other = dict(base, id=f"t{i + 1}", title=f"card {i + 1}")
        out.append(other)
    return out


# === the regression ==========================================================


def test_simple_view_renders_one_card_per_node():
    # Arrange
    nodes = _nodes(3)
    # Act
    html = _render(nodes)
    # Assert
    assert html.count('class="tl-card"') == 3


def test_comment_count_reads_the_nodes_own_list():
    # Arrange — this exact shape threw ReferenceError before the fix:
    # the count referenced a bare `comments` binding; only `cs` exists.
    nodes = _nodes(1)
    # Act
    html = _render(nodes)
    # Assert
    assert "2 comments" in html


def test_last_comment_wins_over_the_list_tail():
    # Arrange
    nodes = _nodes(1, last_comment={"author": "z", "text_preview": "latest!"})
    # Act
    html = _render(nodes)
    # Assert
    assert "latest!" in html and "two" not in html.split("tl-simple")[1]


def test_no_comments_renders_the_placeholder():
    # Arrange
    nodes = _nodes(1, comments=[])
    # Act
    html = _render(nodes)
    # Assert
    assert "no comments yet" in html


# === the guard: no bare `comments` binding may reappear ======================


def test_no_bare_comments_binding_in_timeline_source():
    """The regression was a ReferenceError, not a wrong count: any bare
    ``comments`` identifier outside a string/comment is a render crash.
    Property accesses (``t.comments``), the local ``cs``, strings and
    whole-line comments are fine and must not trip this.
    """
    # Arrange
    src = JS_FILE.read_text(encoding="utf-8")
    stripped = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("//") or s.startswith("*"):
            continue
        stripped.append(re.sub(r"'[^']*'|\"[^\"]*\"", "", line))
    code = "\n".join(stripped)
    # Act
    bare = re.findall(r"(?<![.\w$])comments(?![\w$])", code)
    # Assert
    assert bare == [], f"bare `comments` binding(s) in timeline.js: {len(bare)}"


def test_timeline_module_touches_no_dom_at_import_time():
    """Same rule as test_card_helpers.py: a module that reads ``document``
    at import time cannot be require()d by node, so it stays untestable.
    """
    # Arrange
    body = JS_FILE.read_text(encoding="utf-8").split('"use strict";', 1)[1]
    head, _, _ = body.partition("function renderSimple")
    # Act — the live timer + entry-point publishing may touch document;
    # the module top-level (helpers + state) must not.
    # Assert
    assert "document" not in head


# EOF
