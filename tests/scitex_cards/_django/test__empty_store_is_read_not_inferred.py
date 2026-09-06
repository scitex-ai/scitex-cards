#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AN EMPTY BOARD MUST BE A READING, NEVER AN INFERENCE.

``empty_store`` is the flag that tells the frontend to render the normal,
clean, zero-card board INSTEAD of the red load-error banner. That makes it the
most dangerous boolean in the payload: whatever sets it decides whether a
0-card board looks like a fact or like a fault.

It used to be set by INFERENCE — "the resolved store-identity FILE does not
exist, so this must be a brand-new workspace with no cards yet". Post-cutover
that file is the ``tasks.yaml`` SIDECAR, which nothing creates, so on the
operator's live board the inference was permanently true: 0 cards rendered as a
clean empty board while 2,654 sat in the canonical database, for over a day,
with no error anywhere (2026-07-29). The inference was not merely wrong; it was
UNFALSIFIABLE from the outside, because its output is exactly what a real empty
board looks like.

So the flag is now DERIVED FROM THE READ: True iff the store was read and held
no cards. The fresh-workspace case it was invented for is unharmed — an
initialised database with no rows still reports it — but a store that CANNOT be
read no longer borrows the empty board's clothes. It raises, and the endpoint
answers 500 (pinned in ``test__board_reads_the_database.py``).

This file pins the honest-empty side of that contract across the three read
payloads that carry the flag. RequestFactory against the real views, real
stores, no mocks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers.graph import _graph_cache_reset  # noqa: E402
from scitex_cards._django.handlers.timeline import timeline_view  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402

_SEEDED = {
    "tasks": [
        {"id": "seeded", "title": "Seeded Card", "status": "in_progress", "priority": 1}
    ]
}


@pytest.fixture
def real_but_empty_store():
    """The per-test scratch database: initialised, schema-complete, no cards.

    THE LEGITIMATE ZERO. This is what a fresh workspace looks like once its
    store exists — the state the ``empty_store`` flag was invented for, and the
    only state that may now produce it.
    """
    _reset_cache()
    _graph_cache_reset()
    yield Path(os.environ["SCITEX_CARDS_DB"])
    _reset_cache()
    _graph_cache_reset()


@pytest.fixture
def seeded_store():
    """The same database with one card in it."""
    from conftest import seed_db_from_doc

    _reset_cache()
    _graph_cache_reset()
    seed_db_from_doc(_SEEDED, os.environ["SCITEX_CARDS_DB"])
    yield Path(os.environ["SCITEX_CARDS_DB"])
    _reset_cache()
    _graph_cache_reset()


def _get(endpoint: str):
    request = RequestFactory().get(f"/{endpoint}")
    return views.api_dispatch(request, endpoint)


# --- graph -------------------------------------------------------------------


def test_graph_on_an_empty_store_returns_200(real_but_empty_store):
    """An empty board is a state, not an error — no banner for a real zero."""
    # Arrange
    _ = real_but_empty_store
    # Act
    response = _get("graph")
    # Assert
    assert response.status_code == 200


def test_graph_on_an_empty_store_has_no_nodes(real_but_empty_store):
    """The honest payload for a store with no cards is zero nodes."""
    # Arrange
    _ = real_but_empty_store
    # Act
    payload = json.loads(_get("graph").content)
    # Assert
    assert payload["nodes"] == []


def test_graph_on_an_empty_store_sets_empty_store_flag(real_but_empty_store):
    """The FE distinguishes "no cards yet" from "0 matching cards" by this flag."""
    # Arrange
    _ = real_but_empty_store
    # Act
    payload = json.loads(_get("graph").content)
    # Assert
    assert payload["empty_store"] is True


# --- tasks -------------------------------------------------------------------


def test_tasks_on_an_empty_store_returns_200(real_but_empty_store):
    """/tasks shares the read path, so it shares the honest empty state."""
    # Arrange
    _ = real_but_empty_store
    # Act
    response = _get("tasks")
    # Assert
    assert response.status_code == 200


def test_tasks_on_an_empty_store_is_an_empty_list(real_but_empty_store):
    """The raw task list of a store with no cards is []."""
    # Arrange
    _ = real_but_empty_store
    # Act
    payload = json.loads(_get("tasks").content)
    # Assert
    assert payload["tasks"] == []


def test_tasks_on_an_empty_store_sets_empty_store_flag(real_but_empty_store):
    """/tasks carries the same flag the /graph payload does."""
    # Arrange
    _ = real_but_empty_store
    # Act
    payload = json.loads(_get("tasks").content)
    # Assert
    assert payload["empty_store"] is True


# --- timeline ----------------------------------------------------------------


def test_timeline_on_an_empty_store_returns_200(real_but_empty_store):
    """/timeline reads the default store through the same ``get_board``."""
    # Arrange
    request = RequestFactory().get("/timeline")
    # Act
    response = timeline_view(request)
    # Assert
    assert response.status_code == 200


def test_timeline_on_an_empty_store_has_no_events(real_but_empty_store):
    """The honest timeline of a store with no cards is zero events."""
    # Arrange
    request = RequestFactory().get("/timeline")
    # Act
    payload = json.loads(timeline_view(request).content)
    # Assert
    assert payload["events"] == []


def test_timeline_on_an_empty_store_sets_empty_store_flag(real_but_empty_store):
    """/timeline carries the same flag the /graph payload does."""
    # Arrange
    request = RequestFactory().get("/timeline")
    # Act
    payload = json.loads(timeline_view(request).content)
    # Assert
    assert payload["empty_store"] is True


# --- honesty in the other direction ------------------------------------------


def test_a_store_with_cards_is_never_reported_empty(seeded_store):
    """A populated store must never masquerade as the fresh-workspace state.

    This is the assertion the old inference could not make honestly: it read a
    file that had nothing to do with the cards, so it answered the same way for
    a store holding 2,654 cards as for one holding none.
    """
    # Arrange
    _ = seeded_store
    # Act
    payload = json.loads(_get("graph").content)
    # Assert
    assert payload["empty_store"] is False


# EOF
