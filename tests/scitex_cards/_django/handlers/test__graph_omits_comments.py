#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/graph`` must NOT ship the full comment thread.

Step 3 of the payload reduction deleted ``comments[]`` from the node dicts.
Measured on the live store at 2,881 cards: 9,015,360 B removed, against
707,195 B of summary scalars and 15,343 B of ``rescore_history`` kept —
a net 7.9 MiB off a 19.8 MiB response that the board refetched on nearly
every 5 s poll.

Nothing pinned its ABSENCE, only its presence-by-habit, so re-adding it
would have been silent and would have looked like a fix (every consumer
still has a comments[] fallback, so the board would keep working while the
payload quietly tripled). This file is the guard: the replacements must be
present and the thread must not be.

The full thread is still served by ``GET /chat/<card_id>`` — see
``test__chat_view.py``, which asserts that endpoint DOES carry it. The two
files together state the split: summaries on the list surface, content on
the per-card surface.
"""

from __future__ import annotations

import pytest

from scitex_cards._django.handlers.graph import _build_graph


class _Board:
    """Board stand-in carrying EVERY attribute ``_build_graph`` reads.

    Enumerated from the source (``rg -o 'board\\.[a-z_]+'``) rather than
    discovered by running until it stopped raising — a stub grown that way
    ends up matching the code paths the test happens to hit rather than the
    interface, and then silently under-tests when the code changes.
    """

    def __init__(self, tasks):
        self.tasks = tasks
        self.store_path = "/nonexistent/cards.db"
        self.mtime = 0.0
        self.sig = "sig"
        self.empty_store = False
        self.groups = []


@pytest.fixture()
def node():
    """One node built from a card carrying a real comment thread."""
    tasks = [
        {
            "id": "c-1",
            "title": "a card",
            "status": "in_progress",
            "comments": [
                {"ts": "2026-07-01T00:00:00Z", "author": "operator", "text": "hi"},
                {
                    "ts": "2026-07-02T00:00:00Z",
                    "author": "operator",
                    "text": "drag",
                    "kind": "rescore",
                    "rescore": {"urgency": [1, 4], "importance": [2, 5]},
                },
            ],
        }
    ]
    graph = _build_graph(_Board(tasks))
    return graph["nodes"][0]


def test_graph_node_omits_the_comment_thread(node):
    """The 7.9 MiB. Re-adding this key must fail here, not in production."""
    # Arrange
    # Act — the fixture built the node.
    # Assert
    assert "comments" not in node


def test_graph_node_still_reports_the_comment_count(node):
    """The list surfaces need the count; only the prose was dropped."""
    # Arrange
    # Act — the fixture built the node.
    # Assert
    assert node["comment_count"] == 2


def test_graph_node_still_carries_the_last_comment_preview(node):
    """The timeline footer renders from this, so it must survive the drop."""
    # Arrange
    # Act — the fixture built the node.
    # Assert
    assert node["last_comment"]["text_preview"] == "drag"


def test_graph_node_still_carries_rescore_history(node):
    """The Matrix needs event CONTENT; this is why the scalars alone were
    not enough to drop the thread."""
    # Arrange
    # Act — the fixture built the node.
    # Assert
    assert node["rescore_history"][0]["rescore"]["urgency"] == [1, 4]


def test_no_comment_prose_survives_anywhere_in_the_node(node):
    """The point of the exercise, stated as a property rather than a key.

    A future change could re-introduce the thread under a different name and
    every test above would still pass. This asserts on the VALUES: the body
    of a non-rescore comment must appear nowhere in the serialized node.
    """
    # Arrange
    import json

    # Act
    blob = json.dumps(node, default=str)

    # Assert
    assert '"hi"' not in blob


# EOF
