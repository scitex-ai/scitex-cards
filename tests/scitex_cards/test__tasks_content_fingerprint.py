#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for ``tasks_content_fingerprint`` — the board's cheap cache key.

Bottleneck (1) of the 2026-10-16 board-load qualification:
``store_generation`` hashes the whole exported document (~2.5 s) merely to
ask "did the board change", and heartbeat tables move it constantly, so the
board cache never hit. The fingerprint answers the same question for the
board's ACTUAL content in ~0.1 s with one small SELECT over ``(id,
revision)`` pairs (every row write moves it via the DB-enforced
``tasks_bump_revision`` trigger — the same invariant the optimistic lock
relies on).

Follows the repo's storage-test convention: ``postgres_dsn`` carves a
throwaway schema (FAILS loudly with no writable PostgreSQL — a skipped
storage test is indistinguishable from a passing one), and all writes go
through the backend verbs, never raw SQL.
"""

from __future__ import annotations

import os
import re

import pytest

from scitex_cards._backend import get_backend
from scitex_cards._db import tasks_content_fingerprint

_FP_RE = re.compile(r"^\d+:[0-9a-f]{32}$")


@pytest.fixture
def store(env, postgres_dsn):
    """Pin this test at a throwaway schema with one card in it."""
    env.set("SCITEX_CARDS_AGENT_ID", "fp-tester")
    path = os.environ["SCITEX_STORE_DSN"]
    backend = get_backend()
    backend.add_task(
        path,
        id="fp-seed",
        title="fingerprint seed",
        status="deferred",
        created_by="fp-tester",
        assignee="fp-tester",
    )
    return path


def _fp(store_path: str) -> str:
    fp = tasks_content_fingerprint()
    assert fp is not None, "fingerprint is fail-soft None on a live store"
    return fp


class TestShape:
    """The token is ``<count>:<md5>`` (or the empty-store token)."""

    def test_shape_is_count_colon_md5_token(self, store):
        # Arrange
        path = store
        # Act
        token = _fp(path)
        # Assert
        assert _FP_RE.match(token)


class TestStability:
    """Back-to-back reads agree — heartbeats excluded by construction."""

    def test_stable_across_reads(self, store):
        # Arrange
        path = store
        # Act
        first = _fp(path)
        second = _fp(path)
        # Assert
        assert first == second


class TestInvalidation:
    """Card writes move the fingerprint; the board cache re-reads."""

    def test_add_task_moves_it(self, store):
        # Arrange
        before = _fp(store)
        # Act — a second card lands.
        get_backend().add_task(
            store,
            id="fp-second",
            title="second card",
            status="deferred",
            created_by="fp-tester",
            assignee="fp-tester",
        )
        # Assert
        assert _fp(store) != before

    def test_comment_moves_it(self, store):
        # Arrange — comments rewrite the card row (payload + revision bump).
        before = _fp(store)
        # Act
        get_backend().comment_task(store, "fp-seed", "a comment", by="fp-tester")
        # Assert
        assert _fp(store) != before
