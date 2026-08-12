#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board's own write path must persist every row it changed, and no more.

The board handlers were the last whole-document read-modify-writes left after
the verbs were converted (#822, #823, #825). A human clicking Resolve went
through this path, so the clobber survived there after the Python API was safe.

WHAT THIS FILE ADDS OVER THE EXISTING HANDLER TESTS. ``test_priority.py``
already proves a reorder persists sequential priorities, which would catch a
declaration of one id where many were written. It does NOT cover the case that
decided the value used here: ``order`` and ``updated`` DIVERGE when the payload
names a card the board does not have. The handler skips it (a frontend racing
an external edit), so ``updated`` is the shorter, accurate list, and the write
must still persist every card that WAS reordered.

``reopen`` and ``resolve`` are genuinely single-card — one status flip plus one
appended comment — unlike ``priority`` beside them, and unlike ``delete_task``
and ``rescore_task``, where the same obvious declaration would have been wrong.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("django")

from conftest import seed_db_from_doc  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers import graph as _graph_mod  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402
from scitex_cards._model import load_tasks  # noqa: E402

_STORE_DOC = {
    "tasks": [
        {"id": "alpha", "title": "Alpha", "status": "deferred", "priority": 5},
        {"id": "beta", "title": "Beta", "status": "deferred", "priority": 6},
        {"id": "gamma", "title": "Gamma", "status": "deferred", "priority": 7},
    ]
}


@pytest.fixture
def store():
    """Seed, then WARM THE MIRROR HASHES so the write under test is incremental.

    THIS WARM-UP IS THE POINT OF THE FIXTURE, not boilerplate. `seed_db_from_doc`
    reaches `_rebuild_from_doc` and never populates `mirror_hashes`, so the
    FIRST write after a seed finds no prior hashes and takes
    `mirror_doc_incremental`'s full-rebuild branch — which writes every card and
    IGNORES `touched_ids` entirely.

    Measured, not reasoned: without this warm-up, sabotaging the priority
    handler to declare only ONE of three reordered cards left all 22 tests in
    this directory green. A single write after a seed cannot detect a
    `touched_ids` defect, in this file or any other.
    """
    seed_db_from_doc(_STORE_DOC, os.environ["SCITEX_CARDS_DB"])
    store_path = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    Path(store_path).write_text("", encoding="utf-8")
    _reset_cache()
    _graph_mod._graph_cache_reset()
    # The warm-up write itself: a no-op reorder in the cards' existing order.
    _post(store_path, "priority", {"order": ["alpha", "beta", "gamma"]})
    _reset_cache()
    yield store_path
    _reset_cache()
    _graph_mod._graph_cache_reset()


def _post(store_path, endpoint, body):
    request = RequestFactory().post(
        f"/{endpoint}?store={store_path}",
        data=json.dumps(body),
        content_type="application/json",
    )
    return views.api_dispatch(request, endpoint)


def _priorities(store_path):
    return {t["id"]: t.get("priority") for t in load_tasks(store_path)}


@pytest.fixture
def reorder_with_an_unknown_id(store):
    """A drag whose payload names a card the board does not have.

    The handler skips the unknown id, so `order` (4 entries) and `updated`
    (3 entries) diverge here — which is exactly why the write declares the
    latter.
    """
    _post(store, "priority", {"order": ["gamma", "ghost", "beta", "alpha"]})
    return _priorities(store)


def test_a_reorder_persists_every_card_it_renumbered(reorder_with_an_unknown_id):
    # Arrange
    priorities = reorder_with_an_unknown_id
    # Act
    reordered = {k: v for k, v in priorities.items() if k in ("alpha", "beta", "gamma")}
    # Assert — 1,3,4 and NOT 1,2,3: the rank comes from `enumerate(order)`, so
    # a skipped unknown id still consumes its position and leaves a GAP. That
    # is pre-existing behaviour and harmless (priority is an ordering, and the
    # relative order is exactly what was dragged) — asserted as it actually is
    # rather than as the compaction I first assumed and got wrong.
    assert reordered == {"gamma": 1, "beta": 3, "alpha": 4}


def test_a_reorder_does_not_invent_a_row_for_the_unknown_id(
    reorder_with_an_unknown_id,
):
    # Arrange
    priorities = reorder_with_an_unknown_id
    # Act
    present = "ghost" in priorities
    # Assert
    assert not present


def test_resolving_a_card_persists_that_cards_new_status(store):
    # Arrange
    expected = "done"
    # Act
    _post(store, "resolve", {"id": "beta"})
    # Assert
    assert {t["id"]: t["status"] for t in load_tasks(store)}["beta"] == expected


def test_resolving_a_card_leaves_the_other_cards_alone(store):
    # Arrange
    before = {t["id"]: dict(t) for t in load_tasks(store)}
    # Act
    _post(store, "resolve", {"id": "beta"})
    # Assert
    after = {t["id"]: dict(t) for t in load_tasks(store)}
    assert after["gamma"] == before["gamma"]


# EOF
