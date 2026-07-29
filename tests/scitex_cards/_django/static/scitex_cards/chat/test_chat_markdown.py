#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A message body may never become markup.

The DM board renders bodies written by ANY authenticated caller on the rail,
into the operator's browser. So the renderer's contract is not "produce nice
HTML" but "produce nodes, never markup". These tests pin both halves: the
markdown the operator actually writes renders, and the hostile shapes stay
inert.

Each test drives the REAL module under node against a minimal DOM, so a
regression fails here rather than in the operator's browser.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
    / "chat_markdown.js"
)

#: A DOM small enough to read. `serialize` walks the produced nodes and rebuilds
#: markup from them — so if the renderer ever emitted raw markup as TEXT, the
#: serialized form shows it escaped, and if it built a real element, the tag
#: appears. That distinction is exactly what the injection tests assert on.
HARNESS = """
// The module is a UMD. Under `node -e` a CommonJS `module` EXISTS, so it takes
// the exports branch and never attaches itself to the global — the harness has
// to pick it up from there. Getting this wrong makes every test fail with
// "ChatMarkdown is not defined", which reads exactly like a broken renderer.
if (typeof module === "object" && module.exports && module.exports.render) {
  globalThis.ChatMarkdown = module.exports;
}
globalThis.self = globalThis;
function mkNode(tag) {
  return {
    tagName: tag, className: "", children: [], attrs: {}, _text: null,
    appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { this.attrs[k] = v; },
    set textContent(v) { this._text = v; this.children = []; },
    get textContent() { return this._text; },
  };
}
globalThis.document = {
  createElement: (t) => mkNode(t),
  createTextNode: (t) => ({ tagName: "#text", value: String(t) }),
  createDocumentFragment: () => mkNode("#fragment"),
};

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
globalThis.serialize = function (n) {
  if (n.tagName === "#text") return esc(n.value);
  const inner = (n.children || []).map(serialize).join("");
  if (n.tagName === "#fragment") return inner;
  const a = Object.entries(n.attrs || {})
    .map(([k, v]) => ` ${k}="${esc(v)}"`).join("");
  const body = n._text !== null && n._text !== undefined ? esc(n._text) : inner;
  return `<${n.tagName}${a}>${body}</${n.tagName}>`;
};
globalThis.out = function (text) {
  console.log(JSON.stringify({ html: serialize(ChatMarkdown.render(text)) }));
};
"""


def _render(text: str) -> str:
    """Render ``text`` through the real module; return serialized markup."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - environment guard
        pytest.skip("node is not installed, so the shipped module cannot run")
    src = MODULE.read_text(encoding="utf-8")
    script = src + HARNESS + f"\nout({json.dumps(text)});\n"
    proc = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:  # pragma: no cover - surfaced as a failure
        raise AssertionError(f"node failed: {proc.stderr[:2000]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])["html"]


# === the hostile half ======================================================


def test_a_script_tag_in_a_message_does_not_become_an_element():
    """The whole reason this module builds nodes instead of an HTML string."""
    # Arrange
    body = "<script>alert(1)</script>"
    # Act
    html = _render(body)
    # Assert
    assert "<script>" not in html, (
        "a message body produced a real <script> element; the renderer must "
        "emit text nodes, never markup"
    )


def test_an_img_onerror_payload_stays_text():
    """The classic escape-missed-a-branch payload.

    Asserts on the ESCAPED form being present rather than on the raw string
    being absent: "onerror" legitimately appears in the output as literal text,
    so absence would be the wrong property. What must not exist is a real
    <img> ELEMENT, and its presence as escaped text proves the body was
    rendered rather than parsed.
    """
    # Arrange
    body = "<img src=x onerror=alert(1)>"
    # Act
    html = _render(body)
    # Assert
    assert "&lt;img" in html, (
        f"the payload was not rendered as inert text; got: {html[:200]}"
    )


def test_a_javascript_url_is_not_turned_into_a_link():
    """Anchors are the one node that can still carry an executable payload."""
    # Arrange
    body = "[click me](javascript:alert(1))"
    # Act
    html = _render(body)
    # Assert
    assert "javascript:" not in html, (
        "a javascript: URL became an href; only http/https/mailto may link"
    )


def test_a_code_fence_containing_markup_is_shown_not_parsed():
    """A fence is verbatim: this is where string-builders leak."""
    # Arrange
    body = "```\n<script>alert(1)</script>\n```"
    # Act
    html = _render(body)
    # Assert
    assert "&lt;script&gt;" in html, "fence content was not rendered as text"


def test_the_module_never_uses_innerhtml():
    """A structural guard: string-building is how the injection class returns.

    If a future contributor switches to innerHTML this fails, which is the
    point — the safety property is 'builds nodes', not 'escapes carefully'.
    """
    # Arrange — strip comments first. The module's own header EXPLAINS that it
    # never uses these APIs, so a naive scan matches the prose describing the
    # guard and fails on a compliant file. The guard must read code, not
    # commentary; this caught itself on the first run.
    src = MODULE.read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    # Act
    offenders = [
        k for k in ("innerHTML", "outerHTML", "insertAdjacentHTML") if k in code
    ]
    # Assert
    assert offenders == [], f"markdown renderer builds markup strings: {offenders}"


# === the useful half =======================================================


def test_a_fenced_code_block_renders_as_pre_code():
    # Arrange
    body = "```\nls -l\n```"
    # Act
    html = _render(body)
    # Assert
    assert "<pre" in html and "<code>" in html


def test_a_table_renders_as_a_table():
    """Today's incident reports are tables; they render as pipes right now."""
    # Arrange
    body = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    # Act
    html = _render(body)
    # Assert
    assert "<table" in html and "<td>" in html


def test_bold_renders_as_strong():
    # Arrange / Act
    html = _render("this is **important** here")
    # Assert
    assert "<strong>" in html


def test_a_bullet_list_renders_as_list_items():
    # Arrange / Act
    html = _render("- one\n- two")
    # Assert
    assert html.count("<li>") == 2


def test_inline_code_renders_as_code():
    # Arrange / Act
    html = _render("run `pip install` now")
    # Assert
    assert "<code>" in html


def test_a_heading_renders_as_a_heading():
    # Arrange / Act
    html = _render("## Findings")
    # Assert
    assert "<h2" in html


def test_an_https_link_becomes_an_anchor():
    # Arrange / Act
    html = _render("[PR](https://github.com/x/y/pull/1)")
    # Assert
    assert 'href="https://github.com/x/y/pull/1"' in html


def test_plain_text_survives_unchanged():
    """The common case must not be mangled by any of the above."""
    # Arrange / Act
    html = _render("just a sentence")
    # Assert
    assert "just a sentence" in html


# EOF
