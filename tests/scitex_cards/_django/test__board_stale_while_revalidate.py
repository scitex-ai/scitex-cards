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
from conftest import seed_db_from_doc

from scitex_cards._django import services
from scitex_cards._model import load_tasks


@pytest.fixture()
def store(env):
    # SQLite cutover: card DATA lives in the canonical DB (a path-independent
    # read), so the two seeded cards are put THERE. But the board cache — the
    # subject of this whole file — still keys HALF its invalidation on the
    # resolved store PATH's stat (mtime_ns, size, inode via ``_stat_sig``) and
    # opens that path through ``load_groups``. So the store is the PINNED
    # identity path with a real marker FILE behind it; the helpers below mutate
    # the DB for card content AND move the marker file's stat so the cache
    # observes the change, exactly as a write to the old single YAML store
    # moved both at once.
    #
    # THIS FIXTURE CREATES THAT FILE ITSELF, and that is the point. It used to
    # arrive from an autouse fixture in the _django conftest that created it for
    # EVERY test in the package — which is how the 2026-07-29 blank-board outage
    # went untested: the board gated its card read on this file's existence, and
    # the harness manufactured it before each test, so production's actual
    # shape (no such file) was never exercised. The stat half of the cache key
    # is genuinely THIS file's subject, so this file owns the file. Nothing
    # else has to.
    seed_db_from_doc(
        {
            "tasks": [
                {"id": "a", "title": "a", "status": "in_progress"},
                {"id": "b", "title": "b", "status": "done"},
            ]
        },
        os.environ["SCITEX_CARDS_DB"],
    )
    path = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if not Path(path).exists():
        Path(path).write_text("", encoding="utf-8")
    services._board_cache.clear()
    services._refreshing.clear()
    # No per-project lanes in the fixture — keep the unit about the cache.
    env.set("SCITEX_CARDS_LANE_GLOBS", "")
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
    """Add one card, moving BOTH halves a board read consults.

    Card data lives in the canonical DB now, so the new card is seeded there
    (a rebuild reads it back). The board's cache invalidation keys on the
    marker file's stat, so the file is also grown — by a comment line, which
    moves its size + mtime without turning it into a non-mapping that the
    group loader (``load_groups`` -> ``safe_load``) would choke on.
    """
    tasks = list(load_tasks(store))
    tasks.append({"id": cid, "title": cid, "status": status})
    seed_db_from_doc({"tasks": tasks}, os.environ["SCITEX_CARDS_DB"])
    with open(store, "a", encoding="utf-8") as fh:
        fh.write(f"# {cid}\n")


def _break_the_rebuild(store) -> None:
    """Break the REBUILD for real, while the store stays readable.

    The old version replaced `services._load_global_tasks` with a function
    raising `RuntimeError("store unreadable")`. Two things were wrong with it.

    First, it chose the exception. The guard was only ever proved against the
    one type the test invented.

    Second — and this is why the obvious replacement is WRONG — an unreadable
    *database* is not the condition this test is about. Corrupting the DB makes
    `store_generation()` raise at services.py:411, BEFORE the cache is even
    consulted, so no cached board can be served. That is deliberate and
    documented in `get_board`: "a refusal is recoverable and visible, a
    believable empty board is neither" — an unreadable database is MEANT to
    reach the operator as a 500. Asserting stale-serving there would have been
    asserting against the module's stated design. (Measured: it raises
    `sqlite3.DatabaseError: file is not a database` straight out of the view.)

    The real seam is narrower. `_load_global_tasks` ALSO parses the sidecar
    through `load_groups` -> `safe_load`, while `store_generation` reads the
    database. Malformed YAML in the sidecar therefore breaks the rebuild with
    the store still perfectly readable — exactly "the refresh failed, serve the
    board you have". `_append_card` above only ever appends `#` comments for
    this same reason: it is documented there that a non-mapping would choke the
    group loader.
    """
    Path(store).write_text("{unclosed: [\n", encoding="utf-8")


def _rewrite_same_length(store, before) -> None:
    """Mutate a card so ONLY the inode betrays it — same length, same timestamp.

    Card side: title ``"a"`` -> ``"A"`` is an equal-length edit, applied to the
    DB where card data now lives. File side: the marker file is rewritten
    through atomic ``os.replace``, which allocates a new inode, then its
    original length and stamp are restored — so neither ``st_size`` nor
    ``st_mtime_ns`` moves and only ``st_ino`` does. That is the case
    ``(mtime_ns, size)`` alone cannot catch, which is why the inode is in the
    cache key.
    """
    tasks = list(load_tasks(store))
    for task in tasks:
        if task.get("id") == "a":
            task["title"] = "A"
    seed_db_from_doc({"tasks": tasks}, os.environ["SCITEX_CARDS_DB"])
    text = Path(store).read_text(encoding="utf-8")
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


def test_a_poll_storm_starts_only_one_refresh(store):
    # Arrange — a rebuild slow enough that the polls overlap it, passed in as
    # `load` rather than written over the module attribute. The delay is the
    # POINT of the scenario, not a stand-in for one: without an in-flight
    # rebuild there is no storm to guard against, and nothing outside this
    # process can make a database read take 0.4 s on demand.
    services.get_board(store, allow_stale=True)
    calls = {"n": 0}
    real = services._load_global_tasks

    def _slow(path):
        calls["n"] += 1
        time.sleep(0.4)
        return real(path)

    # Act — ten rapid reads against a changed store (the operator's browser
    # polling while a rebuild is in flight).
    _bump_mtime(store)
    for _ in range(10):
        services.get_board(store, allow_stale=True, load=_slow)
    _settle()
    # Assert — one rebuild, not ten.
    assert calls["n"] == 1


def test_a_failing_refresh_keeps_serving_the_previous_board(store):
    # Arrange
    good = services.get_board(store, allow_stale=True)
    _break_the_rebuild(store)
    # Act — the store changes and the background rebuild blows up.
    _bump_mtime(store)
    served = services.get_board(store, allow_stale=True)
    _settle()
    # Assert — the operator still has a board; it is never blanked.
    assert len(served.tasks) == len(good.tasks)


def test_a_further_stale_read_after_a_failed_refresh_still_serves(store):
    # Arrange
    services.get_board(store, allow_stale=True)
    _break_the_rebuild(store)
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


def test_stale_while_revalidate_can_be_switched_off(store, env):
    # Arrange
    services.get_board(store, allow_stale=True)
    env.set("SCITEX_CARDS_BOARD_SWR", "0")
    # Act — with SWR off, a changed store rebuilds synchronously.
    _append_card(store, "d")
    served = services.get_board(store, allow_stale=True)
    # Assert — the caller sees the new card immediately (blocking rebuild).
    assert {t["id"] for t in served.tasks} == {"a", "b", "d"}


# EOF
