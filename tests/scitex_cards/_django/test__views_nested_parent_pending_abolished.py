#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the nested-graph parent fixtures (pending abolition).

``tests/scitex_cards/_django/test_views.py`` seeded ``status: pending``
in 2 fixtures of ``_NESTED_STORE_TEXT`` (``child-a`` + ``solo``) after
``pending`` was abolished 2026-07-10: the assertions pin the ``parent``
field per node only (``test_graph_endpoint_exposes_parent_field_per_node``
+ the every-node key/defaults tests), never the status — so the suite
passed while teaching the abolished value as live (store 4 +
store-verbs + store-card-events + doc-preservation + users 3 +
graph-roles + crash_safe_writer + model 31 precedent). The seeds are
now ``deferred`` (the successor default); these pins keep them there.

No src change: the parent emission path is status-blind —
``handlers/graph.py`` emits ``"parent": t.get("parent")`` verbatim per
node with no status gate — so repointing the fixtures changes which
read path is exercised (live, not tolerated-legacy) without changing
what is proven.

These pins use the file text + the pure validation gate only: no
store, no PostgreSQL — they run in containers where the store-backed
suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import _validate_tasks

_SUBJECT = Path(__file__).with_name("test_views.py")


def test_no_fixture_seeds_the_abolished_status():
    # Arrange
    source = _SUBJECT.read_text()
    # Act
    seeds = [
        line
        for line in source.splitlines()
        if "status" in line and "pending" in line and "abolished" not in line
    ]
    # Assert — every remaining "pending" is the abolition-note comment.
    assert seeds == []


def test_the_successor_default_seed_is_present():
    # Arrange
    source = _SUBJECT.read_text()
    # Act + Assert — non-vacuity: the nested fixture still seeds statuses,
    # and the file under test is the views suite, not an empty stub.
    assert "status: deferred" in source
    assert "_NESTED_STORE_TEXT" in source
    assert "def test_graph_endpoint_exposes_parent_field_per_node" in source


def test_pending_seed_warns_while_deferred_is_silent():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    parent-field tests claim to pin. This is why the fixtures must stay
    ``deferred``.
    """
    # Arrange
    pending = [{"id": "a", "title": "A", "status": "pending"}]
    live = [{"id": "a", "title": "A", "status": "deferred"}]
    # Act
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(pending, source="/some/store.db")
    pending_warnings = [str(x.message) for x in caught]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(live, source="/some/store.db")
    live_warnings = [str(x.message) for x in caught]
    # Assert
    assert any("abolished" in text for text in pending_warnings)
    assert live_warnings == []


def test_parent_assertions_never_gate_on_status():
    # Arrange — the parent-field tests must pin parent linkage only, so a
    # future status-gated assertion fails loudly here instead of silently
    # re-coupling the parent property to the status enum.
    source = _SUBJECT.read_text()
    block = source.split("def test_graph_endpoint_exposes_parent_field_per_node")[1].split(
        "def test_graph_endpoint_includes_parent_key_on_every_node"
    )[0]
    # Act + Assert
    assert '["parent"]' in block
    assert "== expected_parent" in block
    assert '"status"' not in block
    assert "'status'" not in block


def test_nested_fixture_still_pins_four_nodes():
    # Arrange — vacuity guard: the repoint must not have dropped a node.
    source = _SUBJECT.read_text()
    block = source.split("_NESTED_STORE_TEXT = (")[1].split("\n)\n")[0]
    # Act + Assert
    for node in ("hub", "child-a", "child-b", "solo"):
        assert node in block


def test_status_enum_mirror_holds():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
