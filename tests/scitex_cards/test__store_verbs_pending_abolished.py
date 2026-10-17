#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the store-verbs duplicate-id fixture (pending abolition).

``tests/scitex_cards/test__store_verbs.py`` seeded ``status: pending`` in
the ``test_duplicate_id_raises`` restore payload after ``pending`` was
abolished 2026-07-10. :func:`scitex_cards._store_lifecycle.restore_task`
raises on a duplicate id that is not a tombstone (id + tombstone check
only) and never reads the status, so the seed passed while teaching the
abolished value as live (model 31 + throughput 12 + deadline 11 +
is_overdue 19 + reassign_all + priority + nudge + timeline +
crash_safe_writer + store 4 + users 3 precedent). The seed is now
``deferred`` (the successor default); these pins keep it there.

Deliberately UNTOUCHED in the subject file: the ``both pending`` /
``leaves c pending`` prose in the unblock-drive tests (dependency
pending, not a status seed — the sweep only matches lines containing
``status``), and the ``status="done"`` / ``status="blocked"`` live seeds.

These pins use the file text + the pure validation gate + the source
text only: no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import _validate_tasks

_SUBJECT = Path(__file__).with_name("test__store_verbs.py")
_SRC = (
    Path(__file__).parent.parent.parent
    / "src"
    / "scitex_cards"
    / "_store_lifecycle.py"
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
    assert "def test_duplicate_id_raises" in source


def test_pending_seed_warns_while_deferred_is_silent_case1():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    duplicate-id test claims to pin. This is why the fixture must stay
    ``deferred``.
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
    duplicate-id test claims to pin. This is why the fixture must stay
    ``deferred``.
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


def test_duplicate_matches_on_id_not_status_case1():
    # Arrange — the duplicate guard must not gate on the status enum. The
    # window is the guard itself (the `existing` lookup through the
    # "already present" raise), NOT the whole function tail: the
    # status-default for status-less mappings lives two statements above
    # the guard and is pinned separately (restore_task_status file) — the
    # guard's own contract is id + tombstone only.
    # Arrange
    source = _SRC.read_text()
    tail = source.split("already present")[0].split("def restore_task")[1]
    # Act
    block = tail.split("existing = next(")[1]
    # Assert
    assert 't.get("id") == tid' in block


def test_duplicate_matches_on_id_not_status_case2():
    # Arrange — the duplicate guard must not gate on the status enum. The
    # window is the guard itself (the `existing` lookup through the
    # "already present" raise), NOT the whole function tail: the
    # status-default for status-less mappings lives two statements above
    # the guard and is pinned separately (restore_task_status file) — the
    # guard's own contract is id + tombstone only.
    # Arrange
    source = _SRC.read_text()
    tail = source.split("already present")[0].split("def restore_task")[1]
    # Act
    block = tail.split("existing = next(")[1]
    # Assert
    assert "_is_tombstoned" in block


def test_duplicate_matches_on_id_not_status_case3():
    # Arrange — the duplicate guard must not gate on the status enum. The
    # window is the guard itself (the `existing` lookup through the
    # "already present" raise), NOT the whole function tail: the
    # status-default for status-less mappings lives two statements above
    # the guard and is pinned separately (restore_task_status file) — the
    # guard's own contract is id + tombstone only.
    # Arrange
    source = _SRC.read_text()
    tail = source.split("already present")[0].split("def restore_task")[1]
    # Act
    block = tail.split("existing = next(")[1]
    # Assert
    assert '"status"' not in block


def test_duplicate_matches_on_id_not_status_case4():
    # Arrange — the duplicate guard must not gate on the status enum. The
    # window is the guard itself (the `existing` lookup through the
    # "already present" raise), NOT the whole function tail: the
    # status-default for status-less mappings lives two statements above
    # the guard and is pinned separately (restore_task_status file) — the
    # guard's own contract is id + tombstone only.
    # Arrange
    source = _SRC.read_text()
    tail = source.split("already present")[0].split("def restore_task")[1]
    # Act
    block = tail.split("existing = next(")[1]
    # Assert
    assert "'status'" not in block


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
