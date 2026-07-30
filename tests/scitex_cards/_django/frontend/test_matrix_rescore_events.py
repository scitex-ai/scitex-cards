#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The Matrix view must draw the same history from either payload shape.

``board_v3/14-matrix.js`` reconstructs quadrant occupancy over time by
replaying rescore events. It used to get them by scanning every card's
``comments[]``; it now prefers ``rescore_history``, the field ``/graph``
derives for exactly this purpose, so that ``comments[]`` can leave the
payload (8.5 MB of 19.8 MB, refetched on nearly every 5 s poll).

The migration is only correct if BOTH shapes produce an identical
series, so that is what these tests assert — against the real JS file,
required through ``node``, not a hand-ported mirror. The sibling
``test_chat_helpers.py`` has to mirror its subject because ``.tsx``
cannot be required; ``14-matrix.js`` exports through ``module.exports``,
so there is no mirror here to drift.

``test_positive_control_...`` is the one that makes the others mean
something: an equivalence test between two code paths passes trivially
if both paths silently return nothing. It proves that stripping the
events DOES change the answer, so "identical" is a real result rather
than two empty charts agreeing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MATRIX_JS = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "14-matrix.js"
)


def _node() -> str:
    """Locate ``node``; skip cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _rescore(ts: str, urgency: list, importance: list) -> dict:
    """A rescore comment shaped as ``rescore_task`` writes it."""
    return {
        "ts": ts,
        "author": "operator",
        "text": "drag",
        "kind": "rescore",
        "rescore": {"urgency": urgency, "importance": importance},
    }


_EVENTS_A = [
    _rescore("2026-07-01T00:00:00Z", [1, 5], [1, 5]),
    _rescore("2026-07-02T00:00:00Z", [5, 2], [5, 2]),
]
_EVENTS_B = [_rescore("2026-07-01T00:00:00Z", [4, 1], [4, 1])]

#: The same board as an OLD server sends it: events only inside comments[],
#: mixed with ordinary prose that must contribute nothing.
OLD_SHAPE = [
    {
        "id": "a",
        "urgency": 2,
        "importance": 2,
        "comments": _EVENTS_A + [{"ts": "x", "author": "n", "text": "prose"}],
    },
    {"id": "b", "urgency": 1, "importance": 1, "comments": _EVENTS_B},
    {"id": "c", "urgency": 3, "importance": 3, "comments": [{"ts": "y", "text": "hi"}]},
    {"id": "d", "comments": []},
]

#: The same board as a NEW server sends it: the derived field, no comments[].
NEW_SHAPE = [
    {
        "id": "a",
        "urgency": 2,
        "importance": 2,
        "rescore_history": [
            {"ts": c["ts"], "rescore": c["rescore"]} for c in _EVENTS_A
        ],
    },
    {
        "id": "b",
        "urgency": 1,
        "importance": 1,
        "rescore_history": [
            {"ts": c["ts"], "rescore": c["rescore"]} for c in _EVENTS_B
        ],
    },
    {"id": "c", "urgency": 3, "importance": 3, "rescore_history": []},
    {"id": "d", "rescore_history": []},
]

#: Neither shape — the axes survive but every event is gone.
NO_EVENTS = [
    {k: v for k, v in n.items() if k not in ("comments", "rescore_history")}
    for n in NEW_SHAPE
]


def _series(nodes: list) -> list:
    """``occupancyHistory(nodes)`` from the real JS module, via node."""
    harness = (
        f"const api = require({str(MATRIX_JS)!r});"
        'const nodes = JSON.parse(require("fs").readFileSync(0, "utf8"));'
        "process.stdout.write(JSON.stringify(api.occupancyHistory(nodes)));"
    )
    # Board JSON goes over stdin, not argv: a real board is megabytes and
    # would blow ARG_MAX, and the failure would look like a node crash.
    out = subprocess.run(
        [_node(), "-e", harness],
        input=json.dumps(nodes),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(out.stdout)


def test_both_payload_shapes_give_the_same_series():
    """The whole point of the migration: no visible change to the chart."""
    # Arrange
    expected = _series(OLD_SHAPE)

    # Act
    got = _series(NEW_SHAPE)

    # Assert
    assert got == expected


def test_events_are_actually_replayed():
    """A length-1 series is the silent-flatten failure mode; reject it."""
    # Arrange
    want = len(_EVENTS_A) + len(_EVENTS_B) + 1  # baseline "now" + one per event

    # Act
    got = _series(NEW_SHAPE)

    # Assert
    assert len(got) == want


def test_positive_control_stripping_events_changes_the_series():
    """Without this, two silently-empty paths would agree and look correct."""
    # Arrange
    with_events = _series(NEW_SHAPE)

    # Act
    without_events = _series(NO_EVENTS)

    # Assert
    assert without_events != with_events


def test_derived_field_is_preferred_over_the_comment_scan():
    """A card carrying BOTH must read the field, not re-derive from prose.

    Pinned because the fallback is deliberately kept: if the branch order
    ever flips, every test above still passes (the two paths agree) while
    the payload saving silently evaporates.
    """
    # Arrange
    conflicting = [
        {
            "id": "a",
            "urgency": 2,
            "importance": 2,
            "comments": _EVENTS_A,
            "rescore_history": [],
        }
    ]

    # Act
    got = _series(conflicting)

    # Assert
    assert len(got) == 1


# EOF
