#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the store-card-events fixture (pending abolition).

``tests/scitex_cards/test__store_card_events.py`` seeded ``status="pending"``
in seven fixtures after ``pending`` was abolished 2026-07-10. The emit path
(``_store_mutate.update_task``) branches only on ``_to == "done"``
(``completed`` vs ``status_changed``) and never reads the from-value, so the
seeds passed while teaching the abolished value as the live starting point
(model 31 + throughput 12 + deadline 11 + is_overdue 19 + reassign_all +
priority + nudge + timeline + crash_safe_writer + store 4 + users 3 +
store-verbs precedent). The seeds are now ``deferred`` (the backlog state);
these pins keep them there.

Deliberately UNTOUCHED in the subject file: nothing — the repoint left zero
``pending`` references there, and the abolition note lives on the first
repointed fixture.

These pins use the file text + the pure validation gate + the emit source
text only: no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import _validate_tasks

_SUBJECT = Path(__file__).with_name("test__store_card_events.py")
_SRC = (
    Path(__file__).parent.parent.parent
    / "src"
    / "scitex_cards"
    / "_store_mutate.py"
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


def test_the_successor_flip_is_pinned():
    # Arrange
    source = _SUBJECT.read_text()
    # Act + Assert — non-vacuity: the from/to pin still asserts a flip, and
    # the file under test is the card-events suite.
    assert '("deferred", "in_progress")' in source
    assert "def test_status_flip_emits_status_changed_with_from_to" in source


def test_the_same_value_no_event_pin_is_live():
    # Arrange
    source = _SUBJECT.read_text()
    # Act + Assert — the no-event-on-same-value pin re-sets the live
    # successor, not the abolished value.
    assert "def test_update_status_to_same_value_emits_no_event" in source
    assert 'status="deferred", entry_points' in source


def test_pending_seed_warns_while_deferred_is_silent():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    status_changed pins claim. This is why the fixtures must stay
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


def test_emit_branches_on_done_only():
    # Arrange — the flip emit must not gate on the from-value: any
    # non-done flip (including the repointed deferred->in_progress) is a
    # status_changed with {from,to}.
    source = _SRC.read_text()
    block = source.split("if status_change is not None:")[1][:600]
    # Act + Assert — done-branch only; no from-value gate in the emit.
    assert '_to == "done"' in block
    assert '"status_changed"' in block
    assert "pending" not in block


def test_status_enum_mirror_holds():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES


def test_detector_is_not_vacuous():
    fixture = 'add_task(store=store, id="c-1", status="pending")\n'
    assert "status" in fixture and "pending" in fixture
