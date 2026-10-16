#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the priority-handler legacy-status fixture (pending abolition).

``tests/scitex_cards/_django/handlers/test_priority.py`` seeded
``status: pending`` in the ``two_card_store_after_reorder`` fixture after
``pending`` was abolished 2026-07-10. :func:`handle_priority` assigns
``task["priority"] = rank`` and never reads the status, so the seed passed
while teaching the abolished value as live (model/reassign_all/throughput/
deadline/is_overdue precedent). The seed is now ``deferred`` (the successor
default); these pins keep it there.

These pins use the file text + the pure validation gate + the source text
only: no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import inspect
import warnings
from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_cards._django.handlers import priority as _priority_mod  # noqa: E402
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES  # noqa: E402
from scitex_cards._validate import _validate_tasks  # noqa: E402

_SUBJECT = Path(__file__).with_name("test_priority.py")


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
    # Act + Assert — non-vacuity: the two-card fixture still seeds a
    # status, and the file under test is the priority suite.
    assert '"status": "deferred"' in source
    assert "two_card_store_after_reorder" in source


def test_pending_seed_warns_while_deferred_is_silent():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    priority assertions claim to pin. This is why the fixture must stay
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


def test_priority_handler_is_status_blind():
    # Arrange — the reorder loop must not gate on the status enum.
    source = inspect.getsource(_priority_mod.handle_priority)
    # Act + Assert — priority fields only; no status read in the write path.
    assert 'task["priority"] = rank' in source
    assert '"status"' not in source
    assert "'status'" not in source


def test_status_enum_mirror_holds():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
