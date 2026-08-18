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


def _src(visible: list, graph: object = None, cap: object = None) -> object:
    return _call(
        f"G._graphSrc({json.dumps(visible)}, {json.dumps(graph)}, {json.dumps(cap)})"
    )


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
    assert built["stats"] == {
        "connected": 2,
        "nodes": 2,
        "dep": 1,
        "parent": 0,
        "hidden": 1,
        "total": 2,
        "omitted": 0,
    }


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


# === _graphSrc — the node cap that keeps the diagram drawable ==============
#
# Card board-graph-layout-exceeds-mermaid-maxtextsize-20260815. On the live
# board this builder emitted 155,977 characters for 407 connected cards
# against mermaid's 50,000 ceiling, and the panel showed an error image with
# zero console errors for weeks.


def _chain(n: int) -> tuple[list, dict]:
    """A connected chain of `n` cards — the shape the real board reached."""
    cards = [_card(f"c{i:04d}") for i in range(n)]
    edges = [_edge(f"c{i:04d}", f"c{i + 1:04d}") for i in range(n - 1)]
    return cards, {"edges": edges}


def test_a_board_within_the_cap_still_draws_every_connected_card():
    # The cap must be invisible on a normal board — this is the regression
    # guard for the 99% case, and it is why the cap is not simply "small".
    # Arrange
    cards, graph = _chain(30)
    # Act
    built = _src(cards, graph)
    # Assert
    assert built["stats"]["connected"] == 30

def test_a_board_within_the_cap_omits_nothing():
    # Arrange
    cards, graph = _chain(30)
    # Act
    built = _src(cards, graph)
    # Assert
    assert built["stats"]["omitted"] == 0


def test_a_board_over_the_cap_draws_no_more_than_the_cap():
    # Arrange
    cards, graph = _chain(400)
    # Act
    built = _src(cards, graph)
    # Assert
    assert built["stats"]["connected"] <= 120


def test_the_cap_reports_the_full_connected_total_it_selected_from():
    # The denominator has to survive the cap or the hint line cannot say
    # "120 of 399" and the truncation becomes silent — which is the whole
    # defect this card is about.
    # Arrange
    cards, graph = _chain(400)
    # Act
    built = _src(cards, graph)
    # Assert
    assert built["stats"]["total"] == 400


def test_the_cap_reports_how_many_it_left_out():
    # Arrange
    cards, graph = _chain(400)
    # Act
    built = _src(cards, graph)
    # Assert
    stats = built["stats"]
    assert stats["omitted"] == stats["total"] - stats["connected"]


def test_every_drawn_node_still_has_at_least_one_drawn_edge():
    # A cap that kept nodes whose neighbours were dropped would produce a
    # field of isolated dots — which is what a naive top-N-by-degree
    # selection does to a hub-and-spoke board.
    # Arrange
    cards, graph = _chain(400)
    # Act
    built = _src(cards, graph)
    lines = built["src"].split("\n")
    drawn = {ln.split("[", 1)[0].strip() for ln in lines if "[" in ln}
    linked = set()
    for ln in lines:
        if "-->" in ln:
            left, right = ln.split("-->")
            linked.update({left.strip(), right.strip()})
    # Assert
    assert drawn == linked


def test_a_hub_keeps_its_children_rather_than_being_drawn_alone():
    # The hub-and-spoke case specifically: 300 children of one parent. Ranking
    # nodes by degree alone would select the hub (degree 300) plus 119 other
    # hubs and drop every child, drawing nothing at all.
    # Arrange
    cards = [_card("hub")] + [_card(f"kid{i:03d}", parent="hub") for i in range(300)]
    # Act
    built = _src(cards, {"edges": []})
    # Assert
    assert built["stats"]["connected"] == 120


@pytest.mark.parametrize("hub", ["hub-a", "hub-b", "hub-c"])
def test_one_big_hub_does_not_eat_the_whole_budget(hub):
    # MEASURED ON THE LIVE BOARD: the first version of this cap gave every
    # slot to a single 300-child epic, so the panel drew one star and none of
    # the board's shape. Each seed now takes a share before the next gets a
    # turn — three equal hubs must all appear.
    # Arrange
    cards = []
    for name in ("hub-a", "hub-b", "hub-c"):
        cards.append(_card(name))
        cards += [_card(f"{name}-kid{n:03d}", parent=name) for n in range(100)]
    # Act
    built = _src(cards, {"edges": []})
    # Assert
    assert f"  {hub}[" in built["src"]


def test_the_cap_can_be_lifted_for_a_caller_that_wants_everything():
    # The equivalence proof and any "draw it all" caller need an escape
    # hatch; 0 means uncapped.
    # Arrange
    cards, graph = _chain(400)
    # Act
    built = _src(cards, graph, cap=0)
    # Assert
    assert built["stats"]["connected"] == 400


def test_the_selection_is_deterministic_for_the_same_board():
    # Ties break on id, so the operator does not get a different subset every
    # repaint of the same data.
    # Arrange
    cards, graph = _chain(400)
    # Act
    first = _src(cards, graph)
    second = _src(cards, graph)
    # Assert
    assert first["src"] == second["src"]


def test_the_counts_describe_the_edges_actually_drawn():
    # An edge whose far end was capped away is not on the diagram, so
    # counting it would make the hint line describe a graph nobody can see.
    # Arrange
    cards, graph = _chain(400)
    # Act
    built = _src(cards, graph)
    # Assert
    assert built["stats"]["dep"] == built["src"].count(" --> ")


# A board of the live one's shape: 4,488 cards of which ~800 are connected,
# with labels the length real cards actually have. Built INSIDE node rather
# than passed in as JSON — 4,488 cards do not fit in an argv (measured: the
# first version of this test died with "Argument list too long").
_BIG_BOARD_JS = """
const N = 800, LONELY = 3688;
const id = (i) => "card-" + String(i).padStart(4, "0");
const cards = [], edges = [];
for (let i = 0; i < N; i++) {
  cards.push({id: id(i), project: "scitex-cards", status: "in_progress",
              task: "a card title of the length real card titles have " + i});
}
for (let i = 0; i + 1 < N; i++) {
  edges.push({source: id(i), target: id(i + 1), kind: "depends_on"});
}
for (let i = 0; i < LONELY; i++) {
  cards.push({id: "lonely-" + i, project: "scitex-cards", status: "deferred",
              task: "a card connected to nothing at all " + i});
}
const graph = {edges: edges};
"""


def _big_board(cap: object) -> dict:
    return json.loads(
        _run(
            _BIG_BOARD_JS
            + f"const built = G._graphSrc(cards, graph, {json.dumps(cap)});"
            "console.log(JSON.stringify({len: built.src.length,"
            " limit: G.MERMAID_MAX_TEXT, stats: built.stats}));"
        )
    )


def test_a_real_sized_board_now_fits_under_mermaids_ceiling():
    # THE BUG, MEASURED. The live board produced 155,977 characters against a
    # 50,000 ceiling and mermaid answered with an error image.
    # Arrange
    # Act
    out = _big_board(None)
    # Assert
    assert out["len"] < out["limit"]


def test_the_same_board_uncapped_is_what_used_to_break_it():
    # The other half of the proof: the cap is what fixed it, not a friendlier
    # fixture. Uncapped, this same board still blows past the ceiling — so
    # this test fails the moment the cap stops being applied.
    # Arrange
    # Act
    out = _big_board(0)
    # Assert
    assert out["len"] > out["limit"]


def test_the_real_sized_board_still_reports_its_full_connected_total():
    # Arrange
    # Act
    out = _big_board(None)
    # Assert
    assert out["stats"]["total"] == 800


# === _exceedsMermaidLimit — measure before asking ==========================


def _exceeds(src: str, limit: object = None) -> bool:
    return _call(f"G._exceedsMermaidLimit({json.dumps(src)}, {json.dumps(limit)})")


def test_a_source_over_the_ceiling_is_refused():
    # Arrange
    # Act
    # Assert
    assert _exceeds("x" * 60000) is True


def test_a_source_under_the_ceiling_is_allowed():
    # Arrange
    # Act
    # Assert
    assert _exceeds("flowchart LR") is False


def test_a_source_exactly_at_the_ceiling_is_allowed():
    # The limit is what mermaid accepts, so the boundary belongs on the
    # allowed side — an off-by-one here would refuse a diagram that renders.
    # Arrange
    limit = _call("G.MERMAID_MAX_TEXT")
    # Act
    # Assert
    assert _exceeds("x" * limit) is False


def test_the_ceiling_can_be_overridden_by_the_caller():
    # Arrange
    # Act
    # Assert
    assert _exceeds("x" * 20, 10) is True


# === _graphRenderFailed — a returned SVG is not a drawn diagram ============
#
# THE DEFECT, and the fixtures below are shaped from mermaid 10's REAL output
# measured in a browser on 2026-08-15 (probe recorded in the PR), not from
# what its docs suggest:
#
#   over the ceiling -> a VALID flowchart-v2 SVG with exactly ONE node whose
#     LABEL is "Maximum text size in diagram exceeded". No throw, no error
#     markup. This is what the live board displayed for weeks.
#   parse error      -> mermaid.render() rejects; the caller's catch has it.
#
# RULE FOR THESE FIXTURES (keep it): when a render shape is found in
# production that these do not cover, the fix ADDS THAT SHAPE HERE. They are
# a growing record of every output that has mattered, not a sample.

# Present in EVERY mermaid diagram, good or bad. It is why a string match on
# "error-icon" detects nothing — see the regression test below.
_MERMAID_STYLE = (
    "<style>#stx{font-family:trebuchet ms;}#stx .error-icon{fill:#a44141;}"
    "#stx .error-text{fill:#ddd;stroke:#ddd;}"
    "#stx .node rect{fill:#1f2020;}</style>"
)


def _diagram(node_count: int, label: str = "scitex-cards") -> str:
    """A mermaid flowchart-v2 SVG carrying `node_count` drawn nodes."""
    nodes = "".join(
        f'<g class="node default flowchart-label" id="flowchart-c{i}-{i}">'
        f'<rect class="basic label-container"></rect><g class="label">'
        f'<span class="nodeLabel">{label} {i}</span></g></g>'
        for i in range(node_count)
    )
    return (
        '<svg id="stx" aria-roledescription="flowchart-v2" role="graphics-document">'
        f'{_MERMAID_STYLE}<g class="nodes">{nodes}</g></svg>'
    )


# What mermaid hands back for an oversized source: one node, its own message.
_OVERFLOW_SVG = _diagram(1, "Maximum text size in diagram exceeded")


def _render_failed(svg: object, expected: int) -> bool:
    return _call(f"G._graphRenderFailed({json.dumps(svg)}, {json.dumps(expected)})")


def test_the_overflow_graphic_is_recognised_as_a_failed_render():
    # THE LIVE BUG. 120 nodes asked for, one node returned — a "successful"
    # render of mermaid's own complaint.
    # Arrange
    # Act
    # Assert
    assert _render_failed(_OVERFLOW_SVG, 120) is True


def test_the_boilerplate_error_classes_do_not_condemn_a_good_render():
    # REGRESSION ON MY OWN FIRST FIX. Matching "error-icon"/"error-text" was
    # the obvious check and it is worthless: every mermaid diagram carries
    # those class rules in its <style> block. A predicate built on them would
    # have blanked every healthy diagram, or (as it did) detected nothing.
    # Arrange
    good = _diagram(120)
    # Act
    # Assert
    assert "error-icon" in good and _render_failed(good, 120) is False


def test_a_render_that_drew_everything_asked_for_is_a_success():
    # Arrange
    # Act
    # Assert
    assert _render_failed(_diagram(7), 7) is False


def test_a_render_missing_even_one_node_is_a_failure():
    # Partial output is still not the diagram the operator was promised, and
    # the counts line above it would be describing a graph that is not there.
    # Arrange
    # Act
    # Assert
    assert _render_failed(_diagram(119), 120) is True


def test_an_error_diagram_is_recognised_even_though_mermaid_10_throws():
    # mermaid 10 rejects on a parse error rather than returning this, but
    # older and newer versions have returned an error SVG instead — cheap to
    # keep, and the node count alone would not name the cause.
    # Arrange
    svg = (
        '<svg aria-roledescription="error"><g><path class="error-icon"/>'
        '<text class="error-text">Syntax error in text</text></g></svg>'
    )
    # Act
    # Assert
    assert _render_failed(svg, 0) is True


@pytest.mark.parametrize("empty", ["", None])
def test_nothing_at_all_is_a_failed_render(empty):
    # Arrange
    # Act
    # Assert
    assert _render_failed(empty, 0) is True


def test_two_ids_that_escape_to_the_same_node_are_counted_once():
    # WHAT THE RENDER IS MEASURED AGAINST. _esc maps every punctuation
    # character to "_", so "a:b" and "a/b" are ONE mermaid node. Measuring a
    # render against the CARD count would report a flawless render as a
    # failure on any board holding such a pair, and blank the diagram.
    # Arrange
    visible = [_card("a:b"), _card("a/b"), _card("d")]
    graph = {"edges": [_edge("a:b", "d"), _edge("a/b", "d")]}
    # Act
    built = _src(visible, graph)
    # Assert
    assert built["stats"]["nodes"] == 2


def test_a_collision_does_not_make_a_good_render_look_failed():
    # THE INTERACTION, WHICH WAS CORRECT BY CONSTRUCTION AND NEVER EXECUTED.
    # `stats.nodes` exists so `_graphRenderFailed` measures a render against
    # what mermaid will DRAW rather than how many cards were selected. On a
    # board with a colliding pair those numbers differ — and until this test
    # they differed only in reasoning, never in a run: scitex-cards measured
    # the live board on 2026-08-15 and found 0 colliding groups of 4,517, so
    # the branch had never been taken by anything.
    #
    # The realistic collision is a DOT, not a colon: all 14 ids on the real
    # board containing a character `_esc` rewrites are version numbers
    # (`clew-spec-v0.2`, `cct-release-0.5.6`). A generator emitting both
    # `x-v0.2` and `x-v0_2` is the plausible way this arrives.
    # Arrange
    visible = [_card("x-v0.2"), _card("x-v0_2"), _card("hub")]
    graph = {"edges": [_edge("x-v0.2", "hub"), _edge("x-v0_2", "hub")]}
    built = _src(visible, graph)
    drawn = built["stats"]["nodes"]
    good_svg = _diagram(drawn)
    # Act
    verdict = _render_failed(good_svg, drawn)
    # Assert
    assert verdict is False


def test_a_collision_makes_the_node_count_smaller_than_the_card_count():
    # The other half: if these two ever became equal the test above would pass
    # vacuously, because it would no longer be exercising a collision at all.
    # Arrange
    visible = [_card("x-v0.2"), _card("x-v0_2"), _card("hub")]
    graph = {"edges": [_edge("x-v0.2", "hub"), _edge("x-v0_2", "hub")]}
    # Act
    built = _src(visible, graph)
    # Assert
    assert built["stats"]["nodes"] == built["stats"]["connected"] - 1


def test_measuring_that_render_against_the_card_count_would_have_failed_it():
    # WHY `stats.nodes` RATHER THAN `stats.connected` — stated as an
    # executable fact rather than a comment. Feed the same good render the
    # CARD count and the guard condemns it, blanking a perfectly good diagram.
    # Arrange
    visible = [_card("x-v0.2"), _card("x-v0_2"), _card("hub")]
    graph = {"edges": [_edge("x-v0.2", "hub"), _edge("x-v0_2", "hub")]}
    built = _src(visible, graph)
    good_svg = _diagram(built["stats"]["nodes"])
    # Act
    verdict = _render_failed(good_svg, built["stats"]["connected"])
    # Assert
    assert verdict is True


def test_the_card_count_and_the_node_count_are_reported_separately():
    # Arrange
    visible = [_card("a:b"), _card("a/b"), _card("d")]
    graph = {"edges": [_edge("a:b", "d"), _edge("a/b", "d")]}
    # Act
    built = _src(visible, graph)
    # Assert
    assert built["stats"]["connected"] == 3


# === _graphFailureHtml / _graphOversizeHtml — the honest panels ============


def _failure(reason: object) -> str:
    return _call(f"G._graphFailureHtml({json.dumps(reason)})")


def test_a_render_failure_says_mermaid_returned_the_wrong_thing():
    # The operator must be able to tell "the drawing failed" from "the data
    # is broken" — they lead to completely different next steps.
    # Arrange
    # Act
    out = _failure("render")
    # Assert
    assert "something other than the diagram" in out


def test_a_render_failure_absolves_the_card_data():
    # Arrange
    # Act
    out = _failure("render")
    # Assert
    assert "not a data problem" in out


def test_a_bundle_failure_keeps_naming_the_offline_case():
    # This wording predates the fix and stays: a CDN that cannot be reached
    # is a real, separate, actionable cause.
    # Arrange
    # Act
    out = _failure("bundle")
    # Assert
    assert "failed to load" in out


def test_an_unknown_reason_falls_back_to_the_load_failure_wording():
    # A null reason means the render never reported one, which is the load
    # path. Guessing "render" there would state a cause we did not observe.
    # Arrange
    # Act
    out = _failure(None)
    # Assert
    assert "failed to load" in out


def _oversize(stats: dict, length: int, limit: object = None) -> str:
    return _call(
        f"G._graphOversizeHtml({json.dumps(stats)}, {json.dumps(length)},"
        f" {json.dumps(limit)})"
    )


@pytest.mark.parametrize("fragment", ["155,977", "50,000", "407 connected cards"])
def test_the_oversize_panel_reports_the_measurement(fragment):
    # Reporting the numbers is what stops the next person from having to
    # rediscover them the way this bug had to be.
    # Arrange
    stats = {"connected": 0, "dep": 0, "parent": 0, "hidden": 0,
             "total": 407, "omitted": 407}
    # Act
    out = _oversize(stats, 155977, 50000)
    # Assert
    assert fragment in out


def test_the_oversize_panel_tells_the_operator_what_to_do():
    # Arrange
    stats = {"connected": 0, "dep": 0, "parent": 0, "hidden": 0,
             "total": 407, "omitted": 407}
    # Act
    out = _oversize(stats, 155977, 50000)
    # Assert
    assert "Narrow the filters" in out


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


_CAPPED = {"connected": 120, "dep": 60, "parent": 59, "hidden": 4000,
           "total": 407, "omitted": 287}


def test_a_capped_diagram_says_how_many_of_the_total_it_drew():
    # Without this the panel reports "120 connected nodes" on a board with
    # 407 of them — the counts line would be the thing that lies.
    # Arrange
    # Act
    out = _hint(_CAPPED)
    # Assert
    assert "<strong>120</strong> of <strong>407</strong> connected nodes" in out


def test_a_capped_diagram_names_the_number_it_left_out():
    # Arrange
    # Act
    out = _hint(_CAPPED)
    # Assert
    assert "287 more connected cards are omitted" in out


def test_a_capped_diagram_says_what_to_do_about_it():
    # Arrange
    # Act
    out = _hint(_CAPPED)
    # Assert
    assert "narrow the filters" in out


def test_one_omitted_card_reads_as_singular():
    # Arrange
    stats = dict(_CAPPED, connected=406, omitted=1)
    # Act
    out = _hint(stats)
    # Assert
    assert "1 more connected card is omitted" in out


def test_an_uncapped_diagram_says_nothing_about_omitting_anything():
    # The cap sentence must not appear on a normal board — a permanent
    # "0 omitted" note would train the operator to ignore the line.
    # Arrange
    # Act
    out = _hint(dict(_CAPPED, connected=407, omitted=0))
    # Assert
    assert "omitted" not in out


def test_the_cap_note_precedes_a_render_note():
    # Arrange
    # Act
    out = _hint(_CAPPED, note="cached")
    # Assert
    assert out.index("omitted") < out.index("cached")


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


@pytest.mark.parametrize(
    "call",
    [
        "STX.graphBuilders._graphRenderFailed(svg, expectedNodes)",
        "STX.graphBuilders._exceedsMermaidLimit(built.src)",
        "STX.graphBuilders._graphFailureHtml(",
        "STX.graphBuilders._graphOversizeHtml(",
        "_renderGraphSvg(built.src, built.stats.nodes)",
    ],
)
def test_the_render_path_consults_the_new_guards(call):
    # A predicate nothing calls is decoration. These are the whole fix:
    # without the first, mermaid's one-node complaint is cached and shown as
    # if it were the diagram; without the second, an oversized board is handed
    # over to be refused; the last is the expected node count the first one
    # measures against — omit it and the check silently compares against
    # undefined and passes everything.
    # Arrange
    template = TEMPLATE.read_text(encoding="utf-8")
    # Act
    # Assert
    assert call in template


def test_the_template_pins_the_ceiling_it_guards_against():
    # The guard and the renderer must agree on the number. Letting mermaid
    # default it means a library change silently puts them on different
    # ceilings and the error image comes back.
    # Arrange
    template = TEMPLATE.read_text(encoding="utf-8")
    # Act
    # Assert
    assert "maxTextSize: STX.graphBuilders.MERMAID_MAX_TEXT," in template


def test_the_template_no_longer_hardcodes_the_failure_message():
    # It moved into _graphFailureHtml so the two failure causes could be told
    # apart; a leftover copy would be the drift this split exists to remove.
    # Arrange
    template = TEMPLATE.read_text(encoding="utf-8")
    # Act
    # Assert
    assert "the mermaid bundle failed to load (offline?)" not in template


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
