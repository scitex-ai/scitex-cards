#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for scitex-todo's OWN standalone channel-notification server.

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store, real
:mod:`scitex_cards._inbox` enqueue/poll/ack, and a real (in-process) async
``send`` recorder — no ``unittest.mock``. The live MCP stdio session is not
exercised here (it is driven only at runtime); instead the pure logic seams
(``build_channel_params``, ``drain_once``, ``resolve_agent_id``) are tested
with real objects so the receive→push path is covered end to end.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from scitex_cards import _inbox
from scitex_cards._channel_drain_state import (
    _DrainState,
    gated_drain_once,
    should_drain,
)
from scitex_cards._mcp_channel import (
    build_channel_params,
    drain_once,
    recipient_keys,
    resolve_agent_id,
    resolve_agent_id_optional,
)
from scitex_cards._users import register_user


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


#: The one fully-populated inbox record every `build_channel_params` test reads.
_FULL_RECORD = {
    "id": "n_abc123",
    "event_type": "reassigned",
    "card_id": "c1",
    "body": "Card c1 reassigned to you (by bob)",
    "actor": "bob",
    "ts": "2026-06-28T10:00:00Z",
    "seen": False,
}

#: A sparse record — only a body. Every other meta key must still be a string.
_SPARSE_RECORD = {"body": "x"}

#: The meta keys that must appear as empty strings when the record omits them.
_OPTIONAL_META_KEYS = ("ts", "event_type", "card_id", "actor", "msg_id")


class _SendRecorder:
    """A real async ``send`` callable — records every pushed params payload."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, params: dict) -> None:
        self.calls.append(params)


class _FailingSend:
    """A real async ``send`` that always raises — exercises the retry path."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, params: dict) -> None:
        self.calls.append(params)
        raise RuntimeError("simulated push failure")


# --------------------------------------------------------------------------- #
# fixtures — shared setup for the split tests below                            #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def drained_two_records(tmp_path):
    """Two unseen records for agent-x, drained once through a recorder."""
    store = _store(tmp_path)
    agent = "agent-x"
    r1 = _inbox.enqueue(
        agent,
        event_type="reassigned",
        card_id="c1",
        body="body one",
        actor="bob",
        ts="2026-06-28T10:00:00Z",
        store=store,
    )
    r2 = _inbox.enqueue(
        agent,
        event_type="completed",
        card_id="c2",
        body="body two",
        actor="alice",
        ts="2026-06-28T10:01:00Z",
        store=store,
    )
    recorder = _SendRecorder()
    pushed = asyncio.run(drain_once(agent, recorder, store=store))
    return {
        "store": store,
        "agent": agent,
        "r1": r1,
        "r2": r2,
        "pushed": pushed,
        "recorder": recorder,
    }


@pytest.fixture()
def failed_drain(tmp_path):
    """One record drained through a send that always raises."""
    store = _store(tmp_path)
    agent = "agent-y"
    _inbox.enqueue(
        agent,
        event_type="reassigned",
        card_id="c1",
        body="retry me",
        actor="bob",
        ts="2026-06-28T10:00:00Z",
        store=store,
    )
    failing = _FailingSend()
    pushed = asyncio.run(drain_once(agent, failing, store=store))
    return {"store": store, "agent": agent, "pushed": pushed, "failing": failing}


@pytest.fixture()
def user_id_keyed_inbox(tmp_path):
    """A record enqueued under a REGISTERED agent's user-id, not its name.

    The live silent-drop: the notify dispatcher enqueues to a registered
    agent's USER-ID, but the channel was launched with the raw NAME. The
    channel must still find + deliver it (it now drains both keys).
    """
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    _inbox.enqueue(
        alice.id,  # producer key for a registered name
        event_type="reassigned",
        card_id="c1",
        body="assigned to you",
        actor="operator",
        ts="2026-06-28T10:00:00Z",
        store=store,
    )
    recorder = _SendRecorder()
    pushed = asyncio.run(drain_once("alice", recorder, store=store))
    return {"store": store, "alice": alice, "pushed": pushed, "recorder": recorder}


@pytest.fixture()
def mtime_gate_verdicts(tmp_path):
    """``should_drain`` verdicts across four ticks: first (seeds last_mtime),
    unchanged, mtime-advanced, and settled again.

    The gate stats the ACTUAL file the break-glass inbox backend reads —
    ``inboxes.json``, a sibling of the (legacy) task-store path — not the
    task-store path itself (see ``_channel_drain_state._resolve_store_file``).
    """
    store = _store(tmp_path)
    from scitex_cards._inbox import _inboxes_path

    gated_file = _inboxes_path(store)
    gated_file.write_text('{"inboxes": {}}', encoding="utf-8")
    state = _DrainState()
    verdicts = [
        should_drain(state, store=store),  # first tick → drain (seed)
        should_drain(state, store=store),  # unchanged → skip
    ]
    # Force a strictly-later mtime (deterministic — no sleep / no FS-granularity
    # race): an advanced store mtime must re-open the drain.
    bumped = os.stat(gated_file).st_mtime + 10.0
    os.utime(gated_file, (bumped, bumped))
    verdicts.append(should_drain(state, store=store))  # advanced → drain
    verdicts.append(should_drain(state, store=store))  # settled again → skip
    return verdicts


@pytest.fixture()
def unstatable_store_verdicts(tmp_path):
    """``should_drain`` verdicts for two ticks against an unstatable path."""
    missing = tmp_path / "does-not-exist.yaml"
    state = _DrainState()
    return [
        should_drain(state, store=missing),
        should_drain(state, store=missing),
    ]


@pytest.fixture()
def gated_drain_on_unchanged_store(tmp_path, monkeypatch):
    """A gated tick on an UNCHANGED store, with spy counters on the parsers.

    The spies delegate to the REAL functions (not mocks); they only count.
    """
    store = _store(tmp_path)
    agent = "agent-gate"
    _inbox.enqueue(
        agent,
        event_type="reassigned",
        card_id="c1",
        body="hi",
        actor="bob",
        ts="2026-06-28T10:00:00Z",
        store=store,
    )
    # Seed the gate so its last_mtime matches the store's CURRENT mtime; no
    # drain runs (so no ack-write bumps the mtime).
    state = _DrainState()
    seeded = should_drain(state, store=store)

    import scitex_cards._mcp_channel as chan

    calls = {"recipient_keys": 0, "poll_inbox": 0}
    real_rk = chan.recipient_keys
    real_pi = _inbox.poll_inbox

    def spy_rk(*a, **k):
        calls["recipient_keys"] += 1
        return real_rk(*a, **k)

    def spy_pi(*a, **k):
        calls["poll_inbox"] += 1
        return real_pi(*a, **k)

    monkeypatch.setattr(chan, "recipient_keys", spy_rk)
    monkeypatch.setattr(_inbox, "poll_inbox", spy_pi)

    recorder = _SendRecorder()
    pushed = asyncio.run(
        gated_drain_once(agent, recorder, state, source="stodo", store=store)
    )
    return {
        "seeded": seeded,
        "pushed": pushed,
        "recorder": recorder,
        "calls": calls,
    }


@pytest.fixture()
def gated_drain_after_change(tmp_path):
    """A first gated tick that drains, then a NEW enqueue and a second tick.

    After the first tick drains+acks, a NEW enqueue changes the store → the
    next gated tick sees the advanced mtime and delivers the new record.
    """
    store = _store(tmp_path)
    agent = "agent-chg"
    _inbox.enqueue(
        agent,
        event_type="completed",
        card_id="c1",
        body="first",
        actor="bob",
        ts="2026-06-28T10:00:00Z",
        store=store,
    )
    state = _DrainState()
    first_pushed = asyncio.run(
        gated_drain_once(agent, _SendRecorder(), state, source="stodo", store=store)
    )
    _inbox.enqueue(
        agent,
        event_type="reassigned",
        card_id="c2",
        body="second",
        actor="alice",
        ts="2026-06-28T10:01:00Z",
        store=store,
    )
    # Guarantee a strictly-later mtime regardless of FS granularity. Stat the
    # ACTUAL gated file (see _channel_drain_state._resolve_store_file) — the
    # break-glass backend's inboxes.json sidecar, not the legacy store path.
    from scitex_cards._inbox import _inboxes_path

    gated_file = _inboxes_path(store)
    bumped = os.stat(gated_file).st_mtime + 10.0
    os.utime(gated_file, (bumped, bumped))
    recorder = _SendRecorder()
    pushed = asyncio.run(
        gated_drain_once(agent, recorder, state, source="stodo", store=store)
    )
    return {
        "first_pushed": first_pushed,
        "pushed": pushed,
        "recorder": recorder,
    }


# --------------------------------------------------------------------------- #
# build_channel_params — every meta value is a string                         #
# --------------------------------------------------------------------------- #
def test_build_channel_params_content_is_the_record_body():
    # Arrange
    rec = _FULL_RECORD
    # Act
    params = build_channel_params(rec)
    # Assert
    assert params["content"] == rec["body"]


def test_build_channel_params_source_defaults_to_stodo():
    # Arrange
    rec = _FULL_RECORD
    # Act
    params = build_channel_params(rec)
    # Assert — source drives the `<- stodo` render (the fleet's short
    # sender-identity label — distinct from the scitex-todo agent id so the TUI
    # doesn't confuse system pushes with the agent's own messages).
    assert params["meta"]["source"] == "stodo"


def test_build_channel_params_all_meta_strings():
    # Arrange
    rec = _FULL_RECORD
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert — EVERY meta value MUST be a string (Claude's Zod validator).
    assert all(isinstance(v, str) for v in meta.values()), (
        f"non-str meta values: { {k: type(v) for k, v in meta.items() if not isinstance(v, str)} }"
    )


def test_build_channel_params_meta_carries_the_card_id():
    # Arrange
    rec = _FULL_RECORD
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert meta["card_id"] == "c1"


def test_build_channel_params_meta_carries_the_event_type():
    # Arrange
    rec = _FULL_RECORD
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert meta["event_type"] == "reassigned"


def test_build_channel_params_meta_carries_the_actor():
    # Arrange
    rec = _FULL_RECORD
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert meta["actor"] == "bob"


def test_build_channel_params_meta_carries_the_msg_id():
    # Arrange
    rec = _FULL_RECORD
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert meta["msg_id"] == "n_abc123"


def test_build_channel_params_meta_carries_the_ts():
    # Arrange
    rec = _FULL_RECORD
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert meta["ts"] == "2026-06-28T10:00:00Z"


def test_build_channel_params_custom_source():
    # Arrange
    rec = {"body": "hi"}
    # Act
    params = build_channel_params(rec, source="my-board")
    # Assert
    assert params["meta"]["source"] == "my-board"


def test_build_channel_params_missing_fields_become_empty_strings():
    # A sparse record (only a body) must still produce all-string meta.
    # Arrange
    rec = _SPARSE_RECORD
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert all(meta[key] == "" for key in _OPTIONAL_META_KEYS)


def test_build_channel_params_missing_fields_are_still_strings():
    # Arrange
    rec = _SPARSE_RECORD
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert — absent must render as "", never as None (the Zod validator).
    assert all(isinstance(meta[key], str) for key in _OPTIONAL_META_KEYS)


def test_build_channel_params_none_body_becomes_empty_string():
    # Arrange
    rec = {"body": None}
    # Act
    params = build_channel_params(rec)
    # Assert
    assert params["content"] == ""


# --------------------------------------------------------------------------- #
# drain_once — real inbox, fake send recorder                                 #
# --------------------------------------------------------------------------- #
def test_two_records_enqueue_successfully(drained_two_records):
    """The precondition: without both records the drain asserts below are vacuous."""
    # Arrange
    r1, r2 = drained_two_records["r1"], drained_two_records["r2"]
    # Act
    both = r1 and r2
    # Assert
    assert both


def test_drain_once_pushes_each_unseen_and_acks(drained_two_records):
    # Arrange
    result = drained_two_records
    # Act
    pushed = result["pushed"]
    # Assert
    assert pushed == 2


def test_drain_once_calls_send_for_each_record(drained_two_records):
    # Arrange
    recorder = drained_two_records["recorder"]
    # Act
    calls = recorder.calls
    # Assert
    assert len(calls) == 2


def test_drain_once_pushes_every_body(drained_two_records):
    # Arrange
    recorder = drained_two_records["recorder"]
    # Act
    bodies = {c["content"] for c in recorder.calls}
    # Assert
    assert bodies == {"body one", "body two"}


def test_drain_once_stamps_the_default_source(drained_two_records):
    # Arrange
    recorder = drained_two_records["recorder"]
    # Act
    sources = {c["meta"]["source"] for c in recorder.calls}
    # Assert
    assert sources == {"stodo"}


def test_drain_once_meta_values_are_all_strings(drained_two_records):
    # Arrange
    recorder = drained_two_records["recorder"]
    # Act
    values = [v for c in recorder.calls for v in c["meta"].values()]
    # Assert — Claude's Zod validator rejects any non-string meta value.
    assert all(isinstance(v, str) for v in values)


def test_drain_once_second_drain_pushes_nothing(drained_two_records):
    # After a successful push the records are ack'd — the unseen-only cursor
    # has advanced.
    # Arrange
    store, agent = drained_two_records["store"], drained_two_records["agent"]
    # Act
    pushed2 = asyncio.run(drain_once(agent, _SendRecorder(), store=store))
    # Assert
    assert pushed2 == 0


def test_drain_once_second_drain_calls_no_send(drained_two_records):
    # Arrange
    store, agent = drained_two_records["store"], drained_two_records["agent"]
    recorder2 = _SendRecorder()
    # Act
    asyncio.run(drain_once(agent, recorder2, store=store))
    # Assert
    assert recorder2.calls == []


def test_failed_send_counts_nothing_as_pushed(failed_drain):
    # Arrange
    result = failed_drain
    # Act
    pushed = result["pushed"]
    # Assert — the send raised, so nothing counted as pushed.
    assert pushed == 0


def test_failed_send_was_attempted_once(failed_drain):
    # Arrange
    failing = failed_drain["failing"]
    # Act
    calls = failing.calls
    # Assert
    assert len(calls) == 1


def test_drain_once_failed_send_is_not_acked_and_retried(failed_drain):
    # Arrange
    store, agent = failed_drain["store"], failed_drain["agent"]
    # Act
    pending = _inbox.poll_inbox(agent, unseen_only=True, mark_seen=False, store=store)
    # Assert — still unseen; the failed record is retried on the next drain.
    assert len(pending) == 1


def test_failed_record_keeps_its_body_for_retry(failed_drain):
    # Arrange
    store, agent = failed_drain["store"], failed_drain["agent"]
    # Act
    pending = _inbox.poll_inbox(agent, unseen_only=True, mark_seen=False, store=store)
    # Assert
    assert pending[0]["body"] == "retry me"


def test_retry_drain_delivers_the_failed_record(failed_drain):
    # Arrange
    store, agent = failed_drain["store"], failed_drain["agent"]
    # Act
    pushed2 = asyncio.run(drain_once(agent, _SendRecorder(), store=store))
    # Assert — the next drain with a working send delivers it.
    assert pushed2 == 1


def test_retry_drain_calls_send_once(failed_drain):
    # Arrange
    store, agent = failed_drain["store"], failed_drain["agent"]
    recorder = _SendRecorder()
    # Act
    asyncio.run(drain_once(agent, recorder, store=store))
    # Assert
    assert len(recorder.calls) == 1


def test_retry_drain_acks_the_record(failed_drain):
    # Arrange
    store, agent = failed_drain["store"], failed_drain["agent"]
    asyncio.run(drain_once(agent, _SendRecorder(), store=store))
    # Act
    pending = _inbox.poll_inbox(agent, unseen_only=True, mark_seen=False, store=store)
    # Assert
    assert pending == []


def test_drain_once_empty_inbox_is_noop(tmp_path):
    # Arrange
    recorder = _SendRecorder()
    # Act
    pushed = asyncio.run(drain_once("nobody", recorder, store=_store(tmp_path)))
    # Assert
    assert pushed == 0


def test_drain_once_empty_inbox_calls_no_send(tmp_path):
    # Arrange
    recorder = _SendRecorder()
    # Act
    asyncio.run(drain_once("nobody", recorder, store=_store(tmp_path)))
    # Assert
    assert recorder.calls == []


def test_drain_once_custom_source_propagates(tmp_path):
    # Arrange
    store = _store(tmp_path)
    agent = "agent-z"
    _inbox.enqueue(
        agent,
        event_type="completed",
        card_id="c9",
        body="hi",
        actor=None,
        ts="2026-06-28T10:00:00Z",
        store=store,
    )
    recorder = _SendRecorder()
    # Act
    asyncio.run(drain_once(agent, recorder, source="custom-board", store=store))
    # Assert
    assert recorder.calls[0]["meta"]["source"] == "custom-board"


# --------------------------------------------------------------------------- #
# recipient_keys — consumer keys EXACTLY like the producer (registered→id)    #
# --------------------------------------------------------------------------- #
def test_recipient_keys_unregistered_is_raw_name_only(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    keys = recipient_keys("ghost", store=store)
    # Assert
    assert keys == ["ghost"]


def test_recipient_keys_registered_name_includes_resolved_user_id(tmp_path):
    # Arrange
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # Act
    keys = recipient_keys("alice", store=store)
    # Assert — raw name first (back-compat), then the resolved user-id the
    # producer enqueues under for a REGISTERED name.
    assert keys == ["alice", alice.id]


def test_drain_once_delivers_records_enqueued_under_resolved_user_id(
    user_id_keyed_inbox,
):
    # Arrange
    result = user_id_keyed_inbox
    # Act
    pushed = result["pushed"]
    # Assert
    assert pushed == 1


def test_drain_once_delivers_the_user_id_records_body(user_id_keyed_inbox):
    # Arrange
    recorder = user_id_keyed_inbox["recorder"]
    # Act
    content = recorder.calls[0]["content"]
    # Assert
    assert content == "assigned to you"


def test_drain_once_acks_on_the_user_id_key(user_id_keyed_inbox):
    # Arrange
    store = user_id_keyed_inbox["store"]
    # Act
    pushed2 = asyncio.run(drain_once("alice", _SendRecorder(), store=store))
    # Assert — ack'd on the user-id key, so a second drain pushes nothing.
    assert pushed2 == 0


# --------------------------------------------------------------------------- #
# mtime-gate — the drain short-circuit that stops the per-agent 9MB re-parse   #
# (real store tmp files, spy counters — NO mocks of the store)                 #
# --------------------------------------------------------------------------- #
def test_should_drain_seeds_the_first_tick(mtime_gate_verdicts):
    # Arrange
    verdicts = mtime_gate_verdicts
    # Act
    first = verdicts[0]
    # Assert — the FIRST tick always drains (it seeds last_mtime).
    assert first is True


def test_should_drain_seeds_first_tick_then_skips_unchanged(mtime_gate_verdicts):
    # Arrange
    verdicts = mtime_gate_verdicts
    # Act
    second = verdicts[1]
    # Assert — an UNCHANGED store skips: one stat(), no parse.
    assert second is False


def test_should_drain_true_after_mtime_advances(mtime_gate_verdicts):
    # Arrange
    verdicts = mtime_gate_verdicts
    # Act
    after_bump = verdicts[2]
    # Assert
    assert after_bump is True


def test_should_drain_skips_after_settling_again(mtime_gate_verdicts):
    # Arrange
    verdicts = mtime_gate_verdicts
    # Act
    settled = verdicts[3]
    # Assert
    assert settled is False


def test_should_drain_fail_safe_when_store_unstatable(unstatable_store_verdicts):
    # An explicit-but-missing store path can't be stat'd → fail SAFE = drain
    # EVERY tick, so an unresolvable path never silently drops a notification.
    # Arrange
    verdicts = unstatable_store_verdicts
    # Act
    first = verdicts[0]
    # Assert
    assert first is True


def test_should_drain_fail_safe_stays_open_every_tick(unstatable_store_verdicts):
    # Arrange
    verdicts = unstatable_store_verdicts
    # Act
    second = verdicts[1]
    # Assert — it must not "settle" into skipping an unstatable path.
    assert second is True


def test_gated_drain_seed_tick_opens_the_gate(gated_drain_on_unchanged_store):
    # Arrange
    result = gated_drain_on_unchanged_store
    # Act
    seeded = result["seeded"]
    # Assert — without this the skip assertions below would be vacuous.
    assert seeded is True


def test_gated_drain_pushes_nothing_when_mtime_unchanged(
    gated_drain_on_unchanged_store,
):
    # Arrange
    result = gated_drain_on_unchanged_store
    # Act
    pushed = result["pushed"]
    # Assert
    assert pushed == 0


def test_gated_drain_calls_no_send_when_mtime_unchanged(gated_drain_on_unchanged_store):
    # Arrange
    recorder = gated_drain_on_unchanged_store["recorder"]
    # Act
    calls = recorder.calls
    # Assert
    assert calls == []


def test_gated_drain_skips_all_parsing_when_mtime_unchanged(
    gated_drain_on_unchanged_store,
):
    # The core CPU fix: on an unchanged store the gated tick must NOT touch the
    # full-store parsers (recipient_keys / poll_inbox).
    # Arrange
    result = gated_drain_on_unchanged_store
    # Act
    calls = result["calls"]
    # Assert — nothing parsed; the whole point of the gate.
    assert calls == {"recipient_keys": 0, "poll_inbox": 0}


def test_gated_drain_first_tick_delivers_pending(tmp_path):
    # A fresh loop's first tick always drains — a pending record is delivered.
    # Arrange
    store = _store(tmp_path)
    agent = "agent-first"
    _inbox.enqueue(
        agent,
        event_type="completed",
        card_id="c1",
        body="seed me",
        actor="bob",
        ts="2026-06-28T10:00:00Z",
        store=store,
    )
    recorder = _SendRecorder()
    # Act
    pushed = asyncio.run(
        gated_drain_once(agent, recorder, _DrainState(), source="stodo", store=store)
    )
    # Assert
    assert pushed == 1


def test_gated_drain_first_tick_delivers_the_body(tmp_path):
    # Arrange
    store = _store(tmp_path)
    agent = "agent-first"
    _inbox.enqueue(
        agent,
        event_type="completed",
        card_id="c1",
        body="seed me",
        actor="bob",
        ts="2026-06-28T10:00:00Z",
        store=store,
    )
    recorder = _SendRecorder()
    # Act
    asyncio.run(
        gated_drain_once(agent, recorder, _DrainState(), source="stodo", store=store)
    )
    # Assert
    assert recorder.calls[0]["content"] == "seed me"


def test_gated_drain_first_tick_delivers_before_the_change(gated_drain_after_change):
    # Arrange
    result = gated_drain_after_change
    # Act
    first_pushed = result["first_pushed"]
    # Assert — the first record was delivered and acked before the new enqueue.
    assert first_pushed == 1


def test_gated_drain_pushes_when_store_changed(gated_drain_after_change):
    # Arrange
    result = gated_drain_after_change
    # Act
    pushed = result["pushed"]
    # Assert — the advanced mtime re-opened the gate.
    assert pushed == 1


def test_gated_drain_delivers_the_new_record_body(gated_drain_after_change):
    # Arrange
    recorder = gated_drain_after_change["recorder"]
    # Act
    content = recorder.calls[0]["content"]
    # Assert
    assert content == "second"


# --------------------------------------------------------------------------- #
# resolve_agent_id — fail loud on unresolved                                  #
# --------------------------------------------------------------------------- #
def test_resolve_agent_id_explicit_arg():
    # Arrange
    explicit = "my-agent"
    # Act
    resolved = resolve_agent_id(explicit)
    # Assert
    assert resolved == "my-agent"


def test_resolve_agent_id_from_env(monkeypatch):
    # Arrange
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "env-agent")
    # Act
    resolved = resolve_agent_id()
    # Assert
    assert resolved == "env-agent"


def test_resolve_agent_id_unresolved_raises(monkeypatch):
    # Arrange
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(RuntimeError):
        resolve_agent_id()


def test_resolve_agent_id_unresolved_message_names_the_env_var(monkeypatch):
    # Arrange
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    # Act
    # Assert — the failure must name the var the operator has to set.
    with pytest.raises(RuntimeError, match="SCITEX_TODO_AGENT_ID"):
        resolve_agent_id()


def test_resolve_agent_id_unknown_sentinel_raises(monkeypatch):
    # Arrange
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(RuntimeError):
        resolve_agent_id("unknown")


def test_resolve_agent_id_blank_raises(monkeypatch):
    # Arrange
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(RuntimeError):
        resolve_agent_id("   ")


#: WHY the `unexpanded_placeholder` tests below are split but share one story:
#: the launcher passed the literal text instead of expanding it — Claude Code
#: only expands the `${VAR}` brace form, never bare `$VAR`. Draining an inbox
#: keyed by that literal would silently deliver nothing, so it must fail loud
#: for both spellings and from both the argument and the environment.


def test_resolve_agent_id_unexpanded_placeholder_arg_raises(monkeypatch):
    # Arrange
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(RuntimeError):
        resolve_agent_id("$SCITEX_TODO_AGENT_ID")


def test_resolve_agent_id_placeholder_message_says_placeholder(monkeypatch):
    # Arrange
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    # Act
    # Assert — the message must name the diagnosis, not just fail.
    with pytest.raises(RuntimeError, match="placeholder"):
        resolve_agent_id("$SCITEX_TODO_AGENT_ID")


def test_resolve_agent_id_unexpanded_placeholder_braces_arg_raises(monkeypatch):
    # Arrange
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(RuntimeError):
        resolve_agent_id("${SCITEX_TODO_AGENT_ID}")


def test_resolve_agent_id_unexpanded_placeholder_from_env_raises(monkeypatch):
    # Arrange
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "$SCITEX_TODO_AGENT_ID")
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(RuntimeError):
        resolve_agent_id()


def test_resolve_agent_id_current_var_wins_over_stale_deprecated(monkeypatch):
    """The CURRENT var wins: a valid $SCITEX_TODO_AGENT_ID must NOT be disabled
    by a leftover stale $SCITEX_TODO_AGENT. This is the incident fix — fleet
    agents carry a stale ambient old-name export baked in by an old injector;
    a correctly configured AGENT_ID must still resolve (so the poll loop runs)."""
    # Arrange — a valid NEW-name id AND the stale old name both set.
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "env-agent")
    monkeypatch.setenv("SCITEX_TODO_AGENT", "legacy-agent")
    # Act
    resolved = resolve_agent_id()
    # Assert — the current var wins, no raise.
    assert resolved == "env-agent"


def test_resolve_agent_id_only_deprecated_env_var_fails_loud(monkeypatch):
    """With NO current $SCITEX_TODO_AGENT_ID but the renamed-away
    $SCITEX_TODO_AGENT still exported, resolution fails LOUD pointing at the new
    name — a genuine reliance on the old var the operator must migrate."""
    # Arrange — only the deprecated old name is set.
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    monkeypatch.setenv("SCITEX_TODO_AGENT", "legacy-agent")
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(RuntimeError, match="SCITEX_TODO_AGENT_ID"):
        resolve_agent_id()


# === resolve_agent_id_optional — the unified server's tools-only fallback =====


def test_resolve_agent_id_optional_returns_id_when_set(monkeypatch):
    """With an identity, the unified server enables the digest push."""
    # Arrange
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "env-agent")
    # Act
    resolved = resolve_agent_id_optional()
    # Assert
    assert resolved == "env-agent"


def test_resolve_agent_id_optional_returns_none_when_unset(monkeypatch):
    """No identity ⇒ the unified server serves tools ONLY (push disabled).
    It must NOT raise — the tools surface has to work without an agent id."""
    # Arrange
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    # Act
    resolved = resolve_agent_id_optional()
    # Assert
    assert resolved is None


def test_resolve_agent_id_optional_none_on_deprecated_env(monkeypatch):
    """ONLY the deprecated $SCITEX_TODO_AGENT set (no current AGENT_ID) makes
    resolve fail loud; the optional variant swallows it to None (tools-only)
    rather than crashing the server — the loud warning still surfaces it."""
    # Arrange
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    monkeypatch.setenv("SCITEX_TODO_AGENT", "legacy-agent")
    # Act
    resolved = resolve_agent_id_optional()
    # Assert
    assert resolved is None


def test_resolve_agent_id_optional_returns_id_when_both_vars_set(monkeypatch):
    """THE key regression that re-enables the poll loop: a valid AGENT_ID plus a
    stale deprecated $SCITEX_TODO_AGENT must return the id (NOT None). Before the
    fix the mere presence of the old var made resolve fail loud → optional
    returned None → the digest poll loop never started (server connected, tools
    worked, but no channel notifications were ever pushed)."""
    # Arrange
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "env-agent")
    monkeypatch.setenv("SCITEX_TODO_AGENT", "legacy-agent")
    # Act
    resolved = resolve_agent_id_optional()
    # Assert
    assert resolved == "env-agent"


# === unified server: one scitex-todo serves tools AND declares the channel ====

#: WHY the two `unified_server` tests below are split but share one story: the
#: unified `mcp start` runs FastMCP's underlying low-level server (which has the
#: card tools) with the `claude/channel` capability added — so ONE server both
#: serves tools AND pushes the digest. Adding the channel capability must NOT
#: drop the tools capability.


def test_unified_server_keeps_tools_and_adds_channel_capability():
    # Arrange
    pytest.importorskip("fastmcp")
    from scitex_cards._mcp_server import mcp

    # Act
    opts = mcp._mcp_server.create_initialization_options(
        experimental_capabilities={"claude/channel": {}}
    )
    # Assert
    assert opts.capabilities.tools is not None, "tools capability was dropped"


def test_unified_server_declares_the_channel_capability():
    # Arrange
    pytest.importorskip("fastmcp")
    from scitex_cards._mcp_server import mcp

    # Act
    opts = mcp._mcp_server.create_initialization_options(
        experimental_capabilities={"claude/channel": {}}
    )
    # Assert
    assert opts.capabilities.experimental == {"claude/channel": {}}


def test_unified_start_wiring_present():
    """`scitex-todo mcp start` is wired to the unified server (tools + push)."""
    # Arrange
    from scitex_cards._cli import _mcp

    # Act
    has_runner = hasattr(_mcp, "_run_unified_server")
    # Assert
    assert has_runner


def test_unified_attach_start_wiring_present():
    """The unified start verb is attached to the `mcp` command group."""
    # Arrange
    from scitex_cards._cli import _mcp

    # Act
    has_attach = hasattr(_mcp, "_attach_unified_start")
    # Assert
    assert has_attach


# EOF
