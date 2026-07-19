#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the operator↔agent DM thread store (:mod:`scitex_cards._threads`).

Covers the scitex-dev DM convention v1 contract:

  - append → get_thread round-trip with the canonical record shape
    ``{id, thread, from, to, body, ts, read}``.
  - sorted thread key: ``thread_key(a, b) == thread_key(b, a)``.
  - STORE ISOLATION: threads live in the ``threads.yaml`` SIDECAR next to
    the tasks store, never inside ``tasks.yaml``, with their own lockfile.
  - crash-safe write: the sidecar reparses cleanly after writes.
  - mark_read (all-for-reader and id-scoped) + list_threads unread counts.
  - dm-dispatch: append_message enqueues an ``event_type="dm"`` record into
    the recipient's EXISTING pull-inbox in the real tasks.yaml store.

Real tmp_path stores throughout; no mocks (STX-NM / PA-306). AAA pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scitex_cards import _threads
from scitex_cards._inbox import poll_inbox
from scitex_cards._threads import (
    append_message,
    get_thread,
    list_threads,
    mark_read,
    thread_key,
    threads_path,
)
from scitex_cards._yaml import safe_load


@pytest.fixture(autouse=True)
def _clear_read_cache():
    """Keep the module-level read cache from leaking across tests.

    Entries are keyed by store path and each test gets its own tmp_path, so
    collisions are unlikely — but the cache is process-global state and a
    hermetic suite must not depend on that luck.
    """
    _threads._READ_CACHE.clear()
    yield
    _threads._READ_CACHE.clear()


@pytest.fixture()
def store(tmp_path: Path, env) -> Path:
    """A real tmp tasks.yaml store (threads sidecar lands next to it).

    Git autocommit is disabled so the inbox write path stays fast and
    deterministic under test.
    """
    env.set("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "0")
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return path


# === thread key ============================================================


def test_thread_key_sorts_peers_lexicographically():
    # Arrange
    peers = ("zeta", "alpha")
    # Act
    key = thread_key(*peers)
    # Assert
    assert key == "dm:alpha::zeta"


def test_thread_key_is_direction_agnostic():
    # Arrange
    first, second = "operator", "agent-x"
    # Act
    forward, reverse = thread_key(first, second), thread_key(second, first)
    # Assert
    assert forward == reverse


def test_peers_of_inverts_thread_key():
    # Arrange
    key = thread_key("operator", "agent-x")
    # Act
    peers = _threads.peers_of(key)
    # Assert
    assert peers == ("agent-x", "operator")


# === append / get round-trip ===============================================


#: The one DM every round-trip test below inspects. Its canonical record
#: shape — {id, thread, from, to, body, ts, read} — is the scitex-dev DM
#: convention v1 contract, so each field gets its own failing test rather
#: than hiding behind the first assertion that trips.
DM_SENDER, DM_RECIPIENT, DM_BODY = "operator", "agent-x", "hello there"


@pytest.fixture()
def sent_message(store):
    """One appended DM, returned as ``append_message`` handed it back."""
    return append_message(DM_SENDER, DM_RECIPIENT, DM_BODY, store=store)


def test_append_then_get_thread_round_trip(store, sent_message):
    # Arrange
    expected = [sent_message]
    # Act — read back from the OTHER direction; both resolve one thread.
    got = get_thread(DM_RECIPIENT, DM_SENDER, store=store)
    # Assert
    assert got == expected


def test_sent_record_has_the_canonical_shape(sent_message):
    # Arrange
    expected = {"id", "thread", "from", "to", "body", "ts", "read"}
    # Act
    fields = set(sent_message)
    # Assert
    assert fields == expected


def test_sent_record_names_the_sender(sent_message):
    # Arrange
    expected = DM_SENDER
    # Act
    sender = sent_message["from"]
    # Assert
    assert sender == expected


def test_sent_record_names_the_recipient(sent_message):
    # Arrange
    expected = DM_RECIPIENT
    # Act
    recipient = sent_message["to"]
    # Assert
    assert recipient == expected


def test_sent_record_carries_the_body(sent_message):
    # Arrange
    expected = DM_BODY
    # Act
    body = sent_message["body"]
    # Assert
    assert body == expected


def test_sent_record_carries_the_sorted_thread_key(sent_message):
    # Arrange
    expected = "dm:agent-x::operator"
    # Act
    thread = sent_message["thread"]
    # Assert
    assert thread == expected


def test_a_newly_sent_message_starts_unread(sent_message):
    # Arrange
    expected = False
    # Act
    read = sent_message["read"]
    # Assert
    assert read is expected


def test_sent_record_gets_a_prefixed_message_id(sent_message):
    # Arrange
    prefix = "m_"
    # Act
    message_id = sent_message["id"]
    # Assert
    assert message_id.startswith(prefix)


def test_sent_record_timestamp_is_utc_stamped(sent_message):
    # Arrange
    suffix = "Z"
    # Act
    ts = sent_message["ts"]
    # Assert
    assert ts.endswith(suffix)


def test_append_preserves_chronological_order(store):
    # Arrange
    append_message("operator", "agent-x", "first", store=store)
    append_message("agent-x", "operator", "second", store=store)
    # Act
    msgs = get_thread("operator", "agent-x", store=store)
    # Assert
    assert [m["body"] for m in msgs] == ["first", "second"]


def test_append_rejects_empty_body(store):
    # Arrange
    blank = "   "
    # Act
    # Assert
    with pytest.raises(ValueError):
        append_message("operator", "agent-x", blank, store=store)


def test_no_sidecar_exists_before_any_append(store):
    """The premise of the two never-raise tests below."""
    # Arrange
    sidecar = threads_path(store)
    # Act
    exists = sidecar.exists()
    # Assert
    assert not exists


def test_get_thread_on_missing_sidecar_returns_empty(store):
    # Arrange
    peer = "ghost"
    # Act — never raises on an absent sidecar.
    msgs = get_thread("operator", peer, store=store)
    # Assert
    assert msgs == []


def test_list_threads_on_missing_sidecar_returns_empty(store):
    # Arrange
    expected = {}
    # Act
    summary = list_threads(store=store)
    # Assert
    assert summary == expected


# === store isolation (sidecar, own lock) ===================================


def test_the_threads_sidecar_sits_next_to_tasks_yaml(store, sent_message):
    # Arrange
    expected = store.parent / "threads.yaml"
    # Act
    sidecar = threads_path(store)
    # Assert
    assert sidecar == expected


def test_the_threads_sidecar_is_created_on_append(store, sent_message):
    # Arrange
    sidecar = threads_path(store)
    # Act
    exists = sidecar.exists()
    # Assert
    assert exists


def test_the_sidecar_holds_the_appended_thread(store, sent_message):
    # Arrange
    key = "dm:agent-x::operator"
    # Act
    doc = safe_load(threads_path(store).read_text(encoding="utf-8"))
    # Assert
    assert key in doc["threads"]


def test_tasks_yaml_carries_no_threads_section(store, sent_message):
    """The isolation half: a DM must never land inside the task store."""
    # Arrange
    section = "threads"
    # Act
    tasks_doc = safe_load(store.read_text(encoding="utf-8"))
    # Assert
    assert section not in tasks_doc


def test_threads_use_their_own_lockfile(store, sent_message):
    # Arrange — a separate lock sentinel, not the tasks.yaml one.
    lockfile = store.parent / ".threads.yaml.lock"
    # Act
    exists = lockfile.exists()
    # Assert
    assert exists


# === crash-safe write ======================================================


@pytest.fixture()
def two_written_threads(store):
    """Several writes across TWO threads — the on-disk state the crash-safety
    tests below reparse. Three messages to agent-x, one to agent-y."""
    for i in range(3):
        append_message("operator", "agent-x", f"msg {i}", store=store)
    append_message("agent-y", "operator", "other thread", store=store)
    return store


def test_sidecar_reparses_cleanly_after_writes(two_written_threads):
    """The full structure survives a reparse of the raw bytes, exactly like
    the verify step does it."""
    # Arrange
    expected_threads = 2
    # Act
    doc = safe_load(threads_path(two_written_threads).read_text(encoding="utf-8"))
    # Assert
    assert len(doc["threads"]) == expected_threads


def test_every_appended_message_survives_the_reparse(two_written_threads):
    # Arrange
    key = "dm:agent-x::operator"
    # Act
    doc = safe_load(threads_path(two_written_threads).read_text(encoding="utf-8"))
    # Assert
    assert len(doc["threads"][key]) == 3


def test_writes_leave_no_tmp_sidecar_behind(two_written_threads):
    # Arrange
    tmp_sidecar = two_written_threads.parent / ".threads.yaml.tmp"
    # Act
    exists = tmp_sidecar.exists()
    # Assert
    assert not exists


# === mark_read / list_threads ==============================================


@pytest.fixture()
def two_inbound_one_outbound(store):
    """Two messages INBOUND to the operator plus one they sent — the shape
    that separates "flip the reader's unread" from "flip everything"."""
    append_message("agent-x", "operator", "one", store=store)
    append_message("agent-x", "operator", "two", store=store)
    append_message("operator", "agent-x", "reply", store=store)
    return store


def test_mark_read_all_for_reader(two_inbound_one_outbound):
    # Arrange
    key = thread_key("operator", "agent-x")
    # Act
    flipped = mark_read(key, "operator", store=two_inbound_one_outbound)
    # Assert — only the reader's two INBOUND messages count.
    assert flipped == 2


def test_mark_read_leaves_the_readers_own_message_unread(two_inbound_one_outbound):
    """You never "read" your own outbound message."""
    # Arrange
    key = thread_key("operator", "agent-x")
    # Act
    mark_read(key, "operator", store=two_inbound_one_outbound)
    # Assert
    msgs = get_thread("operator", "agent-x", store=two_inbound_one_outbound)
    assert [m["read"] for m in msgs] == [True, True, False]


def test_mark_read_is_idempotent_on_a_second_call(two_inbound_one_outbound):
    # Arrange
    key = thread_key("operator", "agent-x")
    mark_read(key, "operator", store=two_inbound_one_outbound)
    # Act
    flipped_again = mark_read(key, "operator", store=two_inbound_one_outbound)
    # Assert
    assert flipped_again == 0


def test_mark_read_scoped_to_ids(store):
    # Arrange
    first = append_message("agent-x", "operator", "one", store=store)
    append_message("agent-x", "operator", "two", store=store)
    # Act
    flipped = mark_read(first["thread"], "operator", ids=[first["id"]], store=store)
    # Assert
    assert flipped == 1


def test_mark_read_scoped_to_ids_leaves_the_rest_unread(store):
    # Arrange
    first = append_message("agent-x", "operator", "one", store=store)
    append_message("agent-x", "operator", "two", store=store)
    # Act
    mark_read(first["thread"], "operator", ids=[first["id"]], store=store)
    # Assert
    msgs = get_thread("operator", "agent-x", store=store)
    assert [m["read"] for m in msgs] == [True, False]


@pytest.fixture()
def two_pings(store):
    """Two unread messages from agent-x to the operator, newest last."""
    append_message("agent-x", "operator", "ping", store=store)
    last = append_message("agent-x", "operator", "ping again", store=store)
    return store, last


def test_list_threads_reports_the_thread_peers(two_pings):
    # Arrange
    store, _ = two_pings
    # Act
    row = list_threads(store=store)["dm:agent-x::operator"]
    # Assert
    assert row["peers"] == ("agent-x", "operator")


def test_list_threads_reports_the_message_count(two_pings):
    # Arrange
    store, _ = two_pings
    # Act
    row = list_threads(store=store)["dm:agent-x::operator"]
    # Assert
    assert row["count"] == 2


def test_list_threads_reports_unread_per_peer(two_pings):
    """The sender has nothing unread; the operator has both."""
    # Arrange
    store, _ = two_pings
    # Act
    row = list_threads(store=store)["dm:agent-x::operator"]
    # Assert
    assert row["unread"] == {"agent-x": 0, "operator": 2}


def test_list_threads_reports_the_last_message(two_pings):
    # Arrange
    store, last = two_pings
    # Act
    row = list_threads(store=store)["dm:agent-x::operator"]
    # Assert
    assert row["last"]["id"] == last["id"]


# === dm-dispatch → recipient inbox =========================================


#: A DM whose delivery record the six tests below each pin one field of.
#: The recipient is keyed by RAW NAME here — agent-x is not in the users
#: registry, which is exactly the fallback the dispatcher must honour.
DISPATCH_BODY = "please check PR"


@pytest.fixture()
def dispatched_dm(store):
    """One DM sent to an UNREGISTERED recipient, plus its inbox record."""
    sent = append_message("operator", "agent-x", DISPATCH_BODY, store=store)
    return sent, poll_inbox("agent-x", store=store)


def test_append_message_enqueues_dm_into_recipient_inbox(dispatched_dm):
    # Arrange
    _, inbox = dispatched_dm
    # Act
    count = len(inbox)
    # Assert
    assert count == 1


def test_the_enqueued_record_is_typed_as_a_dm(dispatched_dm):
    # Arrange
    _, inbox = dispatched_dm
    # Act
    event_type = inbox[0]["event_type"]
    # Assert
    assert event_type == "dm"


def test_the_enqueued_record_points_at_the_thread(dispatched_dm):
    # Arrange
    sent, inbox = dispatched_dm
    # Act
    card_id = inbox[0]["card_id"]
    # Assert
    assert card_id == sent["thread"]


def test_the_enqueued_record_carries_the_body(dispatched_dm):
    # Arrange
    _, inbox = dispatched_dm
    # Act
    body = inbox[0]["body"]
    # Assert
    assert body == DISPATCH_BODY


def test_the_enqueued_record_names_the_actor(dispatched_dm):
    # Arrange
    _, inbox = dispatched_dm
    # Act
    actor = inbox[0]["actor"]
    # Assert
    assert actor == "operator"


def test_the_enqueued_record_shares_the_message_timestamp(dispatched_dm):
    # Arrange
    sent, inbox = dispatched_dm
    # Act
    ts = inbox[0]["ts"]
    # Assert
    assert ts == sent["ts"]


#: Documented decision: an agent's REPLY enqueues into the operator's inbox
#: too (cheap, and keeps a future operator-side drain surface working).
@pytest.fixture()
def operator_reply_inbox(store):
    append_message("agent-x", "operator", "done, PR is green", store=store)
    return poll_inbox("operator", store=store)


def test_agent_reply_enqueues_into_operator_inbox_for_symmetry(
    operator_reply_inbox,
):
    # Arrange
    inbox = operator_reply_inbox
    # Act
    count = len(inbox)
    # Assert
    assert count == 1


def test_the_operator_side_record_is_typed_as_a_dm(operator_reply_inbox):
    # Arrange
    inbox = operator_reply_inbox
    # Act
    event_type = inbox[0]["event_type"]
    # Assert
    assert event_type == "dm"


def test_the_operator_side_record_names_the_replying_agent(operator_reply_inbox):
    # Arrange
    inbox = operator_reply_inbox
    # Act
    actor = inbox[0]["actor"]
    # Assert
    assert actor == "agent-x"


@pytest.fixture()
def registered_recipient(store):
    """A REGISTERED recipient, so dispatch must key by its stable u_* id —
    exactly how poll_notifications resolves the drain key."""
    from scitex_cards._users import register_user

    user = register_user(kind="agent", names=["agent-reg"], store=store)
    append_message("operator", "agent-reg", "hello registered", store=store)
    return store, user


def test_dispatch_resolves_registered_recipient_to_user_id(registered_recipient):
    # Arrange
    store, user = registered_recipient
    # Act
    inbox = poll_inbox(user.id, store=store)
    # Assert — enqueued under the stable id.
    assert len(inbox) == 1


def test_dispatch_does_not_also_enqueue_under_the_raw_name(registered_recipient):
    """Both keys filling would double-deliver every DM to a registered agent."""
    # Arrange
    store, _ = registered_recipient
    # Act
    inbox = poll_inbox("agent-reg", store=store)
    # Assert
    assert inbox == []


# === read cache ============================================================
#
# The GUI polls a thread every ~5s, and mark_read sits on that poll path, so
# the READ paths are served from an mtime-guarded cache of the parsed content.
# The rule the module lives or dies by: WRITERS NEVER READ THAT CACHE. These
# tests are what refuse the regression — not this file's comments.


def _poison_read_cache(path: Path, content: dict) -> None:
    """Install ``content`` as the cached parse of ``path``, stamped with the
    file's CURRENT ``(mtime_ns, size)`` so the guard considers it fresh.

    This reproduces — deterministically, without racing the clock — the exact
    stale-read state a write landing inside one mtime tick would produce on a
    coarse-granularity filesystem.
    """
    stat = path.stat()
    _threads._READ_CACHE[str(path)] = (stat.st_mtime_ns, stat.st_size, content)


def test_append_message_never_reads_the_cache(store: Path) -> None:
    """A writer served from a stale cache would serialize the stale mapping
    and DROP messages. append_message must re-read the file under the lock.
    Read back through the UNCACHED primitive so the assertion itself cannot
    be fooled by the cache."""
    # Arrange — one real message on disk, and a cache claiming the store is
    # empty. A cache-reading writer would believe it.
    append_message("agent-a", "operator", "first", store=store)
    path = threads_path(store)
    _poison_read_cache(path, {})
    # Act
    append_message("agent-a", "operator", "second", store=store)
    # Assert
    on_disk = _threads._load_threads(path)[thread_key("agent-a", "operator")]
    assert [r["body"] for r in on_disk] == ["first", "second"]


def test_mark_read_flip_reads_the_file_not_the_cache(store: Path) -> None:
    """The fast NO may consult the cache; the authoritative flip may not.
    Here the cache sees only ONE of two unread messages — the flip must still
    find both on disk."""
    # Arrange
    append_message("agent-a", "operator", "first", store=store)
    append_message("agent-a", "operator", "second", store=store)
    path = threads_path(store)
    key = thread_key("agent-a", "operator")
    full = _threads._load_threads(path)
    _poison_read_cache(path, {key: [dict(full[key][0])]})
    # Act — the pre-check passes (the cache holds an unread one), so the
    # authoritative half runs against the real file.
    flipped = mark_read(key, "operator", store=store)
    # Assert
    assert flipped == 2


def test_mark_read_does_not_truncate_the_store_from_a_stale_cache(
    store: Path,
) -> None:
    """The save after a flip must not write back the cache's truncated view."""
    # Arrange
    append_message("agent-a", "operator", "first", store=store)
    append_message("agent-a", "operator", "second", store=store)
    path = threads_path(store)
    key = thread_key("agent-a", "operator")
    full = _threads._load_threads(path)
    _poison_read_cache(path, {key: [dict(full[key][0])]})
    # Act
    mark_read(key, "operator", store=store)
    # Assert
    assert len(_threads._load_threads(path)[key]) == 2


def test_fast_no_is_derived_per_reader_not_memoized(store: Path) -> None:
    """The cache stores parsed CONTENT; "has unread" is re-derived for each
    reader per call. One peer's "nothing unread" must never answer for the
    other's — both readers here are evaluated against the SAME cache entry."""
    # Arrange — one message each way, then clear only the operator's side.
    append_message("agent-a", "operator", "to-operator", store=store)
    append_message("operator", "agent-a", "to-agent", store=store)
    key = thread_key("agent-a", "operator")
    mark_read(key, "operator", store=store)
    get_thread("agent-a", "operator", store=store)  # warm the cache
    # Act — operator now has nothing (fast NO); agent-a still has one.
    operator_flipped = mark_read(key, "operator", store=store)
    agent_flipped = mark_read(key, "agent-a", store=store)
    # Assert — a per-path memoized boolean would hand the operator's 0 to
    # agent-a and leave its message unread forever.
    assert (operator_flipped, agent_flipped) == (0, 1)


def test_stale_cache_defers_the_flip_by_one_poll(store: Path) -> None:
    """The ACCEPTED cost of the lock-free fast NO, pinned as chosen behavior:
    a stale cache makes this poll a no-op. It is not a bug to be "fixed" by
    putting the writer on the cache — that is the one failure mode."""
    # Arrange — genuinely unread on disk, but the cache says all read.
    append_message("agent-a", "operator", "hello", store=store)
    path = threads_path(store)
    key = thread_key("agent-a", "operator")
    stale = [dict(r, read=True) for r in _threads._load_threads(path)[key]]
    _poison_read_cache(path, {key: stale})
    # Act
    flipped = mark_read(key, "operator", store=store)
    # Assert
    assert flipped == 0


def test_stale_cache_self_heals_on_the_next_poll(store: Path) -> None:
    """...and the deferral is bounded at one poll: the next stamp roll
    re-parses and the message is marked. This is what makes the accepted cost
    a delay rather than a lost flip."""
    # Arrange — take the false-negative poll above, then roll the stamp the
    # way any subsequent write would.
    append_message("agent-a", "operator", "hello", store=store)
    path = threads_path(store)
    key = thread_key("agent-a", "operator")
    stale = [dict(r, read=True) for r in _threads._load_threads(path)[key]]
    _poison_read_cache(path, {key: stale})
    mark_read(key, "operator", store=store)  # the deferred poll
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    # Act
    flipped = mark_read(key, "operator", store=store)
    # Assert
    assert flipped == 1


def test_get_thread_is_served_from_the_read_cache(store: Path) -> None:
    """Pins that the read path actually consults the cache — otherwise the
    5s poll is still paying a full parse and this whole change is inert."""
    # Arrange
    append_message("agent-a", "operator", "on-disk", store=store)
    path = threads_path(store)
    key = thread_key("agent-a", "operator")
    cached = [dict(r, body="from-cache") for r in _threads._load_threads(path)[key]]
    _poison_read_cache(path, {key: cached})
    # Act
    bodies = [m["body"] for m in get_thread("agent-a", "operator", store=store)]
    # Assert
    assert bodies == ["from-cache"]


def test_list_threads_is_served_from_the_read_cache(store: Path) -> None:
    """Same for the agent-list poll (~10s)."""
    # Arrange
    append_message("agent-a", "operator", "on-disk", store=store)
    path = threads_path(store)
    key = thread_key("agent-a", "operator")
    cached = [dict(r, body="from-cache") for r in _threads._load_threads(path)[key]]
    _poison_read_cache(path, {key: cached})
    # Act
    summary = list_threads(store=store)[key]
    # Assert
    assert summary["last"]["body"] == "from-cache"


def test_a_write_invalidates_the_cached_read(store: Path) -> None:
    """The property that makes the cache safe to read from: any write rolls
    the mtime, so the next read re-parses. No reader sees a stale thread."""
    # Arrange — warm the cache on a one-message thread.
    append_message("agent-a", "operator", "first", store=store)
    get_thread("agent-a", "operator", store=store)
    # Act
    append_message("agent-a", "operator", "second", store=store)
    bodies = [m["body"] for m in get_thread("agent-a", "operator", store=store)]
    # Assert
    assert bodies == ["first", "second"]


def test_cached_read_of_an_absent_sidecar_is_empty(store: Path) -> None:
    """Absent file → {} and nothing cached (the uncached primitive's
    never-raise-on-absence contract must survive the cache)."""
    # Arrange
    path = threads_path(store)
    # Act
    threads = _threads._load_threads_cached(path)
    # Assert
    assert threads == {} and str(path) not in _threads._READ_CACHE


# EOF
