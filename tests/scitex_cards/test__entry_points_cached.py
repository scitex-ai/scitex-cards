#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`_iter_entry_points` discovers ONCE per process, not once per card event.

`importlib.metadata.entry_points()` re-reads every installed package's
entry_points.txt (~126 files in a real fleet venv). It runs on EVERY card event
via dispatch_event, so uncached it was the single largest cost in a card write —
sac profiled 2.18s of a 3.24s warm add_task there. This pins the cache so the
cost is paid ONCE per process, not per write.
"""

from __future__ import annotations

import importlib.metadata

import pytest

from scitex_cards._hooks import _plugins


@pytest.fixture(autouse=True)
def _clear_cache():
    # The cache is process-global; isolate each test from the others and from
    # any real discovery that ran at import time.
    _plugins._iter_entry_points.cache_clear()
    yield
    _plugins._iter_entry_points.cache_clear()


def test_entry_points_scanned_only_once_across_many_calls(monkeypatch):
    # Arrange — count how many times the expensive stdlib scan actually runs.
    calls = {"n": 0}
    real = importlib.metadata.entry_points

    def counting_entry_points(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(importlib.metadata, "entry_points", counting_entry_points)

    # Act — call it many times, as a busy fleet writing many cards would.
    for _ in range(50):
        _plugins._iter_entry_points()

    # Assert — the ~126-file scan happened ONCE, not 50 times.
    assert calls["n"] == 1


def test_cache_clear_forces_rediscovery(monkeypatch):
    # Arrange — the escape hatch must work: a live plugin reload / a test can
    # force a fresh scan. Without this, caching would be a one-way door.
    calls = {"n": 0}
    real = importlib.metadata.entry_points

    def counting_entry_points(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(importlib.metadata, "entry_points", counting_entry_points)

    # Act — scan, drop the cache, scan again.
    _plugins._iter_entry_points()
    _plugins._iter_entry_points.cache_clear()
    _plugins._iter_entry_points()

    # Assert
    assert calls["n"] == 2


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
