#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the /graph serialized-bytes cache + ETag (no store needed).

Bottleneck (2) of the 2026-10-16 board-load qualification: every /graph hit
re-ran ``JsonResponse(payload)`` over ~25 MB (~100 ms pure CPU per poll)
even when the payload was cached. The cache now stores the serialized bytes
and serves them verbatim, plus a body-hash ETag with 304 support.

``_build_graph`` is stubbed and the board is a namespace — these tests pin
the SERVING contract (one build, byte-identical hits, conditional 304),
not the payload shape, so they run with no database at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django.handlers import graph as _graph_mod  # noqa: E402


def _board(**over):
    fields = {
        "store_path": Path("/x/tasks.yaml"),
        "mtime": 1.0,
        "sig": ("gen", "stat"),
    }
    fields.update(over)
    return SimpleNamespace(**fields)


@pytest.fixture
def builds():
    """Count ``_build_graph`` calls; serve a fixed small payload."""
    calls: list = []

    def fake_build(board):
        calls.append(board)
        return {"nodes": [], "hello": "world"}

    # Hand-swap, not monkeypatch (PA-306 forbids the fixture): restore in
    # finally so a failing test cannot leak the stub into the next one.
    old_build = _graph_mod._build_graph
    _graph_mod._build_graph = fake_build
    _graph_mod._graph_cache_reset()
    try:
        yield calls
    finally:
        _graph_mod._build_graph = old_build
        _graph_mod._graph_cache_reset()


def _get(**extra):
    return RequestFactory().get("/graph", **extra)


class TestBytesHit:
    """A second GET serves the cached bytes without rebuilding."""

    def test_second_request_builds_only_once(self, builds):
        # Arrange — prime the cache with one request.
        _graph_mod.handle_graph(_get(), _board())
        # Act — second request against the same board identity.
        _graph_mod.handle_graph(_get(), _board())
        # Assert
        assert len(builds) == 1

    def test_hit_body_matches_original_payload(self, builds):
        # Arrange
        first = _graph_mod.handle_graph(_get(), _board())
        # Act
        second = _graph_mod.handle_graph(_get(), _board())
        # Assert
        assert json.loads(second.content) == {"nodes": [], "hello": "world"}

    def test_hit_body_is_byte_identical(self, builds):
        # Arrange
        first = _graph_mod.handle_graph(_get(), _board())
        # Act
        second = _graph_mod.handle_graph(_get(), _board())
        # Assert
        assert second.content == first.content

    def test_response_content_type_is_json(self, builds):
        # Arrange
        request = _get()
        # Act
        resp = _graph_mod.handle_graph(request, _board())
        # Assert — same wire contract as the JsonResponse this replaced.
        assert resp["Content-Type"] == "application/json"

    def test_response_etag_is_a_weak_validator(self, builds):
        # Arrange
        request = _get()
        # Act
        resp = _graph_mod.handle_graph(request, _board())
        # Assert
        assert resp["ETag"].startswith('W/"')


class TestConditionalGet:
    """A matching If-None-Match answers 304 with no body."""

    def test_matching_etag_status_is_304(self, builds):
        # Arrange
        first = _graph_mod.handle_graph(_get(), _board())
        etag = first["ETag"]
        # Act
        resp = _graph_mod.handle_graph(_get(HTTP_IF_NONE_MATCH=etag), _board())
        # Assert
        assert resp.status_code == 304

    def test_matching_etag_body_is_empty(self, builds):
        # Arrange
        first = _graph_mod.handle_graph(_get(), _board())
        etag = first["ETag"]
        # Act
        resp = _graph_mod.handle_graph(_get(HTTP_IF_NONE_MATCH=etag), _board())
        # Assert
        assert resp.content == b""

    def test_matching_etag_echoes_the_validator(self, builds):
        # Arrange
        first = _graph_mod.handle_graph(_get(), _board())
        etag = first["ETag"]
        # Act
        resp = _graph_mod.handle_graph(_get(HTTP_IF_NONE_MATCH=etag), _board())
        # Assert
        assert resp["ETag"] == etag

    def test_stale_etag_status_is_200(self, builds):
        # Arrange
        _graph_mod.handle_graph(_get(), _board())
        # Act — an ETag from some other content version.
        resp = _graph_mod.handle_graph(
            _get(HTTP_IF_NONE_MATCH='W/"deadbeef"'), _board()
        )
        # Assert
        assert resp.status_code == 200

    def test_stale_etag_body_is_nonempty(self, builds):
        # Arrange
        _graph_mod.handle_graph(_get(), _board())
        # Act — an ETag from some other content version.
        resp = _graph_mod.handle_graph(
            _get(HTTP_IF_NONE_MATCH='W/"deadbeef"'), _board()
        )
        # Assert
        assert len(resp.content) > 0


class TestInvalidation:
    """A moved board identity rebuilds exactly once."""

    def test_changed_sig_rebuilds_exactly_once(self, builds):
        # Arrange — prime under one identity.
        _graph_mod.handle_graph(_get(), _board())
        # Act — same path, new generation.
        _graph_mod.handle_graph(_get(), _board(sig=("gen2", "stat")))
        # Assert
        assert len(builds) == 2

    def test_changed_sig_rebuilt_body_matches_payload(self, builds):
        # Arrange — prime under one identity.
        _graph_mod.handle_graph(_get(), _board())
        # Act — same path, new generation.
        resp = _graph_mod.handle_graph(_get(), _board(sig=("gen2", "stat")))
        # Assert
        assert json.loads(resp.content) == {"nodes": [], "hello": "world"}
