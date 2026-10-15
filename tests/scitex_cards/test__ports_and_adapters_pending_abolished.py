#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the ports/adapters fixtures (pending abolition).

The four ``LocalFileSync`` fixtures in ``test__ports_and_adapters.py`` seeded
``status: pending`` after ``pending`` was abolished 2026-07-10: two DB seeds
(the load test and the save round-trip) and two YAML probe bodies (the mtime
probes, whose text is stat-only and never validated). A ``pending`` seed
still loads — save-side validation only WARNS on it (operator ruling
2026-07-10) — so every one of these tests passed while teaching the abolished
value as live (org/mermaid/idle-guard/write-blocker/groups/reconcile-seam
precedent). The seeds are now ``in_progress``; these pins keep them there.

These pins use the file text + the pure validation gate only: no store, no
PostgreSQL — they run in containers where the store-backed suite refuses
via the fail-LOUD guard.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from scitex_cards._validate import _validate_tasks

_SUBJECT = Path(__file__).with_name("test__ports_and_adapters.py")


def test_no_fixture_seeds_the_abolished_status():
    # Arrange
    source = _SUBJECT.read_text()
    # Act
    seeds = [
        line
        for line in source.splitlines()
        if "status" in line and "pending" in line and "abolished" not in line
    ]
    # Assert — every remaining "pending" is an abolition-note comment.
    assert seeds == []


def test_the_live_status_seed_is_present():
    # Arrange
    source = _SUBJECT.read_text()
    # Act + Assert — non-vacuity: the fixtures still seed statuses, and the
    # file under test is the ports/adapters suite, not an empty stub.
    assert '"status": "in_progress"' in source
    assert "status: in_progress" in source
    assert "LocalFileSync" in source


def test_pending_seed_warns_while_in_progress_is_silent():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``in_progress`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    LocalFileSync tests claim to pin. This is why the fixtures must stay
    ``in_progress``.
    """
    # Arrange
    pending = [{"id": "a", "title": "A", "status": "pending"}]
    live = [{"id": "a", "title": "A", "status": "in_progress"}]
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
