#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the migration-scanner legacy-status fixtures (pending abolition).

``tests/scitex_cards/_migration/test__migrate.py`` seeded
``status: pending`` in the ``classify_row`` / ``note_excerpt`` fixtures
after ``pending`` was abolished 2026-07-10. :func:`classify_row` reads
only ``id`` / ``note`` / ``title`` / ``comments`` (plus the
``tasks/<id>/`` directory probe) and every assertion pins migration
classification (canonical / NEEDS_* / EMPTY_ID / excerpt text) — never
the seeded status — so the suite passed while teaching the abolished
value as live (store/users/graph-roles precedent). The seeds are now
``deferred`` (the successor default); these pins keep them there.

These pins use the file text + the pure validation gate + the scanner
source text only: no store, no PostgreSQL — they run in containers where
the store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import inspect
import warnings
from pathlib import Path

from scitex_cards._migration._migrate import classify_row
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import _validate_tasks

_SUBJECT = Path(__file__).with_name("test__migrate.py")


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
    # Act + Assert — non-vacuity: the classifier fixtures still seed a
    # status, and the file under test is the migration-scanner suite.
    assert "status: deferred" in source or '"status": "deferred"' in source
    assert "classify_row" in source


def test_pending_seed_warns_while_deferred_is_silent():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    classification assertions claim to pin. This is why the fixture must
    stay ``deferred``.
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


def test_classify_row_is_status_blind():
    # Arrange — the classification decision must derive from id / note /
    # title / comments / dir probe, never from a task status field.
    source = inspect.getsource(classify_row)
    # Act + Assert — no "status" read anywhere in the function body.
    assert "status" not in source


def test_status_enum_mirror_holds():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
