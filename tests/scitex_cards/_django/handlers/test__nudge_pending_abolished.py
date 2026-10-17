#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the nudge-handler legacy-status fixture (pending abolition).

``tests/scitex_cards/_django/handlers/test_nudge.py`` seeded
``status: pending`` in the ``_STORE_TEXT`` YAML fixture after ``pending``
was abolished 2026-07-10. :func:`handle_nudge` forwards
``list(board.tasks)`` to the notify-body builder without gating on any
task status, and every assertion pins the HTTP wire (405 method gate,
400 body validation, no-turn-url reason, 429 cooldown) — never the
seeded status — so the suite passed while teaching the abolished value
as live (model/reassign_all/throughput/deadline/is_overdue/priority
precedent). The seed is now ``deferred`` (an open, valid status keeping
t2 out of the in_progress lane exactly as before); these pins keep it
there.

These pins use the file text + the pure validation gate + the source
text only: no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import inspect
import warnings
from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_cards._django.handlers import nudge as _nudge_mod  # noqa: E402
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES  # noqa: E402
from scitex_cards._validate import _validate_tasks  # noqa: E402

_SUBJECT = Path(__file__).with_name("test_nudge.py")


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
    assert "status: deferred" in source


def test_the_successor_default_seed_is_present_case2():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "_STORE_TEXT" in source


def test_pending_seed_warns_while_deferred_is_silent_case1():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    nudge wire assertions claim to pin. This is why the fixture must stay
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
    nudge wire assertions claim to pin. This is why the fixture must stay
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


def test_nudge_handler_is_status_blind_case1():
    # Arrange — the dispatch path must forward the board without gating
    # on any task status field.
    # Arrange
    # Act
    source = inspect.getsource(_nudge_mod.handle_nudge)
    # Assert
    assert "list(board.tasks)" in source


def test_nudge_handler_is_status_blind_case2():
    # Arrange — the dispatch path must forward the board without gating
    # on any task status field.
    # Arrange
    # Act
    source = inspect.getsource(_nudge_mod.handle_nudge)
    # Assert
    assert 'task["status"]' not in source


def test_nudge_handler_is_status_blind_case3():
    # Arrange — the dispatch path must forward the board without gating
    # on any task status field.
    # Arrange
    # Act
    source = inspect.getsource(_nudge_mod.handle_nudge)
    # Assert
    assert "task['status']" not in source


def test_nudge_handler_is_status_blind_case4():
    # Arrange — the dispatch path must forward the board without gating
    # on any task status field.
    # Arrange
    # Act
    source = inspect.getsource(_nudge_mod.handle_nudge)
    # Assert
    assert 'task.get("status")' not in source


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
