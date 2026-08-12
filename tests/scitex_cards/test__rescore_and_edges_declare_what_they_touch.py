#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``rescore_task`` and the edge verbs must persist every row they change.

The companion to the lifecycle-verb conversion. Those verbs each declared
``touched_ids=[task_id]``; TWO OF THE THREE VERBS HERE WOULD BE WRONG WITH
THAT SAME OBVIOUS VALUE, and only an assertion distinguishes them:

  ``rescore_task``      recompute_ranks reassigns 1..N across EVERY scored
                        card and strips the key from terminal ones — the
                        module docstring says so outright ("Every other
                        card's rank int changes SILENTLY"). Declaring only
                        the rescored card would persist its new axes while
                        DISCARDING every neighbour's shifted rank, leaving
                        duplicate and missing positions in a total order.

  ``set_edge``          writes the edge list to `source` and, on `add`, also
                        appends to the OTHER card's `subscribers`. Declaring
                        only `source` would drop the subscription that makes
                        the waiter hear about its gate.

  ``set_collaborator``  genuinely single-card, and asserted as such so the
  / ``set_subscriber``  narrow declaration is a checked fact rather than an
                        assumption inherited from its neighbours.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_cards._model import load_tasks, save_tasks
from scitex_cards._store_relations import set_collaborator, set_edge
from scitex_cards._store_rescore import rescore_task


def _write_board(path: Path, tasks: list[dict]) -> None:
    save_tasks(tasks, path)


def _by_id(tasks: list[dict]) -> dict:
    return {t["id"]: t for t in tasks}


@pytest.fixture
def board(tmp_path: Path) -> Path:
    path = tmp_path / "cards.yaml"
    # `a` owns an agent because the edge tests need a waiter to subscribe;
    # giving it here rather than re-writing the board inside those tests,
    # since a narrower rewrite trips the shrink guard — correctly, and it
    # caught this file's first draft doing exactly that.
    _write_board(
        path,
        [
            {"id": "a", "title": "A", "status": "deferred", "agent": "agent-a"},
            {"id": "b", "title": "B", "status": "deferred"},
            {"id": "c", "title": "C", "status": "deferred"},
        ],
    )
    return path


def test_rescore_persists_the_neighbour_ranks_it_shifted(board):
    # Arrange
    rescore_task(board, task_id="a", urgency=1, importance=1)
    rescore_task(board, task_id="b", urgency=5, importance=5)
    # Act
    rescore_task(board, task_id="c", urgency=4, importance=4)
    # Assert
    assert [t["rank"] for t in sorted(load_tasks(board), key=lambda t: t["id"])] == [
        3,
        1,
        2,
    ]


def test_rescore_leaves_no_duplicate_rank_positions(board):
    # Arrange
    for tid, u in (("a", 1), ("b", 5), ("c", 3)):
        rescore_task(board, task_id=tid, urgency=u, importance=u)
    # Act
    ranks = [t.get("rank") for t in load_tasks(board) if t.get("rank") is not None]
    # Assert
    assert len(ranks) == len(set(ranks))


def test_adding_an_edge_persists_the_subscription_on_the_other_card(board):
    # Arrange
    waiter_owner = "agent-a"
    # Act
    set_edge(board, action="add", kind="depends_on", source="a", target="b")
    # Assert
    assert waiter_owner in (_by_id(load_tasks(board))["b"].get("subscribers") or [])


def test_adding_an_edge_persists_the_edge_on_the_source(board):
    # Arrange
    expected = ["b"]
    # Act
    set_edge(board, action="add", kind="depends_on", source="a", target="b")
    # Assert
    assert _by_id(load_tasks(board))["a"].get("depends_on") == expected


def test_setting_a_collaborator_writes_only_that_card(board):
    # Arrange
    before = _by_id(load_tasks(board))["c"]
    # Act
    set_collaborator(board, task_id="a", who="someone", action="add")
    # Assert
    assert _by_id(load_tasks(board))["c"] == before


# EOF
