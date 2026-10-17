#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the store-doc-preservation fixtures (pending abolition).

``tests/scitex_cards/test__store_doc_preservation.py`` seeded
``status: pending`` in 5 fixtures after ``pending`` was abolished
2026-07-10: the ``_seed_store_with_users`` default (which stamps EVERY
seeded task, so every preservation test below it ran on a tolerated
legacy row), the ``_doc_after_update`` seed (which flips to
``in_progress`` — the assertion pins the flip, never the seed), and
the ``_doc_after_save_doc_unlocked`` / ``_doc_after_save_tasks_unlocked``
raw rows (which pin the ``users:`` section + the task ids, never the
status). A ``pending`` seed still writes — validation only WARNS on it
(operator ruling 2026-07-10) — so every one of these tests passed while
teaching the abolished value as live (store 4 + store-verbs +
store-card-events + users 3 + graph-roles + crash_safe_writer +
model 31 precedent). The seeds are now ``deferred`` (the successor
default); these pins keep them there.

No src change: the preservation path is status-blind — the assertions
pin the ``users:`` registry and the mutated task ids, and none of the
write verbs gate section recovery on the status enum — so repointing
the fixtures changes which read path is exercised (live, not
tolerated-legacy) without changing what is proven.

These pins use the file text + the pure validation gate only: no
store, no PostgreSQL — they run in containers where the store-backed
suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import _validate_tasks

_SUBJECT = Path(__file__).with_name("test__store_doc_preservation.py")


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


def test_the_successor_default_seed_is_present_case1():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert '"status": "deferred"' in source


def test_the_successor_default_seed_is_present_case2():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "def test_users_survives_add_task" in source


def test_the_successor_default_seed_is_present_case3():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "def _seed_store_with_users" in source


def test_pending_seed_warns_while_deferred_is_silent_case1():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    preservation / flip / tombstone tests claim to pin. This is why the
    fixtures must stay ``deferred``.
    """
    # Arrange
    pending = [{"id": "a", "title": "A", "status": "pending"}]
    live = [{"id": "a", "title": "A", "status": "deferred"}]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(pending, source="/some/store.db")
    pending_warnings = [str(x.message) for x in caught]
    # Act
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(live, source="/some/store.db")
    # Assert
    live_warnings = [str(x.message) for x in caught]
    assert any("abolished" in text for text in pending_warnings)


def test_pending_seed_warns_while_deferred_is_silent_case2():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    preservation / flip / tombstone tests claim to pin. This is why the
    fixtures must stay ``deferred``.
    """
    # Arrange
    pending = [{"id": "a", "title": "A", "status": "pending"}]
    live = [{"id": "a", "title": "A", "status": "deferred"}]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(pending, source="/some/store.db")
    pending_warnings = [str(x.message) for x in caught]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(live, source="/some/store.db")
    # Act
    live_warnings = [str(x.message) for x in caught]
    # Assert
    assert live_warnings == []


def test_the_update_flip_exercises_a_live_transition_case1():
    # Arrange — the flip helper must move deferred -> in_progress (a live
    # transition), not rescue a tolerated-legacy pending row into
    # in_progress (which would pin the save path's leniency, not the
    # preservation property).
    # Arrange
    source = _SUBJECT.read_text()
    # Act
    block = source.split("def _doc_after_update")[1].split("def _doc_after_delete")[0]
    # Assert
    assert '"pending"' not in block


def test_the_update_flip_exercises_a_live_transition_case2():
    # Arrange — the flip helper must move deferred -> in_progress (a live
    # transition), not rescue a tolerated-legacy pending row into
    # in_progress (which would pin the save path's leniency, not the
    # preservation property).
    # Arrange
    source = _SUBJECT.read_text()
    # Act
    block = source.split("def _doc_after_update")[1].split("def _doc_after_delete")[0]
    # Assert
    assert '"deferred"' in block


def test_the_update_flip_exercises_a_live_transition_case3():
    # Arrange — the flip helper must move deferred -> in_progress (a live
    # transition), not rescue a tolerated-legacy pending row into
    # in_progress (which would pin the save path's leniency, not the
    # preservation property).
    # Arrange
    source = _SUBJECT.read_text()
    # Act
    block = source.split("def _doc_after_update")[1].split("def _doc_after_delete")[0]
    # Assert
    assert '"in_progress"' in block


def test_status_enum_mirror_holds_case1():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_status_enum_mirror_holds_case2():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    # Arrange
    # Act
    # Assert
    assert "pending" not in VALID_STATUSES


def test_status_enum_mirror_holds_case3():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    # Arrange
    # Act
    # Assert
    assert "deferred" in VALID_STATUSES
