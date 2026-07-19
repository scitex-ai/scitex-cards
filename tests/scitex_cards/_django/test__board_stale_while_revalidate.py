#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board serves instantly across a store write, refreshing behind it.

WHY THIS EXISTS (operator, 2026-07-18): the board took 30+ seconds and showed
"loading…". Profiled on the live store: ``get_board`` costs **4.6 s** to
rebuild (0.01 s cached) and the cache is invalidated by EVERY store write —
so with a fleet writing every few seconds, nearly every board request paid a
full multi-MB parse. Measured end-to-end that was a 31 s board.

A board is a LIVE VIEW, not a decision read: it self-refreshes and shows a
LIVE badge, so data one refresh-cycle old is invisible to the viewer while a
31-second wait is not. So a stale board is served immediately and the rebuild
runs behind the response.

Pinned here: the stale hit is FAST, the refresh actually LANDS, a refresh
storm cannot start N rebuilds, a failing rebuild never blanks the board, and
the behaviour can be switched off. Real store files, no mocks.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from scitex_cards._django import services


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "tasks.yaml"
    path.write_text(
        "tasks:\n"
        "  - id: a\n    title: a\n    status: in_progress\n"
        "  - id: b\n    title: b\n    status: done\n",
        encoding="utf-8",
    )
    services._board_cache.clear()
    services._refreshing.clear()
    # No per-project lanes in the fixture — keep the unit about the cache.
    monkeypatch.setenv("SCITEX_TODO_LANE_GLOBS", "")
    yield str(path)
    services._board_cache.clear()
    services._refreshing.clear()


def _settle(deadline_s: float = 10.0) -> None:
    """Wait for any background refresh to finish (bounded)."""
    end = time.time() + deadline_s
    while time.time() < end and services._refreshing:
        time.sleep(0.05)


def _bump_mtime(store) -> None:
    """Age the store forward by an UNAMBIGUOUS amount, standing in for a write.

    NOT ``os.utime(path, None)``, which sets the stamp to *now* and merely
    ASSUMES now is later than what the cache holds. The cache compares against
    ``effective_mtime``, and the refresh only kicks when that value MOVES.

    CONFIRMED: the flake is a missed invalidation. ``_load_global_tasks`` was
    never called (``assert 0 == 1``), which is only reachable when the board
    read judged the cache fresh — so ``utime(None)`` did not move
    ``effective_mtime`` on that run.

    NOT CONFIRMED — two mechanisms produce that and this test cannot tell them
    apart, so neither is claimed here:
      * coarse filesystem timestamps (set "now" twice inside one granule and
        the float comes back equal). Measured 0/200 collisions on the dev
        container's fs; CI's filesystem was never measured.
      * ``effective_mtime`` is a ``max()`` over the global store AND the lane
        files. Any lane file stamped later dominates the max, and bumping the
        store to "now" then changes nothing.

    Stepping the stamp explicitly past both is what makes this deterministic;
    it does not depend on which mechanism was at fault. The flake reached
    develop in #494 and turned two unrelated docs-only PRs red before it was
    traced back here.
    """
    bumped = os.stat(store).st_mtime + 5
    os.utime(store, (bumped, bumped))


def _append_card(store, cid: str, status: str = "deferred") -> None:
    with open(store, "a", encoding="utf-8") as fh:
        fh.write(f"  - id: {cid}\n    title: {cid}\n    status: {status}\n")


def _break_the_rebuild(monkeypatch) -> None:
    """Make the background rebuild raise, as an unreadable store would."""

    def _boom(path):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(services, "_load_global_tasks", _boom)


def _rewrite_same_length(store, before) -> None:
    """Rewrite atomically with the SAME byte length and the SAME timestamp.

    ``in_progress`` -> ``done`` is not equal-length, so swap a title instead:
    ``"a"`` -> ``"A"`` keeps the length. Every write to this store goes through
    atomic ``os.replace``, which allocates a new inode, so only the inode moves.
    """
    text = Path(store).read_text(encoding="utf-8").replace("title: a\n", "title: A\n")
    tmp = Path(str(store) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, store)
    os.utime(store, ns=(before.st_atime_ns, before.st_mtime_ns))


def test_a_warm_stale_board_holds_every_seeded_card(store):
    # Arrange
    store_path = store
    # Act
    first = services.get_board(store_path, allow_stale=True)
    # Assert
    assert len(first.tasks) == 2


def test_a_write_does_not_make_the_next_read_pay_a_rebuild(store):
    # Arrange
    # Warm the cache, then let a writer roll the store's mtime.
    services.get_board(store, allow_stale=True)
    _bump_mtime(store)
    # Act
    t0 = time.time()
    services.get_board(store, allow_stale=True)
    elapsed = time.time() - t0
    _settle()
    # Assert — served from cache, not rebuilt: the point of the change.
    assert elapsed < 0.5


def test_a_stale_read_after_a_write_still_returns_a_full_board(store):
    # Arrange
    services.get_board(store, allow_stale=True)
    _bump_mtime(store)
    # Act
    served = services.get_board(store, allow_stale=True)
    _settle()
    # Assert
    assert len(served.tasks) == 2


def test_the_background_refresh_actually_lands(store):
    # Arrange
    services.get_board(store, allow_stale=True)
    # Act — append a card, then read (gets the stale board) and let it settle.
    _append_card(store, "c")
    services.get_board(store, allow_stale=True)
    _settle()
    # Assert — the refresh picked the new card up for the NEXT reader.
    assert {t["id"] for t in services.get_board(store).tasks} == {"a", "b", "c"}


def test_a_poll_storm_starts_only_one_refresh(store, monkeypatch):
    # Arrange
    # Make the rebuild slow enough that polls overlap it.
    services.get_board(store, allow_stale=True)
    calls = {"n": 0}
    real = services._load_global_tasks

    def _slow(path):
        calls["n"] += 1
        time.sleep(0.4)
        return real(path)

    monkeypatch.setattr(services, "_load_global_tasks", _slow)
    # Act — ten rapid reads against a changed store (the operator's browser
    # polling while a rebuild is in flight).
    _bump_mtime(store)
    for _ in range(10):
        services.get_board(store, allow_stale=True)
    _settle()
    # Assert — one rebuild, not ten.
    assert calls["n"] == 1


def test_a_failing_refresh_keeps_serving_the_previous_board(store, monkeypatch):
    # Arrange
    good = services.get_board(store, allow_stale=True)
    _break_the_rebuild(monkeypatch)
    # Act — the store changes and the background rebuild blows up.
    _bump_mtime(store)
    served = services.get_board(store, allow_stale=True)
    _settle()
    # Assert — the operator still has a board; it is never blanked.
    assert len(served.tasks) == len(good.tasks)


def test_a_further_stale_read_after_a_failed_refresh_still_serves(store, monkeypatch):
    # Arrange
    services.get_board(store, allow_stale=True)
    _break_the_rebuild(monkeypatch)
    _bump_mtime(store)
    services.get_board(store, allow_stale=True)
    _settle()
    # Act
    served_again = services.get_board(store, allow_stale=True)
    # Assert — a further stale read keeps serving the last good board rather
    # than surfacing the failure. (A STRICT read would rightly raise here: the
    # store really is unreadable, and fail-loud is correct when the caller
    # cannot tolerate staleness.)
    assert len(served_again.tasks) == 2


def test_a_strict_caller_is_never_served_stale(store):
    """READ-YOUR-OWN-WRITES. The default must stay blocking.

    This pins a REAL regression: the first cut of stale-while-revalidate
    applied to every ``get_board`` caller, which broke the chat POST — it
    writes a message and reads it straight back through the board, so the
    operator's just-posted comment vanished until the next refresh. The
    endpoint that writes must see its write; only opted-in read-only views
    may lag.
    """
    # Arrange
    # Warm the cache.
    services.get_board(store)
    # Act — a write lands, then a DEFAULT (strict) read.
    _append_card(store, "z", status="in_progress")
    served = services.get_board(store)
    # Assert — the new card is there: no staleness without opting in.
    assert {t["id"] for t in served.tasks} == {"a", "b", "z"}


def test_rewinding_the_stamp_really_hides_the_write_from_the_clock(store):
    """The trap this file's granularity pins depend on is actually armed."""
    # Arrange
    services.get_board(store)
    before = os.stat(store)
    # Act — a real write, then rewind the clock so ONLY the size betrays it.
    _append_card(store, "z", status="in_progress")
    os.utime(store, ns=(before.st_atime_ns, before.st_mtime_ns))
    # Assert
    assert os.stat(store).st_mtime_ns == before.st_mtime_ns


def test_a_write_inside_one_timestamp_granule_is_still_seen(store):
    """READ-YOUR-OWN-WRITES survives a filesystem whose clock did not move.

    THE REAL BUG, and it was a product bug, not a test bug. The board cache
    compared ``stat().st_mtime`` — a float of SECONDS. On a filesystem with
    1-second timestamp granularity a write and the stat that follows it report
    the SAME mtime, so a STRICT read answered from the pre-write cache and the
    caller silently did not see its own write. The chat POST depends on
    exactly this guarantee: it writes a message and reads it straight back.

    CI found it before a user did — test_a_strict_caller_is_never_served_stale
    failed on py3.13 with the appended card simply missing. This test pins the
    condition deterministically on ANY filesystem by forcing the stamp back to
    its pre-write value, which is what a coarse-granularity fs does for free.
    """
    # Arrange
    # Warm the cache and capture the exact stamp it recorded.
    services.get_board(store)
    before = os.stat(store)
    # Act — a real write, then rewind the clock so ONLY the size betrays it.
    _append_card(store, "z", status="in_progress")
    os.utime(store, ns=(before.st_atime_ns, before.st_mtime_ns))
    served = services.get_board(store)
    # Assert — the write is visible despite an unchanged timestamp.
    assert {t["id"] for t in served.tasks} == {"a", "b", "z"}


def test_a_same_length_edit_leaves_the_file_size_unchanged(store):
    """Half of the trap the inode pin needs: the size must not betray the edit."""
    # Arrange
    services.get_board(store)
    before = os.stat(store)
    # Act
    _rewrite_same_length(store, before)
    # Assert
    assert os.stat(store).st_size == before.st_size


def test_a_same_length_edit_leaves_the_mtime_unchanged(store):
    """The other half of the trap: the clock must not betray the edit either."""
    # Arrange
    services.get_board(store)
    before = os.stat(store)
    # Act
    _rewrite_same_length(store, before)
    # Assert
    assert os.stat(store).st_mtime_ns == before.st_mtime_ns


def test_a_same_length_edit_inside_one_granule_is_still_seen(store):
    """The case SIZE alone does not catch — and the reason inode is in the key.

    ``st_mtime_ns`` is nanosecond-TYPED, not nanosecond-ACCURATE: on a
    filesystem stamping whole seconds the sub-second digits are zero. So a
    (mtime_ns, size) key still collides when an edit changes neither — a
    priority ``1`` -> ``2``, or a status swapped for one of equal length.

    Every write to this store goes through atomic ``os.replace``, which
    allocates a new inode, so the inode moves even when clock and length do
    not. Written as a MUTATION test: it must go RED without ``st_ino`` in the
    key, which a write-sleep-write scenario would not (the sleep alone would
    make it pass under the buggy key).
    """
    # Arrange
    services.get_board(store)
    before = os.stat(store)
    # Act
    _rewrite_same_length(store, before)
    served = services.get_board(store)
    # Assert — the edit is visible even though only the inode moved.
    assert {t["title"] for t in served.tasks} == {"A", "b"}


def test_the_first_strict_board_read_sees_both_seeded_cards(store):
    """The baseline the same-length mutation pin is measured against."""
    # Arrange
    store_path = store
    # Act
    first = services.get_board(store_path)
    # Assert
    assert {t["id"] for t in first.tasks} == {"a", "b"}


def test_stale_while_revalidate_can_be_switched_off(store, monkeypatch):
    # Arrange
    services.get_board(store, allow_stale=True)
    monkeypatch.setenv("SCITEX_CARDS_BOARD_SWR", "0")
    # Act — with SWR off, a changed store rebuilds synchronously.
    _append_card(store, "d")
    served = services.get_board(store, allow_stale=True)
    # Assert — the caller sees the new card immediately (blocking rebuild).
    assert {t["id"] for t in served.tasks} == {"a", "b", "d"}


# EOF
