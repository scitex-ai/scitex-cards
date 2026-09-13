#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the PostgreSQL digest replay-storm fix (supersede-on-enqueue).

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store, real
``enqueue`` / ``poll_inbox`` / ``collapse_digests`` against it. Covers:

* ``enqueue(..., supersede=True)`` for the cumulative digest keeps at most ONE
  unseen digest while retaining its predecessors as seen history.
* supersede leaves SEEN digests + OTHER event_types untouched.
* the non-supersede path keeps the ``(type,card,ts,actor)`` dedup unchanged.
"""

from __future__ import annotations

import pytest

from scitex_cards._inbox import enqueue, poll_inbox
from scitex_cards._reminders import DIGEST_CARD_ID, EVENT_DIGEST


def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _enqueue_digest(store, recipient, ts, *, supersede=True):
    return enqueue(
        recipient,
        event_type=EVENT_DIGEST,
        card_id=DIGEST_CARD_ID,
        body=f"digest snapshot @ {ts}",
        actor="notifyd",
        ts=ts,
        supersede=supersede,
        store=store,
    )


def _enqueue_comment(store, ts="2026-07-06T00:00:00Z"):
    """A genuine per-card event — the thing supersede must never touch."""
    return enqueue(
        "u_owner",
        event_type="commented",
        card_id="c1",
        body="bob commented on c1",
        actor="bob",
        ts=ts,
        store=store,
    )


@pytest.fixture()
def superseded_store(tmp_path):
    """Three digest snapshots enqueued with supersede=True, newest last."""
    store = _store(tmp_path)
    _enqueue_digest(store, "u_owner", "2026-07-06T00:00:00Z")
    _enqueue_digest(store, "u_owner", "2026-07-07T00:00:00Z")
    last = _enqueue_digest(store, "u_owner", "2026-07-08T00:00:00Z")
    return {"store": store, "last": last}


@pytest.fixture()
def drained_then_new_digest_store(tmp_path):
    """One digest drained (seen), then a fresh supersede-enqueue on top."""
    store = _store(tmp_path)
    _enqueue_digest(store, "u_owner", "2026-07-06T00:00:00Z")
    # Drain (mark seen) the first digest — it is now history.
    poll_inbox("u_owner", unseen_only=True, mark_seen=True, store=store)
    # A new supersede-enqueue must NOT touch the seen record.
    _enqueue_digest(store, "u_owner", "2026-07-07T00:00:00Z")
    return store


@pytest.fixture()
def mixed_event_store(tmp_path):
    """A per-card `commented` event, then a supersede digest on top."""
    store = _store(tmp_path)
    _enqueue_comment(store)
    _enqueue_digest(store, "u_owner", "2026-07-07T00:00:00Z")
    return store


# --------------------------------------------------------------------------- #
# Change 1 — supersede-on-enqueue                                             #
# --------------------------------------------------------------------------- #
def test_supersede_keeps_only_latest_unseen_digest(superseded_store):
    # Arrange
    store = superseded_store["store"]
    # Act
    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    # Assert
    assert len(unseen) == 1, "at most one pending digest must survive"


def test_supersede_keeps_the_newest_digest_timestamp(superseded_store):
    # Arrange
    store = superseded_store["store"]
    # Act
    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    # Assert — the survivor is the NEWEST snapshot, not an arbitrary one.
    assert unseen[0]["ts"] == "2026-07-08T00:00:00Z"


def test_supersede_keeps_the_last_enqueued_record(superseded_store):
    # Arrange
    store = superseded_store["store"]
    last = superseded_store["last"]
    # Act
    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    # Assert — identity, not just an equal timestamp.
    assert unseen[0]["id"] == last["id"]


def test_supersede_retains_predecessors_as_seen_history(superseded_store):
    # Arrange
    store = superseded_store["store"]
    # Act
    everything = poll_inbox("u_owner", unseen_only=False, store=store)
    # Assert
    assert [r["ts"] for r in everything] == [
        "2026-07-06T00:00:00Z",
        "2026-07-07T00:00:00Z",
        "2026-07-08T00:00:00Z",
    ]


def test_supersede_leaves_the_seen_digest_in_history(drained_then_new_digest_store):
    # Arrange
    store = drained_then_new_digest_store
    # Act
    everything = poll_inbox("u_owner", unseen_only=False, store=store)
    # Assert — the drained digest was not swept away with the unseen ones.
    assert len(everything) == 2


def test_supersede_preserves_seen_digest(drained_then_new_digest_store):
    # Arrange
    store = drained_then_new_digest_store
    # Act
    everything = poll_inbox("u_owner", unseen_only=False, store=store)
    seen = [r for r in everything if r.get("seen")]
    # Assert
    assert len(seen) == 1 and seen[0]["ts"] == "2026-07-06T00:00:00Z"


def test_supersede_leaves_the_new_digest_unseen(drained_then_new_digest_store):
    # Arrange
    store = drained_then_new_digest_store
    # Act
    everything = poll_inbox("u_owner", unseen_only=False, store=store)
    unseen = [r for r in everything if not r.get("seen")]
    # Assert
    assert len(unseen) == 1 and unseen[0]["ts"] == "2026-07-07T00:00:00Z"


def test_supersede_does_not_touch_other_event_types(mixed_event_store):
    # Arrange
    store = mixed_event_store
    # Act
    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    types = sorted(r["event_type"] for r in unseen)
    # Assert
    assert types == ["commented", EVENT_DIGEST]


def test_supersede_leaves_the_per_card_event_intact(mixed_event_store):
    # The commented per-card event survives the digest supersede untouched.
    # Arrange
    store = mixed_event_store
    # Act
    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    commented = [r for r in unseen if r["event_type"] == "commented"]
    # Assert
    assert len(commented) == 1 and commented[0]["card_id"] == "c1"


def test_non_supersede_first_enqueue_returns_a_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    first = _enqueue_comment(store)
    # Assert
    assert first is not None


def test_non_supersede_duplicate_enqueue_returns_none(tmp_path):
    # Same (type, card, ts, actor) re-emit → deduped (returns None, no dup).
    # Arrange
    store = _store(tmp_path)
    _enqueue_comment(store)
    # Act
    dup = _enqueue_comment(store)
    # Assert
    assert dup is None


def test_non_supersede_path_keeps_dedup(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_comment(store)
    _enqueue_comment(store)
    # Act
    everything = poll_inbox("u_owner", unseen_only=False, store=store)
    # Assert — the dedup dropped the re-emit rather than storing it twice.
    assert len(everything) == 1


def test_non_supersede_distinct_ts_kept(tmp_path):
    # Two digests WITHOUT supersede: distinct ts → both kept (old behavior).
    # Arrange
    store = _store(tmp_path)
    _enqueue_digest(store, "u_owner", "2026-07-06T00:00:00Z", supersede=False)
    _enqueue_digest(store, "u_owner", "2026-07-07T00:00:00Z", supersede=False)
    # Act
    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    # Assert
    assert len(unseen) == 2
