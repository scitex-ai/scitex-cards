#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the Graph layout's pure string builders (no mocks).

Exercises ``src/scitex_cards/_django/static/scitex_cards/board_v3/
17-graph-builders.js`` by ``require()``-ing the SHIPPED file and running the
REAL functions under node — the same arrangement as ``test_card_helpers.py``
(split PR-2) and the ``chat/`` module tests. There is deliberately no
hand-ported copy of the logic here: a mirror can drift from the file the
browser loads and then both "pass" while disagreeing.

These three lived inline in board_v3.html with ZERO behavioural coverage
until this extraction, and could not have been tested there: ``_graphSrc``
read ``STATE.graph`` and ``_graphHintHtml`` read ``GRAPH_FIT`` straight off
the page. Both now arrive as arguments, which is what makes every case below
a plain call rather than a page fixture.

Card ``scitex-cards-gui-board-v3-template-split-20260717`` (split PR-3).
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
    / "board_v3"
    / "17-graph-builders.js"
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
    """Run a JS fragment against the real 17-graph-builders.js; return stdout."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = f"const G = require({json.dumps(str(JS_FILE))});\n" + js
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip()


def _call(expr: str) -> object:
    """Evaluate a builders expression and JSON-decode the result."""
    return json.loads(_run(f"console.log(JSON.stringify({expr}));"))


def _src(visible: list, graph: object = None) -> object:
    return _call(f"G._graphSrc({json.dumps(visible)}, {json.dumps(graph)})")


def _card(cid: str, **kw) -> dict:
    card = {"id": cid, "title": cid, "project": "p", "status": "deferred"}
    card.update(kw)
    return card


def _edge(source: str, target: str, kind: str = "depends_on") -> dict:
    return {"source": source, "target": target, "kind": kind}


# === _graphSrc — the connectivity rule =====================================


def test_a_set_with_no_edges_at_all_draws_nothing():
    # The Graph layout's empty state. Returning null (not an empty diagram)
    # is what makes the caller show the explanatory panel instead of a blank
    # mermaid frame.
    # Arrange
    visible = [_card("a"), _card("b")]
    # Act
    built = _src(visible, {"edges": []})
    # Assert
    assert built is None


def test_an_edge_whose_endpoints_are_both_visible_is_drawn():
    # Arrange
    visible = [_card("a"), _card("b")]
    # Act
    built = _src(visible, {"edges": [_edge("a", "b")]})
    # Assert
    assert "  a --> b" in built["src"]


def test_an_edge_pointing_outside_the_visible_set_is_dropped():
    # THE FILTER RULE. A card filtered out of view must not drag its
    # dependency back in as a dangling mermaid node — that would draw an edge
    # to a node the operator cannot see and did not ask for.
    # Arrange
    visible = [_card("a")]
    # Act
    built = _src(visible, {"edges": [_edge("a", "off-screen")]})
    # Assert
    assert built is None


def test_a_parent_edge_is_dashed_so_hierarchy_reads_differently():
    # Hierarchy is a separate axis from blocking; the dashed arrow is the
    # only thing distinguishing them in the rendered diagram.
    # Arrange
    visible = [_card("hub"), _card("leaf", parent="hub")]
    # Act
    built = _src(visible, {"edges": []})
    # Assert
    assert "  hub -.-> leaf" in built["src"]


def test_a_card_that_is_its_own_parent_does_not_draw_a_self_loop():
    # Arrange
    visible = [_card("a", parent="a")]
    # Act
    built = _src(visible, {"edges": []})
    # Assert
    assert built is None


def test_a_parent_outside_the_visible_set_is_dropped():
    # Arrange
    visible = [_card("leaf", parent="hub")]
    # Act
    built = _src(visible, {"edges": []})
    # Assert
    assert built is None


def test_disconnected_cards_are_counted_but_not_drawn():
    # The hint line's honesty depends on this: cards matching the filters but
    # connected to nothing are reported as hidden rather than silently lost.
    # Arrange
    visible = [_card("a"), _card("b"), _card("lonely")]
    # Act
    built = _src(visible, {"edges": [_edge("a", "b")]})
    # Assert
    assert built["stats"] == {"connected": 2, "dep": 1, "parent": 0, "hidden": 1}


@pytest.mark.parametrize("axis", ["dep", "parent"])
def test_dep_and_parent_edges_are_counted_separately(axis):
    # A board with one of each must report one of each — collapsing the two
    # axes into a single number would make the hint line lie about what the
    # diagram actually draws.
    # Arrange
    visible = [_card("a"), _card("b"), _card("c", parent="a")]
    # Act
    built = _src(visible, {"edges": [_edge("a", "b")]})
    # Assert
    assert built["stats"][axis] == 1


def test_a_blocks_edge_is_drawn_solid_like_depends_on():
    # The server already normalises direction (source=prereq), so `blocks`
    # and `depends_on` render identically — only `parent` differs.
    # Arrange
    visible = [_card("a"), _card("b")]
    # Act
    built = _src(visible, {"edges": [_edge("a", "b", "blocks")]})
    # Assert
    assert "  a --> b" in built["src"]


def test_the_diagram_declares_itself_a_left_to_right_flowchart():
    # Arrange
    visible = [_card("a"), _card("b")]
    # Act
    built = _src(visible, {"edges": [_edge("a", "b")]})
    # Assert
    assert built["src"].startswith("flowchart LR")


# === _graphSrc — the escaping rules that keep mermaid parseable ============


def test_a_card_id_with_mermaid_hostile_characters_is_sanitised():
    # mermaid node ids accept only [A-Za-z0-9_-]; a real card id like
    # "help-agent:waiting/1" would end the node declaration early and the
    # whole diagram would fail to parse.
    # Arrange
    visible = [_card("a:b/c"), _card("d")]
    # Act
    built = _src(visible, {"edges": [_edge("a:b/c", "d")]})
    # Assert
    assert "  a_b_c --> d" in built["src"]


@pytest.mark.parametrize("hostile", ['"', "[", "]"])
def test_a_title_containing_quotes_or_brackets_cannot_break_the_label(hostile):
    # The label sits inside a mermaid ["..."] literal, so an unescaped quote
    # or bracket terminates it early and corrupts the whole diagram.
    # Arrange
    visible = [_card("a", task='say "hi" [now]'), _card("b")]
    # Act
    built = _src(visible, {"edges": [_edge("a", "b")]})
    label = built["src"].split("\n")[1].split('["', 1)[1].rsplit('"]', 1)[0]
    # Assert
    assert hostile not in label


def test_a_very_long_title_is_truncated_so_one_card_cannot_stretch_the_diagram():
    # Arrange
    visible = [_card("a", task="x" * 200), _card("b")]
    # Act
    built = _src(visible, {"edges": [_edge("a", "b")]})
    # Assert
    assert "x" * 61 not in built["src"]


def test_the_task_text_is_preferred_over_the_title():
    # Matches the card renderer, which shows `task` and falls back to title.
    # Arrange
    visible = [_card("a", task="the real line", title="the fallback"), _card("b")]
    # Act
    built = _src(visible, {"edges": [_edge("a", "b")]})
    # Assert
    assert "the real line" in built["src"]


def test_the_title_is_used_when_there_is_no_task_text():
    # Arrange
    visible = [_card("a", title="only a title"), _card("b")]
    # Act
    built = _src(visible, {"edges": [_edge("a", "b")]})
    # Assert
    assert "only a title" in built["src"]


# === _graphSrc — status classes come from the SSOT, never a second table ===


def test_a_node_carries_its_raw_status_class():
    # RAW status, not a 4-bucket collapse, so the in-board diagram matches
    # the Python build_mermaid() artifacts.
    # Arrange
    visible = [_card("a", status="in_progress"), _card("b")]
    # Act
    built = _src(visible, {"edges": [_edge("a", "b")]})
    # Assert
    assert ":::st-in_progress" in built["src"]


def test_a_card_with_no_status_falls_back_to_deferred():
    # Arrange
    visible = [{"id": "a", "title": "a"}, {"id": "b", "title": "b"}]
    # Act
    built = _src(visible, {"edges": [_edge("a", "b")]})
    # Assert
    assert ":::st-deferred" in built["src"]


def test_class_definitions_are_emitted_from_the_payload_status_colors():
    # Arrange
    graph = {
        "edges": [_edge("a", "b")],
        "status_colors": {"done": {"fill": "#0f0", "stroke": "#000"}},
    }
    # Act
    built = _src([_card("a"), _card("b")], graph)
    # Assert
    assert "  classDef st-done fill:#0f0,stroke:#000,stroke-width:1px,color:#222;" \
        in built["src"]


def test_a_dashed_status_adds_a_stroke_dasharray():
    # Arrange
    graph = {
        "edges": [_edge("a", "b")],
        "status_colors": {
            "cancelled": {"fill": "#111", "stroke": "#222", "dashed": True}
        },
    }
    # Act
    built = _src([_card("a"), _card("b")], graph)
    # Assert
    assert "stroke-dasharray:5 3;" in built["src"]


def test_no_status_colors_means_no_class_definitions_rather_than_a_throw():
    # The payload is the SSOT; an older server that omits status_colors must
    # degrade to an uncoloured diagram, not a broken layout.
    # Arrange
    # Act
    built = _src([_card("a"), _card("b")], {"edges": [_edge("a", "b")]})
    # Assert
    assert "classDef" not in built["src"]


def test_an_absent_graph_payload_is_survivable():
    # _graphSrc is called during prewarm, which can fire before the first
    # /graph response has landed.
    # Arrange
    # Act
    built = _src([_card("a", parent="b"), _card("b")], None)
    # Assert
    assert "  b -.-> a" in built["src"]


# === _graphHintHtml — the counts line ======================================


def _hint(stats: dict, note: str = "", fit: bool = True) -> str:
    return _call(
        f"G._graphHintHtml({json.dumps(stats)}, {json.dumps(note)}, {json.dumps(fit)})"
    )


_STATS = {"connected": 2, "dep": 1, "parent": 0, "hidden": 3}


@pytest.mark.parametrize(
    "fragment",
    [
        "<strong>2</strong> connected node",
        "<strong>1</strong> depends_on / blocks edge",
        "3 disconnected card",
    ],
)
def test_the_hint_reports_every_count_it_was_given(fragment):
    # Arrange
    # Act
    out = _hint(_STATS)
    # Assert
    assert fragment in out


@pytest.mark.parametrize(("fit", "label"), [(True, "1:1"), (False, "Fit")])
def test_the_fit_flag_picks_the_button_label(fit, label):
    # The label says what the button will DO, so it is the inverse of the
    # current mode — this is exactly the kind of inversion that used to be
    # untestable while GRAPH_FIT was read off the page.
    # Arrange
    # Act
    out = _hint(_STATS, fit=fit)
    # Assert
    assert label in out


def test_a_note_is_appended_when_given():
    # Arrange
    # Act
    out = _hint(_STATS, note="cached")
    # Assert
    assert "not drawn. cached</span>" in out


def test_no_note_leaves_no_dangling_separator():
    # Arrange
    # Act
    out = _hint(_STATS, note="")
    # Assert
    assert "not drawn.</span>" in out


@pytest.mark.parametrize(
    ("key", "count", "marker"),
    [
        ("connected", 1, "connected node "),
        ("connected", 2, "connected nodes"),
        ("dep", 1, "blocks edge "),
        ("dep", 2, "blocks edges"),
        ("parent", 1, "parent edge "),
        ("parent", 2, "parent edges"),
    ],
)
def test_counts_are_pluralised(key, count, marker):
    # Arrange
    stats = dict(_STATS, **{key: count})
    # Act
    out = _hint(stats)
    # Assert
    assert marker in out


def test_the_hint_button_calls_the_template_toggle():
    # toggleGraphFit deliberately stayed in the template (DOM + localStorage),
    # so the emitted onclick must still name it or the button goes dead.
    # Arrange
    # Act
    out = _hint(_STATS)
    # Assert
    assert 'onclick="toggleGraphFit()"' in out


# === _graphEmptyHtml — the explanatory empty state =========================


def _empty(count: int) -> str:
    return _call(f"G._graphEmptyHtml({json.dumps(count)})")


def test_the_empty_panel_reports_how_many_cards_matched():
    # Arrange
    # Act
    out = _empty(7)
    # Assert
    assert "<strong>7 cards</strong>" in out


def test_the_empty_panel_pluralises_a_single_card():
    # Arrange
    # Act
    out = _empty(1)
    # Assert
    assert "<strong>1 card</strong>" in out


@pytest.mark.parametrize("layout", ["Timeline", "Wall"])
def test_the_empty_panel_names_the_layouts_that_do_show_everything(layout):
    # The empty state is an explanation, not a dead end: it points at the two
    # layouts that draw unconnected cards.
    # Arrange
    # Act
    out = _empty(0)
    # Assert
    assert layout in out


# === the module contract ===================================================


def test_the_module_publishes_itself_on_the_stx_namespace():
    # Arrange
    # Act
    published = _run(
        "require(" + json.dumps(str(JS_FILE)) + ");"
        "console.log(typeof globalThis.STX.graphBuilders._graphSrc === 'function');"
    )
    # Assert
    assert published == "true"


def test_the_template_loads_the_module():
    # The <script defer> tag is the ONLY thing that registers this module with
    # the page. Drop it and the Graph layout throws at runtime.
    # Arrange
    template = TEMPLATE.read_text(encoding="utf-8")
    # Act
    # Assert
    assert "board_v3/17-graph-builders.js" in template


@pytest.mark.parametrize(
    "builder",
    ["function _graphSrc(", "function _graphHintHtml(", "function _graphEmptyHtml("],
)
def test_the_template_no_longer_defines_the_builders_itself(builder):
    # A second copy is the drift this extraction exists to remove.
    # Arrange
    template = TEMPLATE.read_text(encoding="utf-8")
    # Act
    # Assert
    assert builder not in template


@pytest.mark.parametrize(
    "call",
    [
        "STX.graphBuilders._graphSrc(visible, STATE.graph)",
        "STX.graphBuilders._graphHintHtml(stats, note, GRAPH_FIT)",
    ],
)
def test_the_template_passes_the_page_globals_it_used_to_read(call):
    # The whole point of the signature change: STATE.graph and GRAPH_FIT are
    # arguments now. If a call site is left bare the module reads undefined
    # and the diagram silently loses its edges or its button label.
    # Arrange
    template = TEMPLATE.read_text(encoding="utf-8")
    # Act
    # Assert
    assert call in template


@pytest.mark.parametrize(
    "kept", ["function _mountGraphSvg(", "function toggleGraphFit("]
)
def test_the_dom_half_deliberately_stayed_in_the_template(kept):
    # Pins the boundary this PR drew. Moving these here later without giving
    # them a DOM-free shape would make the module un-require()-able by node
    # and quietly kill every test in this file.
    # Arrange
    template = TEMPLATE.read_text(encoding="utf-8")
    # Act
    # Assert
    assert kept in template


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
