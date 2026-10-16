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


def test_the_successor_default_seed_status_is_present():
    # Arrange
    source = _SUBJECT.read_text()
    # Act
    has_seed = "status: deferred" in source
    # Assert
    assert has_seed


def test_the_successor_default_seed_store_text_is_present():
    # Arrange
    source = _SUBJECT.read_text()
    # Act
    has_fixture = "_STORE_TEXT" in source
    # Assert — non-vacuity: the file under test is the graph-cache suite.
    assert has_fixture


def test_pending_seed_warns_on_load():
    """A ``pending`` row exercises the tolerated-legacy path, not the live one."""
    # Arrange
    pending = [{"id": "a", "title": "A", "status": "pending"}]
    # Act
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(pending, source="/some/store.db")
    pending_warnings = [str(x.message) for x in caught]
    # Assert
    assert any("abolished" in text for text in pending_warnings)


def test_deferred_seed_is_silent_on_load():
    """A ``deferred`` row passes silently — the live-status path the cache pins."""
    # Arrange
    live = [{"id": "a", "title": "A", "status": "deferred"}]
    # Act
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(live, source="/some/store.db")
    live_warnings = [str(x.message) for x in caught]
    # Assert
    assert live_warnings == []


def test_graph_cache_key_is_the_exact_triple():
    # Arrange — the cache-hit decision must derive from the store
    # identity, never from a task status field.
    source = inspect.getsource(_graph_mod.handle_graph)
    # Act
    has_key = "key = (str(board.store_path), board.mtime, board.sig)" in source
    # Assert
    assert has_key


def test_graph_cache_key_code_reads_no_status():
    # Arrange — the only "status" word allowed is the docstring's
    # "status colors" (the payload product, not a read).
    source = inspect.getsource(_graph_mod.handle_graph)
    # Act
    code_lines = [
        line
        for line in source.splitlines()
        if "status colors" not in line
        and '"""' not in line
        # 304 is the HTTP conditional-response status, not a task-status
        # read: the hit decision still derives from the key alone.
        and "status=304" not in line
    ]
    code = "\n".join(code_lines)
    # Assert
    assert "status" not in code


def test_pending_is_in_abolished_statuses():
    # Arrange
    abolished = ABOLISHED_STATUSES
    # Act
    present = "pending" in abolished
    # Assert — a future re-introduction of `pending` fails loudly here.
    assert present


def test_pending_is_absent_from_valid_statuses():
    # Arrange
    valid = VALID_STATUSES
    # Act
    absent = "pending" not in valid
    # Assert
    assert absent


def test_deferred_is_in_valid_statuses():
    # Arrange
    valid = VALID_STATUSES
    # Act
    present = "deferred" in valid
    # Assert
    assert present
