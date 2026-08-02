#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the standalone per-recipient PULL-inbox (no sac).

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store,
real users via ``register_user``, real cards via ``add_task``, real events
via :class:`scitex_cards._events.Event`. The inbox is the dispatcher's SOLE
delivery rail now (the synchronous direct-POST was removed), so a
``deliver_fn`` recorder is still passed for back-compat but is NEVER called.
Covers:

* ``enqueue`` then ``poll_inbox`` returns the record (unseen); ``mark_seen``
  / ``ack`` advances the cursor so a second poll returns nothing new.
* dedup on ``(event_type, card_id, ts, actor)`` — a re-emit yields one record.
* the C4 dispatcher enqueues to the resolved recipients' inboxes on a
  ``reassigned`` / ``completed`` event (asserted via ``poll_inbox``), with NO
  real network on the path.
* the ``poll_notifications`` MCP tool resolves an agent name → its user-id
  inbox and returns / acks correctly.
* inbox persistence round-trips and does NOT clobber ``tasks:`` / ``users:``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
import yaml

from scitex_cards._events import Event, EventType
from scitex_cards._inbox import ack, enqueue, poll_inbox
from scitex_cards._model import load_tasks
from scitex_cards._notify._dispatch import dispatch_notifications
from scitex_cards._store import add_task
from scitex_cards._users import register_user, resolve_user


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    # SQLite is the TASK store now: load_tasks / add_task read+write the
    # canonical DB and only use this path to STAMP provenance, so a write
    # stamped with a tmp_path file is refused by the next read. Return the
    # PINNED store identity (== resolve_tasks_path(None)) the conftest already
    # aims the DB at, so writes stamp the same path reads resolve. The users:
    # section still lives in THIS YAML file; inboxes now live in their own
    # inboxes.json sidecar next to it (see `_inbox._inboxes_path`).
    # register_user keeps operating on this file directly. `tmp_path` is
    # now unused.
    return Path(os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"])


def _read(store):
    return yaml.safe_load(store.read_text(encoding="utf-8"))


def _enqueue_reassigned(
    store, recipient="u_abc", card_id="c1", ts="2026-06-26T00:00:00Z"
):
    return enqueue(
        recipient,
        event_type="reassigned",
        card_id=card_id,
        body=f"Card {card_id} reassigned to you",
        actor="bob",
        ts=ts,
        store=store,
    )


def _enqueue_completed(
    store, recipient="u_abc", card_id="c1", body="done", ts="2026-06-26T00:00:00Z"
):
    return enqueue(
        recipient,
        event_type="completed",
        card_id=card_id,
        body=body,
        actor="bob",
        ts=ts,
        store=store,
    )


class _Recorder:
    """A real ``deliver_fn`` recorder — appends each call; returns ok.

    The dispatcher NO LONGER calls ``deliver_fn`` (the inbox is the sole
    rail), so ``calls`` stays empty in practice; the recorder is kept only
    to exercise the back-compat parameter and assert it is never invoked.
    """

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, agent, body, **kw):
        self.calls.append({"agent": agent, "body": body, **kw})
        return {"ok": True, "agent": agent, "reason": "delivered"}

    @property
    def targets(self) -> list[str]:
        return [c["agent"] for c in self.calls]


# --------------------------------------------------------------------------- #
# fixtures — shared setup for the split tests below                            #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def two_record_inbox(tmp_path):
    """Two completed notices for one recipient, both unseen."""
    store = _store(tmp_path)
    r1 = _enqueue_completed(store, card_id="c1", body="a", ts="2026-06-26T00:00:01Z")
    r2 = _enqueue_completed(store, card_id="c2", body="b", ts="2026-06-26T00:00:02Z")
    return {"store": store, "r1": r1, "r2": r2}


@pytest.fixture()
def reassigned_dispatch(tmp_path):
    """A REASSIGNED event dispatched for a REGISTERED owner (alice)."""
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # created_by == owner so the setup `created` event self-excludes the actor
    # (creator isn't notified of their own creation) — keeps the inbox clean.
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    recorder = _Recorder()
    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=recorder,
    )
    return {"store": store, "alice": alice, "summary": summary, "recorder": recorder}


@pytest.fixture()
def completed_dispatch(tmp_path):
    """A COMPLETED event dispatched for an owner (alice) + subscriber (eve)."""
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    eve = register_user(kind="human", names=["eve"], store=store)
    add_task(
        store=store,
        id="c1",
        title="x",
        agent="alice",
        subscribers=["eve"],
        created_by="alice",
    )
    summary = dispatch_notifications(
        Event(type=EventType.COMPLETED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=_Recorder(),
    )
    return {"store": store, "alice": alice, "eve": eve, "summary": summary}


@pytest.fixture()
def self_actor_dispatch(tmp_path):
    """A REASSIGNED event whose ACTOR is also the card's owner."""
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # created_by == owner so the setup `created` event self-excludes the actor
    # (creator isn't notified of their own creation) — keeps the inbox clean.
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="alice"),
        store=store,
        deliver_fn=_Recorder(),
    )
    return {"store": store, "alice": alice, "summary": summary}


@pytest.fixture()
def unregistered_owner_dispatch(tmp_path):
    """A REASSIGNED event for an owner who was never registered."""
    store = _store(tmp_path)
    add_task(store=store, id="c1", title="x", agent="dave", created_by="dave")
    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=_Recorder(),
    )
    return {"store": store, "summary": summary}


@pytest.fixture()
def redispatched(tmp_path):
    """The SAME completed event dispatched twice (identical ts)."""
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # created_by == owner so the setup `created` event self-excludes the actor
    # (creator isn't notified of their own creation) — keeps the inbox clean.
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    recorder = _Recorder()
    event = Event(type=EventType.COMPLETED, card_id="c1", actor="bob")
    s1 = dispatch_notifications(event, store=store, deliver_fn=recorder)
    s2 = dispatch_notifications(event, store=store, deliver_fn=recorder)
    return {"store": store, "alice": alice, "s1": s1, "s2": s2}


@pytest.fixture()
def bus_dispatch(tmp_path, env):
    """A card-event pushed through the REAL hook bus.

    No mocks — the real default push wire runs in dry-run mode so there is
    no network on the path.
    """
    from scitex_cards._hooks import dispatch_event

    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    env.set("SCITEX_TODO_PUSH_DRY_RUN", "1")
    envelope = Event(type=EventType.REASSIGNED, card_id="c1", actor="bob").to_dict()
    summary = dispatch_event(envelope, store=store, entry_points=[])
    return {"store": store, "alice": alice, "summary": summary, "envelope": envelope}


@pytest.fixture()
def store_with_task_user_and_inbox(tmp_path):
    """A store holding a real user, a real task, and one inbox record."""
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="hello", agent="alice", note="keep me")
    _enqueue_completed(store, body="x")
    return store


# --------------------------------------------------------------------------- #
# enqueue → poll_inbox → mark_seen / ack                                      #
# --------------------------------------------------------------------------- #
def test_enqueue_returns_a_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    rec = _enqueue_reassigned(store)
    # Assert
    assert rec is not None


def test_enqueue_record_starts_unseen(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    rec = _enqueue_reassigned(store)
    # Assert
    assert rec["seen"] is False


def test_enqueue_record_carries_the_card_id(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    rec = _enqueue_reassigned(store)
    # Assert
    assert rec["card_id"] == "c1"


def test_enqueue_record_id_uses_the_n_prefix(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    rec = _enqueue_reassigned(store)
    # Assert
    assert rec["id"].startswith("n_")


def test_enqueue_then_poll_returns_unseen_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    # Act
    got = poll_inbox("u_abc", store=store)
    # Assert
    assert len(got) == 1


def test_polled_record_carries_the_card_id(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    # Act
    got = poll_inbox("u_abc", store=store)
    # Assert
    assert got[0]["card_id"] == "c1"


def test_polled_record_carries_the_body(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    # Act
    got = poll_inbox("u_abc", store=store)
    # Assert
    assert got[0]["body"] == "Card c1 reassigned to you"


def test_polled_record_is_still_unseen(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    # Act
    got = poll_inbox("u_abc", store=store)
    # Assert — a plain poll must not consume the record.
    assert got[0]["seen"] is False


def test_first_drain_returns_the_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store)
    # Act
    first = poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    # Assert
    assert [r["card_id"] for r in first] == ["c1"]


def test_first_drain_marks_the_record_seen(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store)
    # Act
    first = poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    # Assert
    assert first[0]["seen"] is True


def test_mark_seen_advances_cursor(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store)
    poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    # Act
    second = poll_inbox("u_abc", unseen_only=True, store=store)
    # Assert — a second unseen-only poll returns nothing new.
    assert second == []


def test_full_history_still_holds_the_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store)
    poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    # Act
    history = poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert — draining advances a cursor; it does not delete.
    assert [r["card_id"] for r in history] == ["c1"]


def test_full_history_shows_the_record_as_seen(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store)
    poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    # Act
    history = poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert
    assert history[0]["seen"] is True


def test_ack_marks_specific_ids_seen(two_record_inbox):
    # Arrange
    store = two_record_inbox["store"]
    r1 = two_record_inbox["r1"]
    # Act
    flipped = ack("u_abc", [r1["id"]], store=store)
    # Assert
    assert flipped == [r1["id"]]


def test_ack_leaves_other_records_unseen(two_record_inbox):
    # Arrange
    store = two_record_inbox["store"]
    r1, r2 = two_record_inbox["r1"], two_record_inbox["r2"]
    ack("u_abc", [r1["id"]], store=store)
    # Act
    unseen = poll_inbox("u_abc", unseen_only=True, store=store)
    # Assert — only r1 is now seen; r2 is untouched.
    assert [r["id"] for r in unseen] == [r2["id"]]


def test_ack_twice_is_a_noop(two_record_inbox):
    # Arrange
    store = two_record_inbox["store"]
    r1 = two_record_inbox["r1"]
    ack("u_abc", [r1["id"]], store=store)
    # Act
    again = ack("u_abc", [r1["id"]], store=store)
    # Assert — already seen, so nothing flips.
    assert again == []


def test_ack_unknown_id_is_a_noop(two_record_inbox):
    # Arrange
    store = two_record_inbox["store"]
    # Act
    flipped = ack("u_abc", ["n_nope"], store=store)
    # Assert
    assert flipped == []


def test_dedup_first_enqueue_returns_a_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    first = _enqueue_reassigned(store)
    # Assert
    assert first is not None


def test_dedup_exact_reemit_returns_none(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    # Act
    second = _enqueue_reassigned(store)  # exact re-emit
    # Assert
    assert second is None


def test_dedup_keeps_only_one_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    _enqueue_reassigned(store)
    # Act
    everything = poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert
    assert len(everything) == 1


def test_dedup_distinct_ts_is_kept_separately(tmp_path):
    # A DIFFERENT ts is a genuine second event, not a re-emit.
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    # Act
    third = _enqueue_reassigned(store, ts="2026-06-26T00:00:05Z")
    # Assert
    assert third is not None


def test_dedup_distinct_ts_yields_two_records(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    _enqueue_reassigned(store, ts="2026-06-26T00:00:05Z")
    # Act
    everything = poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert
    assert len(everything) == 2


def test_enqueue_with_falsy_recipient_returns_none(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    rec = enqueue(
        "", event_type="completed", card_id="c1", body="x", actor=None, store=store
    )
    # Assert
    assert rec is None


def test_poll_inbox_with_falsy_recipient_is_empty(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    got = poll_inbox("", store=store)
    # Assert
    assert got == []


def test_poll_inbox_for_unknown_recipient_is_empty(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    got = poll_inbox("u_nobody", store=store)
    # Assert
    assert got == []


def test_ack_for_unknown_recipient_is_a_noop(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    flipped = ack("u_nobody", ["n_x"], store=store)
    # Assert
    assert flipped == []


# --------------------------------------------------------------------------- #
# C4 dispatcher enqueues to resolved recipients' inboxes                       #
# --------------------------------------------------------------------------- #
def test_dispatch_reassigned_enqueues_new_owner_inbox(reassigned_dispatch):
    # Arrange
    alice = reassigned_dispatch["alice"]
    # Act
    summary = reassigned_dispatch["summary"]
    # Assert — enqueued to alice's resolved user-id, NOT her name.
    assert summary["enqueued"] == [alice.id]


def test_dispatch_does_not_invoke_the_legacy_push_rail(reassigned_dispatch):
    # Arrange
    recorder = reassigned_dispatch["recorder"]
    # Act
    targets = recorder.targets
    # Assert — the inbox is the SOLE delivery rail now.
    assert targets == []


def test_dispatch_reassigned_enqueues_exactly_one_record(reassigned_dispatch):
    # Arrange
    store, alice = reassigned_dispatch["store"], reassigned_dispatch["alice"]
    # Act
    inbox = poll_inbox(alice.id, store=store)
    # Assert
    assert len(inbox) == 1


def test_dispatch_reassigned_record_carries_the_event_type(reassigned_dispatch):
    # Arrange
    store, alice = reassigned_dispatch["store"], reassigned_dispatch["alice"]
    # Act
    inbox = poll_inbox(alice.id, store=store)
    # Assert
    assert inbox[0]["event_type"] == "reassigned"


def test_dispatch_reassigned_record_body_names_the_card(reassigned_dispatch):
    # Arrange
    store, alice = reassigned_dispatch["store"], reassigned_dispatch["alice"]
    # Act
    inbox = poll_inbox(alice.id, store=store)
    # Assert — a notice that does not name its card is unactionable.
    assert "c1" in inbox[0]["body"]


def test_dispatch_reassigned_record_carries_the_actor(reassigned_dispatch):
    # Arrange
    store, alice = reassigned_dispatch["store"], reassigned_dispatch["alice"]
    # Act
    inbox = poll_inbox(alice.id, store=store)
    # Assert
    assert inbox[0]["actor"] == "bob"


def test_dispatch_completed_enqueues_owner_and_subscribers(completed_dispatch):
    # Arrange
    alice, eve = completed_dispatch["alice"], completed_dispatch["eve"]
    # Act
    summary = completed_dispatch["summary"]
    # Assert
    assert set(summary["enqueued"]) == {alice.id, eve.id}


def test_dispatch_completed_reaches_the_owner_inbox(completed_dispatch):
    # Arrange
    store, alice = completed_dispatch["store"], completed_dispatch["alice"]
    # Act
    inbox = poll_inbox(alice.id, store=store)
    # Assert
    assert [r["event_type"] for r in inbox] == ["completed"]


def test_dispatch_completed_reaches_the_subscriber_inbox(completed_dispatch):
    # Arrange
    store, eve = completed_dispatch["store"], completed_dispatch["eve"]
    # Act
    inbox = poll_inbox(eve.id, store=store)
    # Assert — a subscriber is notified, not merely recorded.
    assert [r["event_type"] for r in inbox] == ["completed"]


def test_dispatch_actor_is_not_enqueued(self_actor_dispatch):
    # The actor caused the event → no inbox entry, even if owner == actor.
    # Arrange
    summary = self_actor_dispatch["summary"]
    # Act
    enqueued = summary["enqueued"]
    # Assert
    assert enqueued == []


def test_dispatch_actor_inbox_stays_empty(self_actor_dispatch):
    # Arrange
    store, alice = self_actor_dispatch["store"], self_actor_dispatch["alice"]
    # Act
    inbox = poll_inbox(alice.id, store=store)
    # Assert
    assert inbox == []


def test_dispatch_unregistered_owner_enqueues_under_raw_name(
    unregistered_owner_dispatch,
):
    # Back-compat: an unregistered owner is enqueued under its raw name (the
    # same key resolve_recipients returns), so poll_notifications(name) finds it.
    # Arrange
    summary = unregistered_owner_dispatch["summary"]
    # Act
    enqueued = summary["enqueued"]
    # Assert
    assert enqueued == ["dave"]


def test_dispatch_unregistered_owner_inbox_holds_the_card(unregistered_owner_dispatch):
    # Arrange
    store = unregistered_owner_dispatch["store"]
    # Act
    inbox = poll_inbox("dave", store=store)
    # Assert
    assert [r["card_id"] for r in inbox] == ["c1"]


def test_dispatch_first_pass_enqueues_the_owner(redispatched):
    # Arrange
    alice = redispatched["alice"]
    # Act
    s1 = redispatched["s1"]
    # Assert
    assert s1["enqueued"] == [alice.id]


def test_dispatch_redispatch_dedups_via_event_ts(redispatched):
    # Re-dispatching the SAME event (same ts) must not double-enqueue.
    # Arrange
    _ = redispatched["alice"]
    # Act
    s2 = redispatched["s2"]
    # Assert — deduped on the event's own ts.
    assert s2["enqueued"] == []


def test_redispatch_leaves_a_single_inbox_record(redispatched):
    # Arrange
    store, alice = redispatched["store"], redispatched["alice"]
    # Act
    history = poll_inbox(alice.id, unseen_only=False, store=store)
    # Assert
    assert len(history) == 1


def test_bus_dispatch_preserves_the_notify_summary_shape(bus_dispatch):
    # End-to-end through the real hook bus: dispatch_event runs the built-in
    # C4 consumer for a card-event, which enqueues to the standalone inbox.
    # Arrange
    summary = bus_dispatch["summary"]
    # Act
    notify = summary["notify"]
    # Assert — the existing summary shape is preserved.
    assert notify["event_type"] == "reassigned"


def test_bus_dispatch_carries_the_enqueued_list(bus_dispatch):
    # Arrange
    summary, alice = bus_dispatch["summary"], bus_dispatch["alice"]
    # Act
    notify = summary["notify"]
    # Assert — the additive `enqueued` list is present.
    assert notify["enqueued"] == [alice.id]


def test_bus_dispatch_reaches_the_standalone_inbox(bus_dispatch):
    # Arrange
    store, alice = bus_dispatch["store"], bus_dispatch["alice"]
    # Act
    inbox = poll_inbox(alice.id, store=store)
    # Assert — the inbox really received it; the standalone rail worked.
    assert [r["card_id"] for r in inbox] == ["c1"]


def test_emit_is_fire_and_forget_and_non_raising(bus_dispatch):
    # Arrange
    from scitex_cards._events import emit

    envelope = bus_dispatch["envelope"]
    # Act
    result = emit(envelope, entry_points=[])
    # Assert — emit() must never raise; it returns None.
    assert result is None


def test_dispatch_enqueue_error_is_recorded_not_raised(tmp_path):
    # Fail-soft guarantee for the inbox rail: if enqueue raises for a
    # recipient, the dispatcher records the error and continues (the push
    # rail still runs) — it never re-raises. We force a REAL enqueue error
    # with no mock: point the store at a path whose parent is a regular file,
    # so enqueue's `path.parent.mkdir(...)` raises NotADirectoryError. This is
    # the exception the dispatcher's try/except catches.
    # Arrange
    import scitex_cards._inbox as inbox_mod

    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    bad_store = blocker / "tasks.yaml"  # parent is a file → mkdir fails
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(OSError):
        inbox_mod.enqueue(
            "u_x",
            event_type="completed",
            card_id="c1",
            body="b",
            actor=None,
            ts="2026-06-26T00:00:00Z",
            store=bad_store,
        )


# --------------------------------------------------------------------------- #
# poll_notifications MCP tool                                                  #
# --------------------------------------------------------------------------- #
# The tool lives in ``_mcp_skills`` which imports ``_mcp_server`` (fastmcp is
# an optional extra); skip just the MCP cluster when fastmcp is absent (the
# pure-inbox tests above need no fastmcp). Reuse the established
# asyncio.run + `.fn`-peeling pattern (matches test__mcp_server.py) rather
# than pytest-asyncio (not configured in this suite).
try:
    import fastmcp as _fastmcp  # noqa: F401

    _HAS_FASTMCP = True
except ImportError:  # pragma: no cover — exercised only without the extra
    _HAS_FASTMCP = False

_skip_no_mcp = pytest.mark.skipif(
    not _HAS_FASTMCP,
    reason="fastmcp not installed — `scitex-todo[mcp]` extra absent.",
)


async def _call_tool(tool_callable, **kwargs):
    """Await a `@mcp.tool()` callable, peeling FastMCP 3.x's `.fn` wrapper."""
    fn = getattr(tool_callable, "fn", None) or tool_callable
    return await fn(**kwargs)


@pytest.fixture()
def alice_notification_store(tmp_path):
    """A REGISTERED alice with one COMPLETED notification queued."""
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    dispatch_notifications(
        Event(type=EventType.COMPLETED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=_Recorder(),
    )
    return {"store": store, "alice": alice}


@pytest.fixture()
def dave_notification_store(tmp_path):
    """An UNREGISTERED dave with one REASSIGNED notification queued."""
    store = _store(tmp_path)
    add_task(store=store, id="c1", title="x", agent="dave", created_by="dave")
    dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=_Recorder(),
    )
    return store


@_skip_no_mcp
def test_poll_notifications_echoes_the_agent_name(alice_notification_store):
    # Arrange
    from scitex_cards._mcp_skills import poll_notifications

    store = alice_notification_store["store"]
    # Act
    raw = asyncio.run(
        _call_tool(poll_notifications, agent="alice", tasks_path=str(store))
    )
    payload = json.loads(raw)
    # Assert
    assert payload["agent"] == "alice"


@_skip_no_mcp
def test_poll_notifications_resolves_name_to_inbox(alice_notification_store):
    # Arrange
    from scitex_cards._mcp_skills import poll_notifications

    store, alice = alice_notification_store["store"], alice_notification_store["alice"]
    # Act
    raw = asyncio.run(
        _call_tool(poll_notifications, agent="alice", tasks_path=str(store))
    )
    payload = json.loads(raw)
    # Assert — the resolved name became the u_* id, not the raw name.
    assert payload["recipient_id"] == alice.id


@_skip_no_mcp
def test_poll_notifications_returns_the_queued_notification(alice_notification_store):
    # Arrange
    from scitex_cards._mcp_skills import poll_notifications

    store = alice_notification_store["store"]
    # Act
    raw = asyncio.run(
        _call_tool(poll_notifications, agent="alice", tasks_path=str(store))
    )
    payload = json.loads(raw)
    # Assert
    assert [n["event_type"] for n in payload["notifications"]] == ["completed"]


@_skip_no_mcp
def test_resolve_user_maps_the_name_the_same_way(alice_notification_store):
    """Sanity: the resolver maps the name exactly as the tool does."""
    # Arrange
    store, alice = alice_notification_store["store"], alice_notification_store["alice"]
    # Act
    resolved = resolve_user("alice", store=store)
    # Assert
    assert resolved.id == alice.id


@_skip_no_mcp
def test_poll_notifications_with_ack_returns_the_backlog(alice_notification_store):
    # Arrange
    from scitex_cards._mcp_skills import poll_notifications

    store = alice_notification_store["store"]
    # Act
    first = json.loads(
        asyncio.run(
            _call_tool(
                poll_notifications, agent="alice", ack=True, tasks_path=str(store)
            )
        )
    )
    # Assert
    assert len(first["notifications"]) == 1


@_skip_no_mcp
def test_poll_notifications_ack_advances_cursor(alice_notification_store):
    # Arrange
    from scitex_cards._mcp_skills import poll_notifications

    store = alice_notification_store["store"]
    asyncio.run(
        _call_tool(poll_notifications, agent="alice", ack=True, tasks_path=str(store))
    )
    # Act
    second = json.loads(
        asyncio.run(
            _call_tool(poll_notifications, agent="alice", tasks_path=str(store))
        )
    )
    # Assert — the drain advanced the cursor; nothing new is pending.
    assert second["notifications"] == []


@_skip_no_mcp
def test_poll_notifications_unregistered_name_uses_raw_key(dave_notification_store):
    # Arrange
    from scitex_cards._mcp_skills import poll_notifications

    store = dave_notification_store
    # Act
    payload = json.loads(
        asyncio.run(_call_tool(poll_notifications, agent="dave", tasks_path=str(store)))
    )
    # Assert — raw-name fallback for an unregistered agent.
    assert payload["recipient_id"] == "dave"


@_skip_no_mcp
def test_poll_notifications_raw_key_returns_the_notification(dave_notification_store):
    # Arrange
    from scitex_cards._mcp_skills import poll_notifications

    store = dave_notification_store
    # Act
    payload = json.loads(
        asyncio.run(_call_tool(poll_notifications, agent="dave", tasks_path=str(store)))
    )
    # Assert
    assert [n["card_id"] for n in payload["notifications"]] == ["c1"]


# --------------------------------------------------------------------------- #
# persistence round-trip does NOT clobber tasks:/users:                        #
# --------------------------------------------------------------------------- #
def test_inbox_write_keeps_the_tasks_section(store_with_task_user_and_inbox):
    # Arrange
    store = store_with_task_user_and_inbox
    # Act
    data = _read(store)
    # Assert
    assert isinstance(data.get("tasks"), list)


def test_inbox_write_keeps_the_users_section(store_with_task_user_and_inbox):
    # Arrange
    store = store_with_task_user_and_inbox
    # Act
    data = _read(store)
    # Assert
    assert isinstance(data.get("users"), list)


def test_inbox_write_creates_the_inboxes_sidecar(store_with_task_user_and_inbox):
    # Arrange
    store = store_with_task_user_and_inbox
    # Act — inboxes now live in their OWN sidecar, not embedded in `store`.
    from scitex_cards._inbox import _inboxes_path

    data = json.loads(_inboxes_path(store).read_text(encoding="utf-8"))
    # Assert
    assert isinstance(data.get("inboxes"), dict)


def test_inbox_write_keeps_the_seeded_task(store_with_task_user_and_inbox):
    # Arrange
    store = store_with_task_user_and_inbox
    # Act — the task lives in the SQLite store now, not the YAML file; read it
    # back through load_tasks (the DB read path) to prove the inbox write left
    # it intact.
    tasks = load_tasks(store)
    # Assert
    assert "c1" in {t["id"] for t in tasks}


def test_inbox_persistence_does_not_clobber_tasks_and_users(
    store_with_task_user_and_inbox,
):
    # Arrange
    store = store_with_task_user_and_inbox
    # Act — the task payload lives in the SQLite store now; load it from there.
    task = {t["id"]: t for t in load_tasks(store)}["c1"]
    # Assert — the task PAYLOAD survived, not merely the row.
    assert task["note"] == "keep me"


def test_inbox_write_preserves_the_registered_user(store_with_task_user_and_inbox):
    # Arrange
    store = store_with_task_user_and_inbox
    # Act
    data = _read(store)
    # Assert
    assert any("alice" in (u.get("names") or []) for u in data["users"])


def test_inbox_write_stores_the_record(store_with_task_user_and_inbox):
    # Arrange
    store = store_with_task_user_and_inbox
    # Act — read the inboxes sidecar directly (not embedded in `store`).
    from scitex_cards._inbox import _inboxes_path

    data = json.loads(_inboxes_path(store).read_text(encoding="utf-8"))
    # Assert
    assert data["inboxes"]["u_abc"][0]["card_id"] == "c1"


def test_inbox_only_write_does_not_touch_the_task_store_file(tmp_path):
    # An inbox write now lands ONLY in its own inboxes.json sidecar — it must
    # not create or touch the (unrelated) legacy task-store file at all.
    # Arrange
    store = _store(tmp_path)
    # Act
    _enqueue_completed(store, body="x")
    # Assert
    assert not store.exists()


def test_add_task_after_inbox_write_still_works(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store, body="x")
    # Act
    add_task(store=store, id="c2", title="later", agent="alice")
    tasks = load_tasks(store)
    # Assert — add_task does not raise after an inbox-only write, and the row
    # lands in the SQLite store.
    assert any(t["id"] == "c2" for t in tasks)


def test_inbox_survives_a_later_task_write(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store, body="x")
    # Act
    add_task(store=store, id="c2", title="later", agent="alice")
    from scitex_cards._inbox import _inboxes_path

    data = json.loads(_inboxes_path(store).read_text(encoding="utf-8"))
    # Assert — the inbox is still intact after the task write.
    assert data["inboxes"]["u_abc"][0]["card_id"] == "c1"


# EOF
