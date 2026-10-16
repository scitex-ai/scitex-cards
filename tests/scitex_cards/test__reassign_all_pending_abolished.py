#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the reassign_all legacy-owner fixture (pending abolition).

``tests/scitex_cards/test__reassign_all.py`` seeded ``status: pending`` in
the ``bulk_move_of_legacy_assignee_card`` fixture after ``pending`` was
abolished 2026-07-10. :func:`scitex_cards._store_reassign.reassign_all`
matches on owner (``agent`` falling back to legacy ``assignee``) and never
reads the status, so the seed passed while teaching the abolished value as
live (org/mermaid/is_overdue/deadline/throughput/db-mirror precedent).
The seed is now ``deferred`` (the successor default); these pins keep it
there.

These pins use the file text + the pure validation gate + the source text
only: no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import _validate_tasks

_SUBJECT = Path(__file__).with_name("test__reassign_all.py")
_SRC = (
    Path(__file__).parent.parent.parent
    / "src"
    / "scitex_cards"
    / "_store_reassign.py"
)


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
    # Act + Assert — non-vacuity: the legacy-owner fixture still seeds a
    # status, and the file under test is the reassign_all suite.
    assert '"status": "deferred"' in source
    assert "def test_matches_legacy_assignee_only_owner" in source


def test_pending_seed_warns_while_deferred_is_silent():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    owner-matching tests claim to pin. This is why the fixture must stay
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


def test_bulk_move_matches_on_owner_not_status():
    # Arrange — the owner-match loop must not gate on the status enum.
    source = _SRC.read_text()
    loop = source.split("for task in tasks:")[1].split("_model._save_doc_unlocked")[0]
    # Act + Assert — owner fields only; no status read in the match path.
    assert 'task.get("agent")' in loop
    assert 'task.get("assignee")' in loop
    assert '"status"' not in loop
    assert "'status'" not in loop


def test_status_enum_mirror_holds():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
