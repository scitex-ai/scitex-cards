#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the migrate-apply fixtures (pending abolition).

``tests/scitex_cards/_migration/test__migrate_apply.py`` seeded
``status: pending`` in its ``_seed_db`` fixtures after ``pending`` was
abolished 2026-07-10. :func:`apply_lane` builds on :func:`classify_row`
(which reads only ``id`` / ``note`` / ``title`` / ``comments`` plus the
``tasks/<id>/`` directory probe) and the README/strip helpers (which
read only ``note`` / ``title`` / ``comments`` and preserve ``status``
verbatim) — and every assertion pins migration behavior (note bytes,
title trim, idempotency, dry-run, git commit), never the seeded status —
so the suite passed while teaching the abolished value as live
(scanner/store/users/graph-roles precedent). The seeds are now
``deferred`` (the successor default); these pins keep them there.

These pins use the file text + the pure validation gate + the migrator
source text only: no store, no PostgreSQL — they run in containers where
the store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import inspect
import warnings
from pathlib import Path

from scitex_cards._migration._migrate import apply_lane, classify_row
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import _validate_tasks

_SUBJECT = Path(__file__).with_name("test__migrate_apply.py")


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


def test_the_successor_default_seed_is_present():
    # Arrange
    source = _SUBJECT.read_text()
    # Act + Assert — non-vacuity: the apply fixtures still seed a
    # status, and the file under test is the migrate-apply suite.
    assert "status: pending" not in source
    assert "status: deferred" in source
    assert "apply_lane" in source


def test_deferred_fixture_loads_silently():
    """Guard the successor itself: the new seeds differ from pending on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    migration assertions claim to pin. This is why the fixture must
    stay ``deferred``.
    """
    # Arrange
    live = [{"id": "a", "title": "A", "status": "deferred"}]
    # Act
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(live, source="/some/store.db")
    # Assert
    assert [str(x.message) for x in caught] == []


def test_apply_path_is_status_blind():
    # Arrange — the migration decision + README/strip path must derive
    # from id / note / title / comments / dir probe, never from status.
    apply_source = inspect.getsource(apply_lane)
    classify_source = inspect.getsource(classify_row)
    # Act + Assert — no "status" read anywhere in either function body.
    assert "status" not in apply_source
    assert "status" not in classify_source


def test_status_enum_mirror_holds():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
