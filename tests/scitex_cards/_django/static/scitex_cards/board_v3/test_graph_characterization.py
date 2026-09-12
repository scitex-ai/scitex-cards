#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The graph builders still produce EXACTLY this, byte for byte.

THE BAR THIS ENFORCES, in scitex-cards' words (2026-08-15): "an extraction
that cannot show byte-identical output on real data has not earned the word
refactor." Every remaining board_v3 cluster is held to it.

WHY A CHARACTERIZATION TEST AND NOT AN OLD-VS-NEW DIFF. The obvious form is to
keep a copy of the pre-extraction inline function and diff the two. That is the
right tool for a ONE-OFF check — it is how 155,977 == 155,977 was shown for
#849 — and the wrong thing to commit: a permanent copy of the logic is a SECOND
IMPLEMENTATION that drifts from the shipped one, and then both "pass" while
disagreeing. Once the inline original is deleted there is no "old" left to diff
against anyway, so the honest invariant is the one written here:

    shipped module + committed payload -> exactly this output

which also catches any FUTURE behaviour change, not merely the one extraction.

THE FIXTURE IS SYNTHESIZED, NOT A SNAPSHOT, and its own ``_README`` carries the
reasoning and the rule that keeps it honest: WHEN A SHAPE-RELATED BUG IS FOUND
IN PRODUCTION, THE FIX ADDS THAT SHAPE TO THE FIXTURE. A synthesized fixture
can only hold shapes someone thought of; real data eventually produces one
nobody did.

TWO PROPERTIES OF THE COMMITTED OUTPUT ARE BUGS, RECORDED DELIBERATELY. A
characterization test pins what the code DOES, which is not always what it
should do, and writing that down is the point rather than an embarrassment:

  1. ``clew-spec-v0.2`` and ``clew-spec-v0_2`` both escape to ``clew-spec-v0_2``,
     so the expected source DECLARES THE SAME NODE TWICE and the edge between
     them renders as a SELF-LOOP. That is card
     ``board-graph-ids-collide-under-escaping-20260815``; when ``_esc`` is made
     injective the expected file changes, and this test is what makes that
     change visible and reviewable line by line rather than trusted.
  2. status ``in_progress-2`` renders class ``st-in_progress`` — the class
     strips everything outside ``[a-z_]``, so two distinct statuses can collapse
     into one colour.

Card ``gui-byte-identical-extraction-proof-as-repo-test-20260815``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_FIXTURES = _HERE / "fixtures"
PAYLOAD = _FIXTURES / "graph_payload.json"
EXPECTED_SRC = _FIXTURES / "graph_expected.mmd"
EXPECTED_STATS = _FIXTURES / "graph_expected_stats.json"

GRAPH_BUILDERS_JS = (
    _HERE.parents[5]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "17-graph-builders.js"
)


def _node() -> str:
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


@pytest.fixture(scope="module")
def produced() -> dict:
    """Run the SHIPPED module over the committed payload; return src + stats."""
    assert GRAPH_BUILDERS_JS.is_file(), f"module missing: {GRAPH_BUILDERS_JS}"
    script = (
        f"const M = require({json.dumps(str(GRAPH_BUILDERS_JS))});\n"
        f"const p = require({json.dumps(str(PAYLOAD))});\n"
        "const out = M._graphSrc(p.visible, p.graph, undefined);\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        [_node(), "-e", script], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"node failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_the_generated_source_is_byte_identical_to_the_committed_one(produced):
    # THE BAR. Not "looks equivalent" — the same bytes.
    # Arrange
    expected = EXPECTED_SRC.read_text(encoding="utf-8")
    # Act
    actual = produced["src"]
    # Assert
    assert actual == expected


def test_the_stats_are_identical_to_the_committed_ones(produced):
    # The hint line is computed from these, so a silent change here changes
    # what the operator is told about what was drawn.
    # Arrange
    expected = json.loads(EXPECTED_STATS.read_text(encoding="utf-8"))
    # Act
    actual = produced["stats"]
    # Assert
    assert actual == expected


def test_the_fixture_still_exercises_an_id_collision(produced):
    # A fixture can stop testing a shape without failing: edit an id and the
    # collision quietly disappears while every assertion above still passes,
    # because the expected file would be regenerated to match. This pins the
    # PROPERTY rather than the bytes, so the shape cannot leave silently.
    # Arrange
    stats = produced["stats"]
    # Act
    collisions = stats["connected"] - stats["nodes"]
    # Assert
    assert collisions == 1, stats


def test_the_fixture_still_exercises_a_disconnected_card(produced):
    # Same reasoning: `hidden` counts cards no edge touches, and a fixture that
    # accidentally connects them all would stop testing the "not drawn" branch.
    # Arrange
    stats = produced["stats"]
    # Act
    hidden = stats["hidden"]
    # Assert
    assert hidden > 0, stats


def test_the_fixture_still_exercises_both_edge_axes(produced):
    # `dep` (depends_on + blocks) and `parent` are separate axes drawn with
    # different arrows; a fixture with only one stops covering the other.
    # Arrange
    stats = produced["stats"]
    # Act
    axes = (stats["dep"] > 0, stats["parent"] > 0)
    # Assert
    assert axes == (True, True), stats
