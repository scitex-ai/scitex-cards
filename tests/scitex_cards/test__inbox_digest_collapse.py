#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the digest replay-storm fix (supersede-on-enqueue + collapse).

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store, real
``enqueue`` / ``poll_inbox`` / ``collapse_digests`` against it. Covers:

* ``enqueue(..., supersede=True)`` for the cumulative digest keeps at most ONE
  unseen digest — a fresh snapshot strictly replaces its unseen predecessors.
* supersede leaves SEEN digests + OTHER event_types untouched.
* the non-supersede path keeps the ``(type,card,ts,actor)`` dedup unchanged.
* ``collapse_digests`` clears an accumulated backlog to the single newest
  unseen digest in one locked pass, marking the rest seen, others untouched.
"""

from __future__ import annotations

import pytest

from scitex_cards._inbox import enqueue, poll_inbox
from scitex_cards._inbox_maint import collapse_digests
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


@pytest.fixture()
def backlog_store(tmp_path):
    """Five accumulated digests (the old buggy non-supersede path) plus one
    genuine per-card event that must survive the collapse untouched."""
    store = _store(tmp_path)
    for day in range(4, 9):
        _enqueue_digest(store, "u_owner", f"2026-07-0{day}T00:00:00Z", supersede=False)
    enqueue(
        "u_owner",
        event_type="reassigned",
        card_id="c9",
        body="c9 reassigned to you",
        actor="alice",
        ts="2026-07-05T12:00:00Z",
        store=store,
    )
    return store


@pytest.fixture()
def three_digest_store(tmp_path):
    """A three-digest backlog for a single recipient."""
    store = _store(tmp_path)
    for day in range(4, 7):
        _enqueue_digest(store, "u_owner", f"2026-07-0{day}T00:00:00Z", supersede=False)
    return store


@pytest.fixture()
def two_recipient_store(tmp_path):
    """Backlogs of three and four digests, for two different recipients."""
    store = _store(tmp_path)
    for day in range(4, 7):
        _enqueue_digest(store, "u_a", f"2026-07-0{day}T00:00:00Z", supersede=False)
    for day in range(4, 8):
        _enqueue_digest(store, "u_b", f"2026-07-0{day}T00:00:00Z", supersede=False)
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


def test_supersede_removes_predecessors_from_history(superseded_store):
    # The two earlier snapshots are GONE (removed, not merely marked seen):
    # the full inbox history holds only the surviving digest.
    # Arrange
    store = superseded_store["store"]
    # Act
    everything = poll_inbox("u_owner", unseen_only=False, store=store)
    # Assert
    assert [r["ts"] for r in everything] == ["2026-07-08T00:00:00Z"]


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


# --------------------------------------------------------------------------- #
# Change 3 — collapse_digests backlog sweep                                   #
# --------------------------------------------------------------------------- #
def test_collapse_digests_counts_the_collapsed_recipient(backlog_store):
    # Arrange
    store = backlog_store
    # Act
    summary = collapse_digests(store=store)
    # Assert
    assert summary["recipients_collapsed"] == 1


def test_collapse_digests_counts_the_digests_marked_seen(backlog_store):
    # Arrange
    store = backlog_store
    # Act
    summary = collapse_digests(store=store)
    # Assert — 5 digests → keep 1, mark the other 4 seen.
    assert summary["digests_marked_seen"] == 4


def test_collapse_digests_clears_backlog(backlog_store):
    # Arrange
    store = backlog_store
    # Act
    collapse_digests(store=store)
    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    unseen_digests = [r for r in unseen if r["event_type"] == EVENT_DIGEST]
    # Assert
    assert len(unseen_digests) == 1


def test_collapse_digests_keeps_the_newest_digest(backlog_store):
    # Arrange
    store = backlog_store
    # Act
    collapse_digests(store=store)
    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    unseen_digests = [r for r in unseen if r["event_type"] == EVENT_DIGEST]
    # Assert — the survivor is the newest snapshot.
    assert unseen_digests[0]["ts"] == "2026-07-08T00:00:00Z"


def test_collapse_digests_leaves_other_events_unseen(backlog_store):
    # The non-digest event is still unseen (untouched).
    # Arrange
    store = backlog_store
    # Act
    collapse_digests(store=store)
    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    # Assert
    assert any(r["event_type"] == "reassigned" for r in unseen)


def test_collapse_digests_deletes_nothing_from_history(backlog_store):
    # Nothing deleted — the full history still holds all 6 records.
    # Arrange
    store = backlog_store
    # Act
    collapse_digests(store=store)
    everything = poll_inbox("u_owner", unseen_only=False, store=store)
    # Assert
    assert len(everything) == 6


def test_collapse_digests_first_pass_collapses_the_backlog(three_digest_store):
    # Arrange
    store = three_digest_store
    # Act
    first = collapse_digests(store=store)
    # Assert
    assert first["recipients_collapsed"] == 1


def test_collapse_digests_idempotent(three_digest_store):
    # Arrange
    store = three_digest_store
    collapse_digests(store=store)
    # Act
    second = collapse_digests(store=store)
    # Assert — a second pass has nothing left to collapse.
    assert second == {"recipients_collapsed": 0, "digests_marked_seen": 0}


def test_collapse_digests_multi_recipient(two_recipient_store):
    # Arrange
    store = two_recipient_store
    # Act
    summary = collapse_digests(store=store)
    # Assert
    assert summary["recipients_collapsed"] == 2


def test_collapse_digests_marks_seen_per_recipient(two_recipient_store):
    # Arrange
    store = two_recipient_store
    # Act
    summary = collapse_digests(store=store)
    # Assert — each recipient keeps exactly one, so 2 + 3 are marked seen.
    assert summary["digests_marked_seen"] == (3 - 1) + (4 - 1)


def test_collapse_digests_leaves_one_digest_for_recipient_a(two_recipient_store):
    # Arrange
    store = two_recipient_store
    # Act
    collapse_digests(store=store)
    remaining = poll_inbox("u_a", store=store)
    # Assert
    assert len(remaining) == 1


def test_collapse_digests_leaves_one_digest_for_recipient_b(two_recipient_store):
    # Arrange
    store = two_recipient_store
    # Act
    collapse_digests(store=store)
    remaining = poll_inbox("u_b", store=store)
    # Assert
    assert len(remaining) == 1
