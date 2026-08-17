#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the board's two pure card helpers (no mocks).

Exercises ``src/scitex_cards/_django/static/scitex_cards/board_v3/
16-card-helpers.js`` by ``require()``-ing the SHIPPED file and running the
REAL functions under node — the same arrangement as the ``chat/`` module
tests. There is deliberately no hand-ported copy of the logic here: a mirror
can drift from the file the browser loads and then both "pass" while
disagreeing.

Both functions lived inline in board_v3.html with ZERO behavioural coverage
until this extraction, which is the point of it:

  1. ``escapeHtml`` — the board's ONLY XSS boundary. Every template literal
     that interpolates card data goes through it, and four already-extracted
     modules read it off ``window``.
  2. ``cardOwner`` — the client mirror of :func:`scitex_cards._owner.card_owner`.
     Pinned against the Python SSOT here so the two cannot drift apart:
     ``agent`` falls back to ``assignee``, blank means owner-less.

Card ``scitex-cards-gui-board-v3-template-split-20260717`` (split PR-2).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scitex_cards._owner import card_owner

JS_FILE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "16-card-helpers.js"
)

TEMPLATE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "board_v3.html"
)


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(js: str) -> str:
    """Run a JS fragment against the real 16-card-helpers.js; return stdout."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = f"const CardHelpers = require({json.dumps(str(JS_FILE))});\n" + js
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip()


def _call(expr: str) -> object:
    """Evaluate a CardHelpers expression and JSON-decode the result."""
    return json.loads(_run(f"console.log(JSON.stringify({expr}));"))


def _escape(raw: object) -> str:
    return _call(f"CardHelpers.escapeHtml({json.dumps(raw)})")


def _owner(card: object) -> object:
    return _call(f"CardHelpers.cardOwner({json.dumps(card)})")


# === escapeHtml — the XSS boundary =========================================


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("&", "&amp;"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ('"', "&quot;"),
        ("'", "&#39;"),
    ],
)
def test_each_significant_character_is_escaped(raw, escaped):
    # Arrange
    # Act
    out = _escape(raw)
    # Assert
    assert out == escaped


def test_a_script_tag_cannot_survive_as_markup():
    # The concrete attack the helper exists to stop: a card title that would
    # otherwise close the surrounding literal and open a real <script>.
    # Arrange
    title = "<script>alert(1)</script>"
    # Act
    out = _escape(title)
    # Assert
    assert out == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_an_attribute_break_out_is_escaped():
    # Card ids are interpolated into onclick="…('${escapeHtml(id)}')" —
    # a bare quote there would end the attribute and inject a handler.
    # Arrange
    card_id = "x' onclick='steal()"
    # Act
    out = _escape(card_id)
    # Assert
    assert "'" not in out


def test_ampersand_is_escaped_before_the_others_so_entities_are_not_doubled():
    # A single pass over the character class, not sequential replaces —
    # otherwise "<" would become "&amp;lt;" and render as literal text.
    # Arrange
    # Act
    out = _escape("<&>")
    # Assert
    assert out == "&lt;&amp;&gt;"


def test_plain_text_is_returned_unchanged():
    # Arrange
    # Act
    out = _escape("resident board rollout")
    # Assert
    assert out == "resident board rollout"


def test_null_becomes_the_empty_string():
    # Cards routinely carry null fields; the board must render "" for them,
    # never the text "null".
    # Arrange
    # Act
    out = _call("CardHelpers.escapeHtml(null)")
    # Assert
    assert out == ""


def test_undefined_becomes_the_empty_string():
    # Arrange
    # Act
    out = _call("CardHelpers.escapeHtml(undefined)")
    # Assert
    assert out == ""


def test_zero_is_stringified_not_treated_as_absent():
    # `0` is falsy but present — a `!s` guard would wrongly blank it.
    # Arrange
    # Act
    out = _call("CardHelpers.escapeHtml(0)")
    # Assert
    assert out == "0"


def test_a_number_is_stringified():
    # Arrange
    # Act
    out = _call("CardHelpers.escapeHtml(42)")
    # Assert
    assert out == "42"


# === cardOwner — the owner SSOT ============================================


def test_agent_is_the_owner():
    # Arrange
    card = {"agent": "scitex-cards-gui", "assignee": "someone-else"}
    # Act
    owner = _owner(card)
    # Assert
    assert owner == "scitex-cards-gui"


def test_assignee_is_the_fallback_when_there_is_no_agent():
    # Arrange
    card = {"assignee": "scitex-cards"}
    # Act
    owner = _owner(card)
    # Assert
    assert owner == "scitex-cards"


def test_a_card_with_neither_is_owner_less():
    # Owner-less is null, NOT a retired fallback identity — the board buckets
    # these into an explicitly-labeled lane (operator mandate 2026-06-26).
    # Arrange
    card = {"title": "orphan"}
    # Act
    owner = _owner(card)
    # Assert
    assert owner is None


def test_a_blank_owner_counts_as_owner_less():
    # Arrange
    card = {"agent": "   "}
    # Act
    owner = _owner(card)
    # Assert
    assert owner is None


def test_surrounding_whitespace_is_stripped():
    # Arrange
    card = {"agent": "  scitex-cards-gui  "}
    # Act
    owner = _owner(card)
    # Assert
    assert owner == "scitex-cards-gui"


def test_a_missing_card_is_owner_less_rather_than_a_throw():
    # render() maps over nodes that can be undefined mid-poll.
    # Arrange
    # Act
    owner = _call("CardHelpers.cardOwner(null)")
    # Assert
    assert owner is None


@pytest.mark.parametrize(
    "card",
    [
        {"agent": "scitex-cards-gui", "assignee": "someone-else"},
        {"assignee": "scitex-cards"},
        {"agent": "  spaced  "},
        {"agent": "   "},
        {"title": "orphan"},
    ],
)
def test_the_js_mirror_agrees_with_the_python_owner_ssot(card):
    # The two implementations of one rule must not drift; this is the test
    # that fails if either side changes alone.
    # Arrange
    # Act
    from_js = _owner(card)
    from_python = card_owner(card)
    # Assert
    assert from_js == from_python


# === the published surface =================================================


def test_the_module_publishes_itself_on_the_stx_namespace():
    # The template and the extracted modules reach it as
    # window.STX.cardHelpers at call time; a require()-only export would
    # leave the browser with nothing.
    # Arrange
    # Act
    published = _run(
        "console.log(typeof globalThis.STX.cardHelpers.escapeHtml"
        ' === "function");'
    )
    # Assert
    assert published == "true"


def test_the_template_loads_the_module():
    # The <script defer> tag is the ONLY thing that registers this module with
    # the page. Drop it and every escapeHtml call in the template throws at
    # runtime — nothing else in the suite would notice.
    # Arrange
    template = TEMPLATE.read_text(encoding="utf-8")
    # Act
    # Assert
    assert "board_v3/16-card-helpers.js" in template


def test_the_template_alias_delegates_rather_than_reimplementing():
    # A second copy of the escaping rule is the drift this extraction exists
    # to remove; the template must call through, not re-declare the table.
    # Arrange
    template = TEMPLATE.read_text(encoding="utf-8")
    # Act
    # Assert
    assert "return STX.cardHelpers.escapeHtml(s);" in template


def test_the_template_alias_is_a_function_declaration_not_an_assignment():
    # THE FORM IS LOAD-BEARING, and the delegation test above does NOT pin it.
    # `const escapeHtml = function (s) { return STX.cardHelpers.escapeHtml(s); }`
    # satisfies that test verbatim and still breaks the page: const/let create
    # bindings in the global LEXICAL environment, NOT properties of the global
    # object, so only a `function` declaration (or `var`) puts the name on
    # `window`. Four already-extracted modules read it off `window` —
    # timeline.js, 14-matrix.js, 10-agent-avatar.js, timelineSelect.js — so an
    # assignment form breaks their escaping UNCONDITIONALLY, not intermittently.
    # A comment asking the next reader not to "simplify" this is not a barrier;
    # this assertion is. (Found in review of #847 by scitex-cards.)
    # Arrange
    template = TEMPLATE.read_text(encoding="utf-8")
    # Act
    # Assert
    assert "\n    function escapeHtml(s) {" in template


def test_the_template_no_longer_carries_its_own_escape_table():
    # COMMENTS ARE STRIPPED FIRST, and that is not a loosening.
    # This assertion read the RAW text, so a `//` line explaining WHY the
    # double-escape bug happened — which necessarily quotes `&quot;` — failed it
    # (2026-08-17). Prose ABOUT an entity is indistinguishable from the entity
    # to a substring search, so the gate fired on the documentation of the fix
    # rather than on a regression. An escape table is CODE; it cannot hide
    # inside a line comment, so stripping comments narrows what the test sees to
    # exactly what it is about and it can still go red on a real table.
    # Only WHOLE-LINE comments are dropped, never a trailing `//`: cutting at
    # the first `//` anywhere would also truncate any line containing an
    # "https://" literal, and truncation can only ever HIDE a table — a false
    # pass. Dropping whole comment lines cannot.
    # Arrange
    extra_js = TEMPLATE.read_text(encoding="utf-8").split("{% block extra_js %}", 1)[1]
    # Act
    code_only = "\n".join(
        line for line in extra_js.splitlines() if not line.lstrip().startswith("//")
    )
    # Assert
    assert "&quot;" not in code_only


def test_the_module_touches_no_dom_at_import_time():
    # The rule that makes extraction worth anything: a module that reads
    # `document` at import time cannot be require()d by node, so it stays
    # untestable even after it moves out of the template.
    # Arrange
    source = JS_FILE.read_text(encoding="utf-8")
    body = source.split('"use strict";', 1)[1]
    # Act
    # Assert
    assert "document" not in body
