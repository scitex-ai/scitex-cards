#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the timeline-handler legacy-status fixture (pending abolition).

``tests/scitex_cards/_django/handlers/test__timeline_view.py`` seeded
``status: pending`` in the ``store_ungrouped`` doc after ``pending``
was abolished 2026-07-10. The ``/timeline`` lane path
(:func:`_lane_for` + :func:`_event_within_window`) reads only
owner/group/project/title + timestamps — never the task status — and
every assertion in that file pins the HTTP wire / lane bucketing /
window filtering, never the seeded status — so the suite passed while
teaching the abolished value as live (nudge/priority/model/reassign_all/
throughput/deadline/is_overdue precedent). The seed is now
``deferred`` (an open, valid status keeping the row in-window exactly
as before); these pins keep it there.

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

from scitex_cards._django.handlers import timeline as _timeline_mod  # noqa: E402
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES  # noqa: E402
from scitex_cards._validate import _validate_tasks  # noqa: E402

_SUBJECT = Path(__file__).with_name("test__timeline_view.py")


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
    assert "store_ungrouped" in source


def test_pending_seed_warns_while_deferred_is_silent_case1():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    lane assertions claim to pin. This is why the fixture must stay
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
    lane assertions claim to pin. This is why the fixture must stay
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


def test_timeline_lane_is_status_blind():
    # Arrange — the lane + window path must never gate on task status.
    lane_source = inspect.getsource(_timeline_mod._lane_for)
    window_source = inspect.getsource(_timeline_mod._event_within_window)
    # Act — lane projects owner/group/project/title only;
    # window admits on timestamps only; status is copied through to the
    # event row, never read for admission.
    probed = (lane_source, window_source)
    bad = [
        which
        for source in probed
        for which in ('["status"]', "['status']", '.get("status")')
        if which in source
    ]
    # Assert
    assert not bad, bad


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
