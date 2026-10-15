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


def test_the_live_status_seed_is_present():
    # Arrange
    source = _SUBJECT.read_text()
    # Act + Assert — non-vacuity: the fixtures still seed statuses, and the
    # file under test is the wake-watcher suite, not an empty stub.
    assert '"status": "deferred"' in source
    assert 'status="deferred"' in source
    assert "detect_changes" in source


def test_diff_predicate_is_status_blind_so_the_repoint_keeps_intent():
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
    # Act — seeding never wakes, for either value.
    assert detect_changes(WatcherState(), live_seed, now=0.0) == []
    assert detect_changes(WatcherState(), legacy_seed, now=0.0) == []
    # Act — a flip off either seed fires the same status_changed wake.
    for seed in (live_seed, legacy_seed):
        state = WatcherState()
        detect_changes(state, seed, now=0.0)
        out = detect_changes(
            state,
            [{"id": "a", "title": "A", "status": "in_progress", "agent": "x"}],
            now=100.0,
        )
        # Assert
        assert len(out) == 1
        assert out[0].trigger_kind == "status_changed"
    # Act — the live flip names both live statuses in its summary.
    state = WatcherState()
    detect_changes(state, live_seed, now=0.0)
    out = detect_changes(
        state,
        [{"id": "a", "title": "A", "status": "in_progress", "agent": "x"}],
        now=100.0,
    )
    # Assert
    assert "deferred" in out[0].summary
    assert "in_progress" in out[0].summary


def test_abolished_valid_mirror():
    # Arrange + Act + Assert — the enum facts this repoint rests on.
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
