#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the graph-cache legacy-status fixture (pending abolition).

``tests/scitex_cards/_django/handlers/test_graph_cache.py`` seeded
``status: pending`` in the ``_STORE_TEXT`` YAML fixture (plus a second task
appended in the mtime-invalidation test) after ``pending`` was abolished
2026-07-10. :func:`handle_graph` keys its payload cache on
``(store_path, board.mtime, board.sig)`` and every assertion pins cache
behaviour (hit count, byte-equal payload, mtime-keyed invalidation,
reset hook) — never the seeded status — so the suite passed while
teaching the abolished value as live (nudge/priority/timeline precedent).
The seeds are now ``deferred`` (an open, valid status; the cached payload
shape is status-agnostic); these pins keep them there.

These pins use the file text + the pure validation gate + the handler
source text only: no store, no PostgreSQL — they run in containers where
the store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import inspect
import warnings
from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_cards._django.handlers import graph as _graph_mod  # noqa: E402
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES  # noqa: E402
from scitex_cards._validate import _validate_tasks  # noqa: E402

_SUBJECT = Path(__file__).with_name("test_graph_cache.py")


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
    # Act + Assert — non-vacuity: the YAML store fixture still seeds a
    # status, and the file under test is the graph-cache suite.
    assert "status: deferred" in source
    assert "_STORE_TEXT" in source


def test_pending_seed_warns_while_deferred_is_silent():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    cache assertions claim to pin. This is why the fixture must stay
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


def test_graph_cache_key_is_status_blind():
    # Arrange — the cache-hit decision must derive from the store
    # identity, never from a task status field.
    source = inspect.getsource(_graph_mod.handle_graph)
    # Act + Assert — key is exactly (store_path, mtime, sig); the only
    # "status" word in the function is the docstring's "status colors"
    # (the payload product, not a read).
    assert "key = (str(board.store_path), board.mtime, board.sig)" in source
    code_lines = [
        line
        for line in source.splitlines()
        if "status colors" not in line and '"""' not in line
    ]
    code = "\n".join(code_lines)
    assert "status" not in code


def test_status_enum_mirror_holds():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
