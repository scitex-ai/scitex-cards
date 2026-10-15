#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the store-suite fixtures (pending abolition).

``tests/scitex_cards/test__store.py`` seeded ``status: pending`` in 4
fixtures after ``pending`` was abolished 2026-07-10: the ``add_task``
round-trip / on-disk / id pins (which assert the echoed row, the disk
write, and the id — never the abolished value itself) and the
``_legacy_row_without_created_by`` helper (which asserts ``created_by``
absence, never the status). A ``pending`` seed still writes — validation
only WARNS on it (operator ruling 2026-07-10) — so every one of these
tests passed while teaching the abolished value as live (model 31 +
throughput 12 + deadline 11 + is_overdue 19 + reassign_all + priority +
nudge + timeline + crash_safe_writer precedent). The seeds are now
``deferred`` (the successor default); these pins keep them there.

Deliberately UNTOUCHED in the subject file: the ``not-a-status``
tolerance pair (a different contract — unknown values warn loudly but
never destroy the card), the ``past-pending`` / ``future-pending`` card
ids in the overdue suite (id substrings, not status seeds), and the
prose line about past-due rows.

These pins use the file text + the pure validation gate only: no store,
no PostgreSQL — they run in containers where the store-backed suite
refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import _validate_tasks

_SUBJECT = Path(__file__).with_name("test__store.py")


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
    # Act + Assert — non-vacuity: the fixtures still seed statuses, and the
    # file under test is the store suite, not an empty stub.
    assert 'status="deferred"' in source
    assert '"status": "deferred"' in source
    assert "def test_add_task_returns_inserted_dict" in source
    assert "def _legacy_row_without_created_by" in source


def test_pending_seed_warns_while_deferred_is_silent():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    round-trip / on-disk / created_by tests claim to pin. This is why the
    fixtures must stay ``deferred``.
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


def test_status_enum_mirror_holds():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
