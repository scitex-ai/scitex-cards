#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read caches behind the GUI ``/dm/*`` endpoints (perf bridge, 2026-07-18).

Measured on the live host before these caches: ``/dm/threads`` = 10.7 s per
request (``list_users`` full-parsed the 8.8 MB store for ONE registered user;
``list_threads`` rescanned every record of 137 threads for unread counts even
with the parse cached). Both hot paths are now memoized on the backing file's
``(mtime_ns, size)`` — the ``services.get_board`` pattern already used by
``_threads._READ_CACHE`` — so a click re-parses only after a real write.

Pinned here with real files and no mocks. Cache EFFECTIVENESS is read from
``_cache_stats`` — the tally production keeps — because a cache that quietly
stops serving returns the right answer every time and only costs more, so the
hit rate is the sole place its failure is visible:

* one parse per file state — a second read with an unchanged file hits cache;
* a write is never SERVED from the cache (a read-modify-write that were would
  be a lost update);
* a WRITE (register / append / mark_read) rolls the key and is visible on the
  very next read — the cache can never mask a store mutation;
* returned structures do not alias the cache — caller mutation cannot poison
  later reads.
"""

from __future__ import annotations

import pytest

from scitex_cards._cache_stats import cache_stats, reset_cache_stats
from scitex_cards import _threads
from scitex_cards._threads import append_message, list_threads, mark_read, thread_key
from scitex_cards import _db_users
from scitex_cards._users import _store_read
from scitex_cards._users._store_read import list_users
from scitex_cards._users._store_write import register_user


@pytest.fixture()
def store(tmp_path):
    """A real, isolated store file + cleared module caches (they are global)."""
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    _store_read._READ_CACHE.clear()
    _db_users._ROW_CACHE.clear()
    _threads._READ_CACHE.clear()
    _threads._SUMMARY_CACHE.clear()
    reset_cache_stats()
    yield str(path)
    _store_read._READ_CACHE.clear()
    _db_users._ROW_CACHE.clear()
    _threads._READ_CACHE.clear()
    _threads._SUMMARY_CACHE.clear()
    reset_cache_stats()


# === users registry read cache =============================================


def test_a_second_user_read_inside_the_ttl_is_served_from_the_cache(store):
    """The cache still collapses repeat reads — now over the DATABASE.

    The PROPERTY is unchanged and still the point: `list_users` sits on
    ordinary card paths and the underlying read costs a ~4.8 ms connection,
    so two calls must not cost two of them.

    Measured through the cache's OWN tally rather than a counter wrapped
    around the loader. That is not just rule compliance: the tally is what
    production reads too, so this test now fails for the same reason an
    operator would notice, and the instrument it exercises is one that ships.
    """
    # Arrange — one registered user, cache warmed by a first read.
    register_user(kind="agent", names=["alice"], store=store)
    list_users(store)
    before = cache_stats(_db_users.CACHE_NAME)

    # Act — a second read inside the same TTL window.
    list_users(store)

    # Assert — that read was SERVED, not performed.
    assert cache_stats(_db_users.CACHE_NAME)["hits"] == before["hits"] + 1


def test_a_second_user_read_inside_the_ttl_costs_no_registry_read(store):
    # Arrange — one registered user, cache warmed by a first read.
    register_user(kind="agent", names=["alice"], store=store)
    list_users(store)
    before = cache_stats(_db_users.CACHE_NAME)

    # Act — a second read inside the same TTL window.
    list_users(store)

    # Assert — the other half of "served": no connection was opened. A hit
    # tally that rose while misses ALSO rose would mean a cache that reports
    # success and reads anyway, which is the failure this pair rules out.
    assert cache_stats(_db_users.CACHE_NAME)["misses"] == before["misses"]


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


def test_a_registry_write_is_never_served_from_the_cache(store):
    """A read-modify-write served from a cache is a LOST UPDATE.

    `load_users_rows_cached` carries this as a warning ("NEVER SERVE A
    READ-MODIFY-WRITE FROM HERE") and the write path obeys it by calling the
    uncached `load_users_rows` under the store lock. Until the cache kept a
    tally that was a comment; it is now a check.

    The old test approached this by counting loader calls and asserting TWO,
    spending ten lines explaining why two is correct and not waste. That
    number conflated the write's own read with the reader's refill, so it
    would also have been 2 if the WRITE had been served from cache and
    something else had missed. Asking the cache directly separates them.
    """
    # Arrange — warm the cache so there is an entry a write could be served.
    register_user(kind="agent", names=["alice"], store=store)
    list_users(store)
    before = cache_stats(_db_users.CACHE_NAME)

    # Act — a write, with a warm entry sitting right there.
    register_user(kind="agent", names=["bob"], store=store)

    # Assert — it took nothing from the cache.
    assert cache_stats(_db_users.CACHE_NAME)["hits"] == before["hits"]


def test_the_read_after_a_registry_write_misses_and_refills(store):
    # Arrange — warm the cache, then note the tallies.
    register_user(kind="agent", names=["alice"], store=store)
    list_users(store)
    before = cache_stats(_db_users.CACHE_NAME)

    # Act — a write retires the entry; then read again.
    register_user(kind="agent", names=["bob"], store=store)
    list_users(store)

    # Assert — the reader paid for a real read, which is the cost of never
    # being served a stale registry. `test_list_users_sees_a_registry_write_
    # on_the_very_next_read` is what that purchase buys.
    assert cache_stats(_db_users.CACHE_NAME)["misses"] == before["misses"] + 1


def test_mutating_a_returned_user_does_not_poison_the_cache(store):
    # Arrange — one cached read.
    register_user(kind="agent", names=["alice"], store=store)
    victim = list_users(store)[0]

    # Act — mutate the returned object's nested list (from_dict aliases it).
    victim.names.append("mallory")

    # Assert — a fresh read from the SAME cache entry is pristine.
    assert list_users(store)[0].names == ["alice"]


# === thread summary cache ==================================================


def test_list_threads_does_not_reparse_while_the_sidecar_is_unchanged(store):
    """The 10.7 s -> cached property: a click re-parses only after a write.

    The old assertion was `parses <= 1`, which a cache that never served
    would also satisfy at zero — an upper bound cannot detect a cache doing
    nothing. Asking for a HIT states the thing the bound was gesturing at.
    """
    # Arrange — a two-message thread, parsed once to fill the cache.
    append_message("operator", "agent-a", "hello", store=store)
    append_message("agent-a", "operator", "hi back", store=store)
    list_threads(store=store)
    before = cache_stats(_threads.CACHE_NAME)

    # Act — a second summary read against the identical file state.
    list_threads(store=store)

    # Assert — the sidecar was not parsed again.
    assert cache_stats(_threads.CACHE_NAME)["misses"] == before["misses"]


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
