#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the users-suite fixtures (pending abolition).

``tests/scitex_cards/_users/test__users.py`` seeded the abolished
``pending`` status in 3 fixtures (store_with_two_tasks, two
task-write-after-user-write round-trips) after ``pending`` was abolished
2026-07-10. Every assertion pins the users registry — tasks undisturbed
by a user write, users readable alongside tasks, user surviving a task
write, task recorded alongside a user — never the status.
``_validate_tasks`` only WARNS on a ``pending`` row (operator ruling
2026-07-10), so the suite passed while encoding a dead status as live.
Same dead-fixture class as the model (31) + crash_safe_writer (13) +
store (4) + timeline + nudge + priority + reassign_all + throughput
(12) + deadline (11) + is_overdue (19) repoints. The fixtures now seed
``deferred`` (the successor default); no src change (the user write path
is status-blind: ``_save_users_unlocked`` replaces only ``users:``,
leaving ``tasks:`` byte-identical).

These pins use the file text + the pure validation gate + a tmp_path
YAML users-write only: no store, no PostgreSQL — they run in containers
where the store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import yaml

from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._users._store import _save_users_unlocked
from scitex_cards._validate import _validate_tasks

_SUBJECT = Path(__file__).with_name("test__users.py")


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
    assert 'status="deferred"' in source


def test_the_successor_default_seed_is_present_case2():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "def test_user_write_does_not_disturb_tasks" in source


def test_the_successor_default_seed_is_present_case3():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "def test_task_write_after_user_write_records_the_task" in source


def test_pending_seed_warns_while_deferred_is_silent_case1():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    users-registry tests claim to pin. This is why the fixtures must stay
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
    users-registry tests claim to pin. This is why the fixtures must stay
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


def test_user_write_preserves_tasks_including_legacy_pending_case1(tmp_path):
    """The repoint changes no verdict: the user write is status-blind.

    A users-only write replaces ``users:`` and leaves ``tasks:`` identical
    — including a legacy ``pending`` row, which survives verbatim. So the
    disturbed-tasks / readable-section tests assert the same thing under
    ``deferred`` as they did under ``pending``; the fixtures are
    ``deferred`` to pin the live path, not to make anything pass.
    """
    # Arrange
    store = tmp_path / "tasks.yaml"
    tasks = [
        {"id": "t-legacy", "title": "Legacy", "status": "pending"},
        {"id": "t-live", "title": "Live", "status": "deferred"},
    ]
    store.write_text(yaml.safe_dump({"tasks": tasks, "users": []}))
    _save_users_unlocked(
        [{"id": "u_a", "kind": "agent", "names": ["alice"]}], store
    )
    # Act
    doc = yaml.safe_load(store.read_text())
    # Assert
    assert [(t["id"], t["status"]) for t in doc["tasks"]] == [
        ("t-legacy", "pending"),
        ("t-live", "deferred"),
    ]


def test_user_write_preserves_tasks_including_legacy_pending_case2(tmp_path):
    """The repoint changes no verdict: the user write is status-blind.

    A users-only write replaces ``users:`` and leaves ``tasks:`` identical
    — including a legacy ``pending`` row, which survives verbatim. So the
    disturbed-tasks / readable-section tests assert the same thing under
    ``deferred`` as they did under ``pending``; the fixtures are
    ``deferred`` to pin the live path, not to make anything pass.
    """
    # Arrange
    store = tmp_path / "tasks.yaml"
    tasks = [
        {"id": "t-legacy", "title": "Legacy", "status": "pending"},
        {"id": "t-live", "title": "Live", "status": "deferred"},
    ]
    store.write_text(yaml.safe_dump({"tasks": tasks, "users": []}))
    _save_users_unlocked(
        [{"id": "u_a", "kind": "agent", "names": ["alice"]}], store
    )
    # Act
    doc = yaml.safe_load(store.read_text())
    # Assert
    assert [u["names"][0] for u in doc["users"]] == ["alice"]


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
