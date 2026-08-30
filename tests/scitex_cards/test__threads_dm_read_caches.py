#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read caches behind the GUI ``/dm/*`` endpoints (perf bridge, 2026-07-18).

Measured on the live host before these caches: ``/dm/threads`` = 10.7 s per
request (``list_users`` full-parsed the 8.8 MB store for ONE registered user;
``list_threads`` rescanned every record of 137 threads for unread counts even
with the parse cached). Both hot paths are now memoized on the backing file's
``(mtime_ns, size)`` — the ``services.get_board`` pattern already used by
``_threads._READ_CACHE`` — so a click re-parses only after a real write.

Pinned here with real files and no mocks. The instrument is CACHE POISONING:
warm the cache, overwrite the cached value with a sentinel no store contains,
then read again. A result carrying the sentinel was served from cache; one
without it went to the store. That answers "did this read reach the database"
directly, where the call counters this file used to install answered only "was
this attribute called" — a weaker question, and one this file had already been
bitten by (a loader bound at import time was invisible to the counter, so the
expected count silently changed meaning).

* one parse per file state — a second read with an unchanged file hits cache;
* a WRITE (register / append / mark_read) rolls the key and is visible on the
  very next read — the cache can never mask a store mutation;
* returned structures do not alias the cache — caller mutation cannot poison
  later reads.
"""

from __future__ import annotations

import pytest

from scitex_cards import _threads
from scitex_cards._threads import append_message, list_threads, mark_read, thread_key
from scitex_cards import _db_users
from scitex_cards._users import _store_read
from scitex_cards._users._store_read import list_users
from scitex_cards._users._store_write import register_user


@pytest.fixture()
def store(new_store):
    """A real, isolated store + cleared module caches (they are global).

    Was ``tmp_path / "tasks.yaml"``; a path names no store now and is refused
    at the door. The cache clearing is the point of the fixture and is
    unchanged — those caches are process-global, so a hermetic test cannot
    depend on the luck of a fresh key.
    """
    path = new_store()
    _store_read._READ_CACHE.clear()
    _db_users._ROW_CACHE.clear()
    _threads._READ_CACHE.clear()
    _threads._SUMMARY_CACHE.clear()
    yield path
    _store_read._READ_CACHE.clear()
    _db_users._ROW_CACHE.clear()
    _threads._READ_CACHE.clear()
    _threads._SUMMARY_CACHE.clear()


#: A name no store contains, so its presence in a result can ONLY come from
#: the cache. Poisoning is what replaced the call counters here — see below.
SENTINEL = "poisoned-from-cache"

#: Same idea for the thread summary: a count no real thread has.
SENTINEL_COUNT = 9999


def _poison_cached_user_names() -> None:
    """Rewrite every CACHED registry row's names to :data:`SENTINEL`.

    WHY POISON RATHER THAN COUNT. A counter answers "was this function called",
    and only through the ONE module attribute it was installed on — this very
    file records the counter missing the write path's own read for exactly that
    reason (``_store_write`` had bound the loader at import time). Poisoning
    answers the question actually being asked, "did this read reach the
    database", and it cannot be fooled by how the loader is imported: the
    sentinel exists ONLY in the cache, so a result carrying it was served from
    cache and a result without it was not.

    The fixture above already clears these caches directly, so reaching into
    them is this file's established idiom, not a new liberty.
    """
    for key, (ts, rows) in list(_db_users._ROW_CACHE.items()):
        poisoned = [dict(row) for row in rows]
        for row in poisoned:
            row["names"] = [SENTINEL]
        _db_users._ROW_CACHE[key] = (ts, poisoned)


def _poison_cached_thread_counts() -> None:
    """Rewrite every CACHED thread summary's message count to a sentinel."""
    for key, (mtime, size, summary) in list(_threads._SUMMARY_CACHE.items()):
        poisoned = {k: dict(v) for k, v in summary.items()}
        for entry in poisoned.values():
            entry["count"] = SENTINEL_COUNT
        _threads._SUMMARY_CACHE[key] = (mtime, size, poisoned)


# === users registry read cache =============================================


def test_a_second_user_read_inside_the_ttl_is_served_from_cache(store):
    """The cache still collapses repeat reads — now over the DATABASE.

    This counted `_load_users_section`, the YAML section parser, which the
    registry no longer reaches. The PROPERTY is unchanged and still the point:
    `list_users` sits on ordinary card paths, and the underlying read costs a
    ~4.8 ms connection, so two calls must not cost two of them.
    """
    # Arrange — one registered user, one read to warm the cache, then poison
    # the cached rows so a cached answer becomes recognisable on sight.
    register_user(kind="agent", names=["alice"], store=store)
    list_users(store)
    _poison_cached_user_names()

    # Act — a second read inside the same TTL window.
    names = list_users(store)[0].names

    # Assert — the sentinel exists ONLY in the cache and was never written to
    # the database, so getting it back proves this read never went there.
    assert names == [SENTINEL]


def test_two_cached_user_reads_return_identical_content(store):
    # Arrange — one registered user.
    register_user(kind="agent", names=["alice"], store=store)

    # Act — two reads against the identical file state.
    first = list_users(store)
    second = list_users(store)

    # Assert — the cache serves the same rows, not a truncated second answer.
    assert [u.id for u in first] == [u.id for u in second]


def test_the_warm_user_cache_starts_with_only_the_first_registration(store):
    # Arrange — warm the cache with one user.
    register_user(kind="agent", names=["alice"], store=store)

    # Act
    names = [u.names[0] for u in list_users(store)]

    # Assert — the premise for the write-visibility tests below.
    assert names == ["alice"]


def test_list_users_sees_a_registry_write_on_the_very_next_read(store):
    # Arrange — warm the cache with one user.
    register_user(kind="agent", names=["alice"], store=store)
    list_users(store)

    # Act — a write rolls the file's (mtime, size); then read again.
    register_user(kind="agent", names=["bob"], store=store)
    names = sorted(u.names[0] for u in list_users(store))

    # Assert — the new row is visible immediately.
    assert names == ["alice", "bob"]


def test_a_registry_write_retires_the_entry_and_is_never_served_a_stale_one(
    store,
):
    """One assertion covering BOTH halves of the write path, because the same
    sentinel answers both questions.

    The write performs its OWN uncached read under the store lock — a
    read-modify-write must never be served from a cache — and the following
    read must then refill from the database. If the write had read the poisoned
    cache instead, it would have written the sentinel back as alice's name and
    it would still be here. If the read had been served the poisoned entry, the
    sentinel would be here too. Seeing the real names is the proof that neither
    happened.

    The old form asserted a call count of two and needed a paragraph explaining
    why two was not a regression. This asks about the data instead.
    """
    # Arrange — warm the cache, then poison it.
    register_user(kind="agent", names=["alice"], store=store)
    list_users(store)
    _poison_cached_user_names()

    # Act — a write, then a read.
    register_user(kind="agent", names=["bob"], store=store)
    names = sorted(name for user in list_users(store) for name in user.names)

    # Assert
    assert names == ["alice", "bob"]


def test_mutating_a_returned_user_does_not_poison_the_cache(store):
    # Arrange — one cached read.
    register_user(kind="agent", names=["alice"], store=store)
    victim = list_users(store)[0]

    # Act — mutate the returned object's nested list (from_dict aliases it).
    victim.names.append("mallory")

    # Assert — a fresh read from the SAME cache entry is pristine.
    assert list_users(store)[0].names == ["alice"]


# === thread summary cache ==================================================


def test_list_threads_does_not_rescan_while_the_sidecar_is_unchanged(store):
    # Arrange — a two-message thread, one read to warm the summary cache, then
    # poison the cached counts.
    append_message("operator", "agent-a", "hello", store=store)
    append_message("agent-a", "operator", "hi back", store=store)
    list_threads(store=store)
    _poison_cached_thread_counts()

    # Act — a second read against the identical sidecar state.
    key = thread_key("operator", "agent-a")
    count = list_threads(store=store)[key]["count"]

    # Assert — the sentinel count is only in the cache, so the second read did
    # not rescan the sidecar. The old form allowed `<= 1` parses, which is also
    # satisfied by ZERO — this cannot be.
    assert count == SENTINEL_COUNT


def test_two_cached_thread_reads_report_the_same_message_count(store):
    # Arrange — a two-message thread.
    append_message("operator", "agent-a", "hello", store=store)
    append_message("agent-a", "operator", "hi back", store=store)
    key = thread_key("operator", "agent-a")

    # Act — two summary reads against the identical file state.
    first = list_threads(store=store)
    second = list_threads(store=store)

    # Assert
    assert first[key]["count"] == second[key]["count"] == 2


def test_the_warm_thread_summary_starts_at_one_message(store):
    # Arrange — warm the summary cache with a single message.
    append_message("operator", "agent-a", "hello", store=store)
    key = thread_key("operator", "agent-a")

    # Act
    summary = list_threads(store=store)[key]

    # Assert — the premise for the write-visibility tests below.
    assert summary["count"] == 1


def _append_a_second_message_and_reread(store):
    """Warm the summary cache, append another message, return the fresh summary."""
    append_message("operator", "agent-a", "hello", store=store)
    key = thread_key("operator", "agent-a")
    list_threads(store=store)
    append_message("operator", "agent-a", "again", store=store)
    return list_threads(store=store)[key]


def test_list_threads_reflects_a_new_message_in_the_count(store):
    # Arrange — warm the summary cache.
    # Act — append (a write) then re-read.
    summary = _append_a_second_message_and_reread(store)

    # Assert — the write invalidated the summary.
    assert summary["count"] == 2


def test_list_threads_reflects_a_new_message_in_the_unread_count(store):
    # Arrange — warm the summary cache.
    # Act — append (a write) then re-read.
    summary = _append_a_second_message_and_reread(store)

    # Assert — unread counts the recipient.
    assert summary["unread"]["agent-a"] == 2


def test_list_threads_reflects_a_new_message_in_the_last_body(store):
    # Arrange — warm the summary cache.
    # Act — append (a write) then re-read.
    summary = _append_a_second_message_and_reread(store)

    # Assert
    assert summary["last"]["body"] == "again"


def test_the_warm_thread_summary_starts_with_one_unread(store):
    # Arrange — one unread message.
    append_message("operator", "agent-a", "hello", store=store)
    key = thread_key("operator", "agent-a")

    # Act
    unread = list_threads(store=store)[key]["unread"]["agent-a"]

    # Assert — the premise for the mark_read test below.
    assert unread == 1


def test_mark_read_reports_how_many_messages_it_flipped(store):
    # Arrange — one unread message, summary cached showing unread=1.
    append_message("operator", "agent-a", "hello", store=store)
    key = thread_key("operator", "agent-a")
    list_threads(store=store)

    # Act — the recipient acks.
    flipped = mark_read(key, "agent-a", store=store)

    # Assert
    assert flipped == 1


def test_list_threads_reflects_mark_read_on_the_very_next_read(store):
    # Arrange — one unread message, summary cached showing unread=1.
    append_message("operator", "agent-a", "hello", store=store)
    key = thread_key("operator", "agent-a")
    list_threads(store=store)

    # Act — the recipient acks; then re-read the summary.
    mark_read(key, "agent-a", store=store)
    unread = list_threads(store=store)[key]["unread"]["agent-a"]

    # Assert — the GUI badge source drops to zero immediately; the cache
    # cannot serve a stale unread count across the flip's write.
    assert unread == 0


def _tamper_with_a_returned_summary(store):
    """Cache a summary, mutate everything mutable in it, return a fresh read."""
    append_message("operator", "agent-a", "hello", store=store)
    key = thread_key("operator", "agent-a")
    victim = list_threads(store=store)[key]
    victim["unread"]["agent-a"] = 999
    victim["last"]["body"] = "tampered"
    return list_threads(store=store)[key]


def test_mutating_a_returned_summarys_unread_does_not_poison_the_cache(store):
    # Arrange — a cached summary.
    # Act — mutate everything mutable the caller received.
    clean = _tamper_with_a_returned_summary(store)

    # Assert — a fresh read from the SAME cache entry is pristine.
    assert clean["unread"]["agent-a"] == 1


def test_mutating_a_returned_summarys_last_does_not_poison_the_cache(store):
    # Arrange — a cached summary.
    # Act — mutate everything mutable the caller received.
    clean = _tamper_with_a_returned_summary(store)

    # Assert — a fresh read from the SAME cache entry is pristine.
    assert clean["last"]["body"] == "hello"


# EOF
