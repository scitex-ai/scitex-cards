#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`_iter_entry_points` discovers ONCE per process, not once per card event.

`importlib.metadata.entry_points()` re-reads every installed package's
entry_points.txt (~126 files in a real fleet venv). It runs on EVERY card event
via dispatch_event, so uncached it was the single largest cost in a card write —
sac profiled 2.18s of a 3.24s warm add_task there. This pins the cache so the
cost is paid ONCE per process, not per write.

Effectiveness is read from ``functools``' own ``cache_info()``. These tests
used to rebind ``importlib.metadata.entry_points`` with a counting wrapper,
which measured the same thing far more dangerously: that is the stdlib call
EVERY package's entry-point discovery goes through, so a patch that outlived
its test would break plugin loading everywhere and surface nowhere near here.
The cache already counts itself.
"""

from __future__ import annotations

import pytest

from scitex_cards._hooks import _plugins


@pytest.fixture(autouse=True)
def _clear_cache():
    # The cache is process-global; isolate each test from the others and from
    # any real discovery that ran at import time.
    _plugins._iter_entry_points.cache_clear()
    yield
    _plugins._iter_entry_points.cache_clear()


def test_entry_points_are_scanned_only_once_across_many_calls():
    # Arrange — the autouse fixture already cleared the cache and its tally.
    # Act — call it many times, as a busy fleet writing many cards would.
    for _ in range(50):
        _plugins._iter_entry_points()

    # Assert — the ~126-file scan happened ONCE, not 50 times.
    assert _plugins._iter_entry_points.cache_info().misses == 1


def test_the_other_forty_nine_calls_were_served_from_cache():
    # Arrange — the autouse fixture already cleared the cache and its tally.
    # Act
    for _ in range(50):
        _plugins._iter_entry_points()

    # Assert — the other half of "scanned once": the rest were SERVED. A miss
    # count of 1 on its own would also hold if 49 calls had never reached the
    # cache at all.
    assert _plugins._iter_entry_points.cache_info().hits == 49


def test_cache_clear_forces_rediscovery():
    # Arrange — the escape hatch must work: a live plugin reload / a test can
    # force a fresh scan. Without this, caching would be a one-way door.
    _plugins._iter_entry_points()

    # Act — drop the cache, then scan again.
    _plugins._iter_entry_points.cache_clear()
    _plugins._iter_entry_points()

    # Assert — the post-clear call MISSED, so it genuinely rediscovered rather
    # than being handed the pre-clear result. `cache_clear` zeroes the tally as
    # well as the cache, which is why this is 1 and not 2: the count restarts,
    # so "the next call missed" is the whole statement and it does not depend
    # on remembering what happened before the clear.
    assert _plugins._iter_entry_points.cache_info().misses == 1


def test_cached_result_is_stable_across_calls():
    # Arrange — cached calls must return the same discovered set: the behaviour
    # dispatch_event relies on is unchanged, only faster.
    # Act
    first = list(_plugins._iter_entry_points())
    second = list(_plugins._iter_entry_points())

    # Assert
    assert [getattr(e, "name", e) for e in first] == [
        getattr(e, "name", e) for e in second
    ]
