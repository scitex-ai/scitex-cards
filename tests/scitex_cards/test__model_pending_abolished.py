#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the model-suite fixtures (pending abolition).

``tests/scitex_cards/test__model.py`` seeded ``status: pending`` in 31
fixtures after ``pending`` was abolished 2026-07-10: validator-direct
structural-fault tests (missing title/id, non-string scope/assignee,
unknown kind, compute-metadata misuse, non-string task/pr_url/goal),
store-seeding YAML texts (order/parent/scope/kind/decision/operator-field
suites), and the decision-lifecycle assertion itself. A ``pending`` seed
still loads — validation only WARNS on it (operator ruling 2026-07-10) —
so every one of these tests passed while teaching the abolished value as
live (org/mermaid/is_overdue/deadline/throughput/db-mirror precedent).
The seeds are now ``deferred`` (the successor default); these pins keep
them there.

Deliberately UNTOUCHED in the subject file: the ``wibble`` unknown-status
tolerance pair (a different contract — newer-writer values must never take
the store down) and the ``deferred replaced the abolished pending``
comment beside the dataclass-default pin (an abolition note, not a seed).

These pins use the file text + the pure validation gate only: no store,
no PostgreSQL — they run in containers where the store-backed suite
refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import _validate_tasks

_SUBJECT = Path(__file__).with_name("test__model.py")


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
    assert "status: deferred" in source


def test_the_successor_default_seed_is_present_case3():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "def test_load_tasks_decision_kind_uses_existing_statuses" in source


def test_pending_seed_warns_while_deferred_is_silent_case1():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    structural-fault tests claim to pin. This is why the fixtures must
    stay ``deferred``.
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
    structural-fault tests claim to pin. This is why the fixtures must
    stay ``deferred``.
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


def test_decision_lifecycle_doc_names_the_successor_state_case1():
    # Arrange — the decision-kind lifecycle pin must not re-teach the
    # abolished value as the awaiting-resolution state.
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "uses VALID_STATUSES (deferred → done)" in source


def test_decision_lifecycle_doc_names_the_successor_state_case2():
    # Arrange — the decision-kind lifecycle pin must not re-teach the
    # abolished value as the awaiting-resolution state.
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "uses VALID_STATUSES (pending → done)" not in source


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
