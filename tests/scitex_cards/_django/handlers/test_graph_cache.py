#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the /graph payload mtime-keyed cache (PR-A of Stage 2 plan,
lead a2a `e5243003` 2026-06-12). The cache piggybacks on
``BoardState.mtime`` so any write that rolls the YAML's mtime
invalidates the entry naturally — no manual invalidation needed.

Real ``RequestFactory`` GETs against a tmp ``tasks.yaml``; no mocks
(STX-NM / PA-306). The cache + reset hook are exercised explicitly to
keep the assertions deterministic across tests.
"""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers import graph as _graph_mod  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402


_STORE_TEXT = (
    "tasks:\n"
    "  - id: a\n"
    "    title: A\n"
    "    status: pending\n"
    "    agent: proj-alpha\n"
    "    project: alpha\n"
)


@pytest.fixture
def store(tmp_path, postgres_dsn):
    # ``postgres_dsn`` is requested but is not the seed target. With no
    # writable PostgreSQL the fleet guard clears ``SCITEX_STORE_DSN`` from
    # the environment, so the ``?store=`` reads below would resolve
    # AMBIENTLY to the live board — a silent production read, not a bare
    # ``KeyError``. The fixture fails first, LOUDLY, with the reason and
    # the remedy. (Same guard as the other ``store`` fixtures in _django/.)
    path = tmp_path / "tasks.yaml"
    path.write_text(_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    _graph_mod._graph_cache_reset()
    yield str(path)
    _reset_cache()
    _graph_mod._graph_cache_reset()


def _graph(store_path: str):
    request = RequestFactory().get(f"/graph?store={store_path}")
    return views.api_dispatch(request, "graph")


class TestCacheHit:
    """A second GET against an unchanged store reuses the cached payload."""

    def test_second_request_hits_cache(self, store):
        # Arrange — prime the cache with one request.
        _graph(store)
        # Act — second request.
        _graph(store)
        # Assert — exactly one entry, keyed on (path, mtime).
        assert len(_graph_mod._GRAPH_PAYLOAD_CACHE) == 1

    def test_cache_payload_is_returned_unchanged(self, store):
        # Arrange
        resp_a = _graph(store)
        # Act — second call must produce the SAME JSON (byte-equal).
        resp_b = _graph(store)
        # Assert
        assert resp_a.content == resp_b.content


class TestCacheInvalidation:
    """When the store's mtime advances, the cache key changes and a
    fresh build runs."""

    def test_mtime_change_produces_new_cache_entry(self, store):
        # Arrange — prime the cache.
        _graph(store)
        # Act — bump the mtime by re-writing the file with a new task.
        time.sleep(0.02)  # ensure mtime ticks past the 1ms resolution
        with open(store, "w", encoding="utf-8") as h:
            h.write(_STORE_TEXT + "  - {id: b, title: B, status: pending}\n")
        # Ensure mtime actually moves (some filesystems coarsen).
        new_mtime = time.time()
        os.utime(store, (new_mtime, new_mtime))
        _reset_cache()  # drop the BoardState's mtime cache too
        _graph(store)
        # Assert — the cache now has at most one valid entry under the
        # NEW mtime (the prior entry may still be present until GC; this
        # is fine — what matters is the payload returned is fresh).
        keys = list(_graph_mod._GRAPH_PAYLOAD_CACHE.keys())
        # The fresh entry's mtime is the just-written one.
        assert any(k[1] >= new_mtime - 0.5 for k in keys)


class TestCacheReset:
    """The test hook clears the cache so per-test arrange is clean."""

    def test_reset_drops_all_entries(self, store):
        # Arrange
        _graph(store)
        # Act
        _graph_mod._graph_cache_reset()
        # Assert
        assert len(_graph_mod._GRAPH_PAYLOAD_CACHE) == 0
