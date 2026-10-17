#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the wake-watcher fixtures (pending abolition).

Every fixture in ``test__wake_watcher.py`` seeded ``status: pending`` after
``pending`` was abolished 2026-07-10 — 36 seeds across the seed / added /
comment / flip / debounce / fan-out / no-change-tick classes. A ``pending``
dict still diffs fine (the watcher never validates statuses), so the whole
suite passed while teaching the abolished value as live
(org/mermaid/idle-guard/write-blocker/groups/reconcile-seam precedent). The
seeds are now ``deferred`` (the successor default); these pins keep them
there.

These pins use the file text + the pure diff predicate only: no store, no
PostgreSQL — they run in containers where the store-backed suite refuses
via the fail-LOUD guard.
"""

from __future__ import annotations

from pathlib import Path

from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._wake_watcher import WatcherState, detect_changes

_SUBJECT = Path(__file__).with_name("test__wake_watcher.py")


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


def test_the_live_status_seed_is_present_case1():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert '"status": "deferred"' in source


def test_the_live_status_seed_is_present_case2():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert 'status="deferred"' in source


def test_the_live_status_seed_is_present_case3():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "detect_changes" in source


def test_live_seed_never_wakes_the_watcher():
    """Guard the interchangeability itself: the value is arbitrary here.

    ``detect_changes`` compares status strings opaquely (``prev !=
    current``) — it never validates them — so a ``deferred`` seed exercises
    exactly the same diff/debounce paths the ``pending`` seed did, and a
    legacy ``pending`` dict diffs identically (tolerated, not rejected).
    This is why the fixtures must stay ``deferred``.
    """
    # Arrange — first pass seeds the snapshot, second pass adds a task.
    live_seed = [{"id": "a", "title": "A", "status": "deferred", "agent": "x"}]
    # Act — seeding never wakes, for either value.
    # Assert
    assert detect_changes(WatcherState(), live_seed, now=0.0) == []


def test_legacy_seed_never_wakes_the_watcher():
    """Guard the interchangeability itself: the value is arbitrary here.

    ``detect_changes`` compares status strings opaquely (``prev !=
    current``) — it never validates them — so a ``deferred`` seed exercises
    exactly the same diff/debounce paths the ``pending`` seed did, and a
    legacy ``pending`` dict diffs identically (tolerated, not rejected).
    This is why the fixtures must stay ``deferred``.
    """
    # Arrange — first pass seeds the snapshot, second pass adds a task.
    legacy_seed = [{"id": "a", "title": "A", "status": "pending", "agent": "x"}]
    # Act — seeding never wakes, for either value.
    # Assert
    assert detect_changes(WatcherState(), legacy_seed, now=0.0) == []


def test_flip_off_either_seed_fires_status_changed_wake():
    """Guard the interchangeability itself: the value is arbitrary here.

    ``detect_changes`` compares status strings opaquely (``prev !=
    current``) — it never validates them — so a ``deferred`` seed exercises
    exactly the same diff/debounce paths the ``pending`` seed did, and a
    legacy ``pending`` dict diffs identically (tolerated, not rejected).
    This is why the fixtures must stay ``deferred``.
    """
    # Arrange — first pass seeds the snapshot, second pass adds a task.
    live_seed = [{"id": "a", "title": "A", "status": "deferred", "agent": "x"}]
    legacy_seed = [{"id": "a", "title": "A", "status": "pending", "agent": "x"}]
    flipped = [{"id": "a", "title": "A", "status": "in_progress", "agent": "x"}]
    # Act — a flip off either seed fires the same status_changed wake.
    bad = []
    for seed in (live_seed, legacy_seed):
        state = WatcherState()
        detect_changes(state, seed, now=0.0)
        out = detect_changes(state, flipped, now=100.0)
        if len(out) != 1 or out[0].trigger_kind != "status_changed":
            bad.append([len(out), [w.trigger_kind for w in out]])
    # Assert
    assert not bad, bad


def test_live_flip_summary_names_the_old_status():
    """The live flip names both live statuses in its summary."""
    # Arrange
    live_seed = [{"id": "a", "title": "A", "status": "deferred", "agent": "x"}]
    flipped = [{"id": "a", "title": "A", "status": "in_progress", "agent": "x"}]
    # Act
    state = WatcherState()
    detect_changes(state, live_seed, now=0.0)
    out = detect_changes(state, flipped, now=100.0)
    # Assert
    assert "deferred" in out[0].summary


def test_live_flip_summary_names_the_new_status():
    """The live flip names both live statuses in its summary."""
    # Arrange
    live_seed = [{"id": "a", "title": "A", "status": "deferred", "agent": "x"}]
    flipped = [{"id": "a", "title": "A", "status": "in_progress", "agent": "x"}]
    # Act
    state = WatcherState()
    detect_changes(state, live_seed, now=0.0)
    out = detect_changes(state, flipped, now=100.0)
    # Assert
    assert "in_progress" in out[0].summary


def test_abolished_valid_mirror_case1():
    # Arrange + Act + Assert — the enum facts this repoint rests on.
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_abolished_valid_mirror_case2():
    # Arrange + Act + Assert — the enum facts this repoint rests on.
    # Arrange
    # Act
    # Assert
    assert "pending" not in VALID_STATUSES


def test_abolished_valid_mirror_case3():
    # Arrange + Act + Assert — the enum facts this repoint rests on.
    # Arrange
    # Act
    # Assert
    assert "deferred" in VALID_STATUSES
