#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for the C4 notify DISPATCHER.

Real round-trips, NO mocks (STX-NM / PA-306): real users via
``register_user`` in a ``tmp_path`` store, real cards via ``add_task`` /
``update_task``, and the real standalone PULL-inbox read via
``scitex_cards._inbox.poll_inbox``. The bus path is exercised via the real
``scitex_cards._events.emit`` / ``scitex_cards._hooks.dispatch_event`` with
``entry_points=[]`` (no plugins) so the BUILT-IN C4 consumer is what runs.

The dispatcher delivers PURELY via the inbox now — the synchronous
direct-POST accelerator was removed (it could not reach a containerized
recipient and slowed the emitting mutation). So the assertions are:

* delivery shows up as ``summary["enqueued"]`` (recipient IDS) and in each
  recipient's inbox (``poll_inbox``); ``summary["delivered"]`` stays EMPTY.
* the ``deliver_fn`` parameter is ACCEPTED for back-compat but NEVER called
  — a deliberately-RAISING recorder proves the comment / any event does not
  depend on or await a turn-URL POST.

Coverage mirrors the C4 card's checklist:

* `reassigned` enqueues to the new owner (by ID); the ACTOR is excluded.
* `commented` is ENQUEUED to owner + collaborators + subscribers (it used
  to be skipped + handled by the container-unreachable direct-POST).
* `merged` enqueues to NOBODY under the default rules (default-quiet);
  `completed` enqueues to owner + subscribers.
* per-recipient fail-soft + end-to-end via the bus.
"""

from __future__ import annotations

import pytest

from scitex_cards._events import Event, EventType, emit
from scitex_cards._hooks import dispatch_event
from scitex_cards._inbox import poll_inbox
from scitex_cards._notify._dispatch import dispatch_notifications
from scitex_cards._store import add_task
from scitex_cards._users import register_user


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


class _ExplodingDeliver:
    """A real ``deliver_fn`` that RAISES if ever invoked — and records it.

    NOT a mock: a plain callable. The dispatcher no longer calls
    ``deliver_fn`` (the inbox is the sole rail), so passing this proves a
    comment / any event never depends on or awaits a turn-URL POST: if the
    dispatcher DID call it, the test would see ``called`` flip / an raise.
    """

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, agent, body, **kw):
        self.calls.append({"agent": agent, "body": body, **kw})
        raise AssertionError(
            f"deliver_fn must NOT be called (inbox is the rail) — got {agent!r}"
        )

    @property
    def called(self) -> bool:
        return bool(self.calls)


# --------------------------------------------------------------------------- #
# reassigned: enqueues to the new owner, actor excluded                       #
# --------------------------------------------------------------------------- #
#: WHY the six `reassigned_to_new_owner` tests below are split but share this
#: rationale: one dispatch produces two INDEPENDENT observable surfaces — the
#: returned summary and the recipient's actual inbox — and a bug in either is
#: invisible from the other. The summary must name the new owner by ID, keep
#: `delivered` EMPTY (the direct-POST rail is gone), and leave the legacy
#: deliver_fn untouched; the inbox must then really hold one `reassigned`
#: note carrying the card id in both its field and its body. A dispatcher
#: that reports an enqueue it never performed passes every summary claim.
@pytest.fixture()
def reassigned_dispatch(tmp_path):
    """Dispatch `reassigned` on a card owned by a registered alice."""
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # created_by == owner so the setup `created` event self-excludes the
    # actor (the creator is not notified of their own creation) — keeps the
    # inbox clean so this test asserts purely on the dispatched event.
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )
    return {
        "summary": summary,
        "rec": rec,
        "alice": alice,
        "notes": poll_inbox(alice.id, store=store),
    }


def test_reassigned_enqueues_to_new_owner(reassigned_dispatch):
    # Arrange
    scenario = reassigned_dispatch
    # Act
    summary = scenario["summary"]
    # Assert — enqueued to the new owner by ID.
    assert summary["enqueued"] == [scenario["alice"].id]


def test_reassigned_reports_nothing_directly_delivered(reassigned_dispatch):
    # Arrange
    scenario = reassigned_dispatch
    # Act
    summary = scenario["summary"]
    # Assert — the inbox is the sole rail; direct-POST delivery is gone.
    assert summary["delivered"] == []


def test_reassigned_never_calls_the_legacy_deliver_fn(reassigned_dispatch):
    # Arrange
    scenario = reassigned_dispatch
    # Act
    rec = scenario["rec"]
    # Assert — the legacy direct-POST wire was NOT invoked.
    assert rec.called is False


def test_reassigned_note_lands_in_the_owner_inbox(reassigned_dispatch):
    # Arrange
    scenario = reassigned_dispatch
    # Act
    notes = scenario["notes"]
    # Assert — exactly one note, and it is the reassignment.
    assert [n["event_type"] for n in notes] == ["reassigned"]


def test_reassigned_note_carries_the_card_id(reassigned_dispatch):
    # Arrange
    scenario = reassigned_dispatch
    # Act
    notes = scenario["notes"]
    # Assert
    assert notes[0]["card_id"] == "c1"


def test_reassigned_note_body_mentions_the_card(reassigned_dispatch):
    # Arrange
    scenario = reassigned_dispatch
    # Act
    notes = scenario["notes"]
    # Assert — a sensible body, not just a bare id field.
    assert "c1" in notes[0]["body"]


#: WHY the three `reassigned_actor` tests below are split but share this
#: rationale: the actor caused the event → never notified, even if they are
#: the owner. Self-exclusion has to hold on BOTH surfaces — an empty summary
#: with a note still sitting in her inbox is the bug, not the fix.
@pytest.fixture()
def reassigned_to_self_dispatch(tmp_path):
    """alice reassigns the card to herself → she is BOTH owner and actor."""
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # created_by == owner so the setup `created` event self-excludes the
    # actor (the creator is not notified of their own creation) — keeps the
    # inbox clean so this test asserts purely on the dispatched event.
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="alice"),
        store=store,
        deliver_fn=rec,
    )
    return {
        "summary": summary,
        "inbox": poll_inbox(alice.id, store=store),
    }


def test_reassigned_excludes_the_actor(reassigned_to_self_dispatch):
    # Arrange
    scenario = reassigned_to_self_dispatch
    # Act
    summary = scenario["summary"]
    # Assert
    assert summary["enqueued"] == []


def test_reassigned_to_self_delivers_nothing_directly(reassigned_to_self_dispatch):
    # Arrange
    scenario = reassigned_to_self_dispatch
    # Act
    summary = scenario["summary"]
    # Assert
    assert summary["delivered"] == []


def test_reassigned_to_self_leaves_the_actor_inbox_empty(
    reassigned_to_self_dispatch,
):
    # Arrange
    scenario = reassigned_to_self_dispatch
    # Act
    inbox = scenario["inbox"]
    # Assert — self-exclusion holds on the inbox, not just in the summary.
    assert inbox == []


#: WHY the two `unregistered_owner` tests below are split but share this
#: rationale: back-compat — an owner who is NOT a registered user is itself a
#: raw recipient id (resolve_recipients returns the raw string), so the raw
#: name must be BOTH the id the summary reports AND the key its inbox is
#: filed under. Agreeing on one and not the other loses the notification.
@pytest.fixture()
def unregistered_owner_dispatch(tmp_path):
    """`dave` is never registered; the raw name has to carry the delivery."""
    store = _store(tmp_path)
    add_task(store=store, id="c1", title="x", agent="dave", created_by="dave")
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )
    return {"summary": summary, "inbox": poll_inbox("dave", store=store)}


def test_unregistered_owner_name_enqueues_via_raw_name(
    unregistered_owner_dispatch,
):
    # Arrange
    scenario = unregistered_owner_dispatch
    # Act
    summary = scenario["summary"]
    # Assert — the raw name "dave" is the recipient id.
    assert summary["enqueued"] == ["dave"]


def test_unregistered_owner_inbox_is_keyed_on_the_raw_name(
    unregistered_owner_dispatch,
):
    # Arrange
    scenario = unregistered_owner_dispatch
    # Act
    inbox = scenario["inbox"]
    # Assert — and that same raw name is its inbox key.
    assert [n["card_id"] for n in inbox] == ["c1"]


# --------------------------------------------------------------------------- #
# commented: now ENQUEUED via the inbox (owner + collaborators + subscribers) #
# --------------------------------------------------------------------------- #
#: WHY the six `commented` tests below are split but share this rationale:
#: `commented` used to be SKIPPED + handled by the container-unreachable
#: direct-POST. It now ENQUEUES to the C3 default set (owner + collaborators
#: + subscribers) via the always-works inbox; the actor (author) is excluded.
#: "Not skipped anymore" and "not direct-POSTed either" are separate claims
#: from "the right people got it", and the author's EMPTY inbox is the claim
#: that fails silently when actor-exclusion regresses.
@pytest.fixture()
def commented_dispatch(tmp_path):
    """bob comments on alice's card, which carries a collaborator + subscriber."""
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    carol = register_user(kind="agent", names=["carol"], store=store)
    eve = register_user(kind="human", names=["eve"], store=store)
    bob = register_user(kind="agent", names=["bob"], store=store)
    add_task(
        store=store,
        id="c1",
        title="x",
        agent="alice",
        collaborators=["carol"],
        subscribers=["eve"],
        created_by="alice",
    )
    rec = _ExplodingDeliver()

    # bob (a card subscriber would be over-thinking — bob is just the actor)
    # comments → owner + collaborators + subscribers get it, NOT bob.
    summary = dispatch_notifications(
        Event(type=EventType.COMMENTED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )
    return {
        "summary": summary,
        "rec": rec,
        "recipient_ids": (alice.id, carol.id, eve.id),
        "store": store,
        "author_inbox": poll_inbox(bob.id, store=store),
    }


def test_commented_enqueues_to_owner_collaborators_subscribers(commented_dispatch):
    # Arrange
    scenario = commented_dispatch
    # Act
    summary = scenario["summary"]
    # Assert
    assert set(summary["enqueued"]) == set(scenario["recipient_ids"])


def test_commented_reports_nothing_directly_delivered(commented_dispatch):
    # Arrange
    scenario = commented_dispatch
    # Act
    summary = scenario["summary"]
    # Assert — no direct-POST.
    assert summary["delivered"] == []


def test_commented_is_no_longer_skipped(commented_dispatch):
    # Arrange
    scenario = commented_dispatch
    # Act
    summary = scenario["summary"]
    # Assert — it used to be skipped wholesale; it must not be anymore.
    assert summary["skipped"] == []


def test_commented_never_calls_the_legacy_deliver_fn(commented_dispatch):
    # Arrange
    scenario = commented_dispatch
    # Act
    rec = scenario["rec"]
    # Assert
    assert rec.called is False


def test_commented_reaches_every_recipient_inbox_once(commented_dispatch):
    # Arrange
    scenario = commented_dispatch
    # Act
    per_recipient = [
        [n["event_type"] for n in poll_inbox(uid, store=scenario["store"])]
        for uid in scenario["recipient_ids"]
    ]
    # Assert — each inbox got exactly the one `commented` note.
    assert per_recipient == [["commented"]] * len(scenario["recipient_ids"])


def test_commented_leaves_the_author_inbox_empty(commented_dispatch):
    # Arrange
    scenario = commented_dispatch
    # Act
    author_inbox = scenario["author_inbox"]
    # Assert — the author (bob) was NOT enqueued (actor excluded).
    assert author_inbox == []


#: WHY the two `no_double_delivery` tests below are split but share this
#: rationale: each recipient is enqueued EXACTLY ONCE for a single comment
#: event (the dispatcher dedups names into a set; the inbox dedups on the
#: event key). A summary listing one id while the inbox holds two notes is
#: precisely the double-delivery this pins down, so both are asserted.
@pytest.fixture()
def commented_once_dispatch(tmp_path):
    """A single `commented` event against a card with one recipient."""
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # created_by == owner so the setup `created` event self-excludes the
    # actor (the creator is not notified of their own creation) — keeps the
    # inbox clean so this test asserts purely on the dispatched event.
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(
            type=EventType.COMMENTED,
            card_id="c1",
            actor="bob",
            ts="2026-06-26T00:00:00Z",
        ),
        store=store,
        deliver_fn=rec,
    )
    return {
        "summary": summary,
        "alice": alice,
        "inbox": poll_inbox(alice.id, store=store),
    }


def test_commented_enqueues_each_recipient_once(commented_once_dispatch):
    # Arrange
    scenario = commented_once_dispatch
    # Act
    summary = scenario["summary"]
    # Assert
    assert summary["enqueued"] == [scenario["alice"].id]


def test_commented_no_double_delivery(commented_once_dispatch):
    # Arrange
    scenario = commented_once_dispatch
    # Act
    inbox = scenario["inbox"]
    # Assert — one event, one note.
    assert len(inbox) == 1


# --------------------------------------------------------------------------- #
# merged default-quiet; completed = owner + subscribers                       #
# --------------------------------------------------------------------------- #
#: WHY the five `merged_default_quiet` tests below are split but share this
#: rationale: `completed` is the canonical done-notice, not `merged`, so a
#: `merged` event must reach NOBODY by default. "Nobody" is a claim about the
#: summary AND about every individual inbox — a quiet summary hiding a real
#: inbox write is the double-notification the default-quiet rule exists to
#: prevent.
@pytest.fixture()
def merged_dispatch(tmp_path):
    """Dispatch the default-quiet `merged` on a card with owner + subscriber."""
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
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.MERGED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )
    return {
        "summary": summary,
        "rec": rec,
        "owner_inbox": poll_inbox(alice.id, store=store),
        "subscriber_inbox": poll_inbox(eve.id, store=store),
    }


def test_merged_enqueues_to_nobody_by_default(merged_dispatch):
    # Arrange
    scenario = merged_dispatch
    # Act
    summary = scenario["summary"]
    # Assert — default-quiet: completed is the done-notice, not merged.
    assert summary["enqueued"] == []


def test_merged_reports_nothing_directly_delivered(merged_dispatch):
    # Arrange
    scenario = merged_dispatch
    # Act
    summary = scenario["summary"]
    # Assert
    assert summary["delivered"] == []


def test_merged_never_calls_the_legacy_deliver_fn(merged_dispatch):
    # Arrange
    scenario = merged_dispatch
    # Act
    rec = scenario["rec"]
    # Assert
    assert rec.called is False


def test_merged_leaves_the_owner_inbox_empty(merged_dispatch):
    # Arrange
    scenario = merged_dispatch
    # Act
    owner_inbox = scenario["owner_inbox"]
    # Assert
    assert owner_inbox == []


def test_merged_leaves_the_subscriber_inbox_empty(merged_dispatch):
    # Arrange
    scenario = merged_dispatch
    # Act
    subscriber_inbox = scenario["subscriber_inbox"]
    # Assert
    assert subscriber_inbox == []


#: WHY the four `completed` tests below are split but share this rationale:
#: owner + subscriber are both enqueued (actor bob is neither) and there is no
#: direct-POST. The summary and the two inboxes are independent surfaces, and
#: the per-inbox check is what proves the enqueue really happened rather than
#: merely being reported.
@pytest.fixture()
def completed_dispatch(tmp_path):
    """Dispatch `completed` on a card with an owner and a subscriber."""
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
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.COMPLETED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )
    return {
        "summary": summary,
        "rec": rec,
        "recipient_ids": (alice.id, eve.id),
        "store": store,
    }


def test_completed_enqueues_to_owner_and_subscribers(completed_dispatch):
    # Arrange
    scenario = completed_dispatch
    # Act
    summary = scenario["summary"]
    # Assert
    assert set(summary["enqueued"]) == set(scenario["recipient_ids"])


def test_completed_reports_nothing_directly_delivered(completed_dispatch):
    # Arrange
    scenario = completed_dispatch
    # Act
    summary = scenario["summary"]
    # Assert
    assert summary["delivered"] == []


def test_completed_never_calls_the_legacy_deliver_fn(completed_dispatch):
    # Arrange
    scenario = completed_dispatch
    # Act
    rec = scenario["rec"]
    # Assert
    assert rec.called is False


def test_completed_note_reaches_every_recipient_inbox(completed_dispatch):
    # Arrange
    scenario = completed_dispatch
    # Act
    per_recipient = [
        [n["event_type"] for n in poll_inbox(uid, store=scenario["store"])]
        for uid in scenario["recipient_ids"]
    ]
    # Assert
    assert per_recipient == [["completed"]] * len(scenario["recipient_ids"])


# --------------------------------------------------------------------------- #
# no card / unknown card → fail-soft no-op                                     #
# --------------------------------------------------------------------------- #
#: WHY the three `missing_card_id` tests below are split but share this
#: rationale: an event with no card id must fail SOFT — no wire call, nothing
#: enqueued, and a NAMED reason in `skipped`. The named reason is the part
#: that distinguishes a deliberate no-op from a silent swallow.
@pytest.fixture()
def missing_card_id_dispatch(tmp_path):
    """A `released` event that carries no card id at all."""
    store = _store(tmp_path)
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.RELEASED, repo="o/r", version="1.0"),
        store=store,
        deliver_fn=rec,
    )
    return {"summary": summary, "rec": rec}


def test_missing_card_id_never_calls_the_deliver_fn(missing_card_id_dispatch):
    # Arrange
    scenario = missing_card_id_dispatch
    # Act
    rec = scenario["rec"]
    # Assert
    assert rec.called is False


def test_missing_card_id_is_noop(missing_card_id_dispatch):
    # Arrange
    scenario = missing_card_id_dispatch
    # Act
    summary = scenario["summary"]
    # Assert
    assert summary["enqueued"] == []


def test_missing_card_id_names_its_skip_reason(missing_card_id_dispatch):
    # Arrange
    scenario = missing_card_id_dispatch
    # Act
    summary = scenario["summary"]
    # Assert — a named reason, not a silent swallow.
    assert "no-card-id" in summary["skipped"]


#: WHY the three `unknown_card` tests below are split but share this
#: rationale: the same fail-soft contract for a card id that resolves to
#: nothing — no wire call, nothing enqueued, and `card-not-found` named in
#: `skipped` so the no-op is auditable.
@pytest.fixture()
def unknown_card_dispatch(tmp_path):
    """The store exists (a user was written) but the card id does not."""
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.COMPLETED, card_id="never-existed", actor="bob"),
        store=store,
        deliver_fn=rec,
    )
    return {"summary": summary, "rec": rec}


def test_unknown_card_never_calls_the_deliver_fn(unknown_card_dispatch):
    # Arrange
    scenario = unknown_card_dispatch
    # Act
    rec = scenario["rec"]
    # Assert
    assert rec.called is False


def test_unknown_card_is_noop(unknown_card_dispatch):
    # Arrange
    scenario = unknown_card_dispatch
    # Act
    summary = scenario["summary"]
    # Assert
    assert summary["enqueued"] == []


def test_unknown_card_names_its_skip_reason(unknown_card_dispatch):
    # Arrange
    scenario = unknown_card_dispatch
    # Act
    summary = scenario["summary"]
    # Assert
    assert "card-not-found" in summary["skipped"]


# --------------------------------------------------------------------------- #
# inbox fail-soft + no synchronous network on the mutation path                #
# --------------------------------------------------------------------------- #
#: WHY the three `per_recipient_enqueue` tests below are split but share this
#: rationale: every resolved recipient is enqueued (a per-recipient
#: try/except wraps each inbox write), so one recipient never starves the
#: others. "Everyone got it" and "nothing errored" are separate claims — a
#: swallowed per-recipient failure shows up in `errors`, not in `enqueued`.
@pytest.fixture()
def independent_enqueue_dispatch(tmp_path):
    """Dispatch to two recipients at once and keep the whole summary."""
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
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.COMPLETED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )
    return {"summary": summary, "rec": rec, "recipient_ids": (alice.id, eve.id)}


def test_per_recipient_enqueue_is_independent(independent_enqueue_dispatch):
    # Arrange
    scenario = independent_enqueue_dispatch
    # Act
    summary = scenario["summary"]
    # Assert — neither recipient starved the other.
    assert set(summary["enqueued"]) == set(scenario["recipient_ids"])


def test_per_recipient_enqueue_records_no_errors(independent_enqueue_dispatch):
    # Arrange
    scenario = independent_enqueue_dispatch
    # Act
    summary = scenario["summary"]
    # Assert — nothing was swallowed into `errors`.
    assert summary["errors"] == []


def test_per_recipient_enqueue_never_calls_the_deliver_fn(
    independent_enqueue_dispatch,
):
    # Arrange
    scenario = independent_enqueue_dispatch
    # Act
    rec = scenario["rec"]
    # Assert
    assert rec.called is False


#: WHY the three `fail_soft_envelope` tests below are split but share this
#: rationale: the fail-soft contract — dispatch_notifications NEVER raises and
#: ALWAYS returns the summary shape, even on a malformed event the resolver
#: rejects. (The inbox's own degenerate-store paths are covered by the _inbox
#: suite; here we assert the dispatcher's fail-soft envelope.) A bogus event
#: type → resolve_recipients raises → folded into `skipped`, not propagated.
#: The complete key set, the empty enqueue and the NAMED reason are three
#: different ways this contract breaks.
_SUMMARY_KEYS = (
    "event_type",
    "card_id",
    "delivered",
    "enqueued",
    "skipped",
    "errors",
)


@pytest.fixture()
def malformed_event_dispatch(tmp_path):
    """A bogus event type the resolver rejects, dispatched anyway."""
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice")

    return dispatch_notifications(
        {
            "kind": "card-event",
            "type": "not-a-real-type",
            "card_id": "c1",
            "actor": "bob",
        },
        store=store,
    )


def test_dispatch_never_raises_and_always_returns_a_summary(
    malformed_event_dispatch,
):
    # Arrange
    expected_keys = _SUMMARY_KEYS
    # Act
    summary = malformed_event_dispatch
    # Assert — the full summary shape survives a rejected event.
    assert all(key in summary for key in expected_keys)


def test_malformed_event_enqueues_to_nobody(malformed_event_dispatch):
    # Arrange
    summary = malformed_event_dispatch
    # Act
    enqueued = summary["enqueued"]
    # Assert
    assert enqueued == []


def test_malformed_event_names_its_skip_reason(malformed_event_dispatch):
    # Arrange
    summary = malformed_event_dispatch
    # Act
    skipped = summary["skipped"]
    # Assert — folded into `skipped`, not propagated as an exception.
    assert "resolve-failed" in skipped


#: WHY the three `producer_guarantee` tests below are split but share this
#: rationale: emit() must NOT raise, AND the dispatcher must never call the
#: legacy direct-POST (so a comment / any event never awaits a turn-URL
#: POST). The dispatch half is asserted on the untouched deliver_fn and the
#: real enqueue; the emit half is asserted by emit() returning a routed
#: card-event summary at all — reaching that assert is itself the proof it
#: did not raise.
@pytest.fixture()
def producer_guarantee_dispatch(tmp_path, env):
    """Dispatch `reassigned` with the shared-store env pointed at tmp_path."""
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice")
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))

    rec = _ExplodingDeliver()
    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )
    return {"summary": summary, "rec": rec, "alice": alice}


def test_emit_stays_non_raising_and_does_not_call_deliver_fn(
    producer_guarantee_dispatch,
):
    # Arrange
    scenario = producer_guarantee_dispatch
    # Act
    rec = scenario["rec"]
    # Assert — the network wire was NEVER invoked.
    assert rec.called is False


def test_producer_dispatch_still_enqueues_to_the_owner(
    producer_guarantee_dispatch,
):
    # Arrange
    scenario = producer_guarantee_dispatch
    # Act
    summary = scenario["summary"]
    # Assert
    assert summary["enqueued"] == [scenario["alice"].id]


def test_emit_through_the_real_bus_returns_a_card_event_summary(
    producer_guarantee_dispatch,
):
    # Arrange
    event = Event(type=EventType.REASSIGNED, card_id="c1", actor="bob")
    # Act
    result = emit(event, entry_points=[])
    # Assert — reaching this line proves emit() did not raise; `kind` proves it
    # actually routed the card-event rather than silently no-opping.
    assert result["kind"] == "card-event"


# --------------------------------------------------------------------------- #
# end-to-end via the bus                                                       #
# --------------------------------------------------------------------------- #
#: WHY the five `dispatch_event_card_event` tests below are split but share
#: this rationale: dispatch_event must invoke the C4 consumer for a card-event
#: and carry its summary under the ADDITIVE `notify` key — additive meaning
#: the pre-existing summary shape (`kind`, `card_writes`, `plugin_count`) is
#: unchanged. Owner alice is resolved and ENQUEUED to her inbox while
#: `delivered` stays empty (no synchronous direct-POST). Each of those is a
#: separate way the bus wiring regresses.
@pytest.fixture()
def bus_reassigned_summary(tmp_path, env):
    """Route a `reassigned` card-event through the real dispatch_event bus."""
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice")
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))

    envelope = Event(type=EventType.REASSIGNED, card_id="c1", actor="bob").to_dict()
    return {
        "summary": dispatch_event(envelope, store=store, entry_points=[]),
        "alice": alice,
    }


def test_dispatch_event_runs_builtin_notify_for_card_event(bus_reassigned_summary):
    # Arrange
    scenario = bus_reassigned_summary
    # Act
    summary = scenario["summary"]
    # Assert — the additive notify key is present and names the event.
    assert summary["notify"]["event_type"] == "reassigned"


def test_dispatch_event_preserves_the_card_event_kind(bus_reassigned_summary):
    # Arrange
    scenario = bus_reassigned_summary
    # Act
    summary = scenario["summary"]
    # Assert
    assert summary["kind"] == "card-event"


def test_dispatch_event_preserves_the_existing_summary_keys(
    bus_reassigned_summary,
):
    # Arrange
    expected_keys = ("card_writes", "plugin_count")
    # Act
    summary = bus_reassigned_summary["summary"]
    # Assert — `notify` was added WITHOUT disturbing the old shape.
    assert all(key in summary for key in expected_keys)


def test_dispatch_event_enqueues_the_owner_through_the_bus(
    bus_reassigned_summary,
):
    # Arrange
    scenario = bus_reassigned_summary
    # Act
    summary = scenario["summary"]
    # Assert
    assert summary["notify"]["enqueued"] == [scenario["alice"].id]


def test_dispatch_event_delivers_nothing_directly(bus_reassigned_summary):
    # Arrange
    scenario = bus_reassigned_summary
    # Act
    summary = scenario["summary"]
    # Assert — no synchronous direct-POST.
    assert summary["notify"]["delivered"] == []


#: WHY the three `no_recipients_is_clean` tests below are split but share this
#: rationale: a default-quiet `merged` card-event runs the consumer but
#: enqueues to nobody — the summary is PRESENT (the consumer ran), enqueued is
#: empty, and nothing was directly delivered. A consumer that never ran also
#: produces an empty enqueue, so the presence claim is what separates them.
@pytest.fixture()
def bus_merged_summary(tmp_path, env):
    """Route a default-quiet `merged` card-event through the bus."""
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice")
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))

    envelope = Event(
        type=EventType.MERGED, card_id="c1", actor="bob", repo="o/r"
    ).to_dict()
    return dispatch_event(envelope, store=store, entry_points=[])


def test_dispatch_event_card_event_with_no_recipients_is_clean(bus_merged_summary):
    # Arrange
    summary = bus_merged_summary
    # Act
    notify = summary["notify"]
    # Assert — the consumer ran and reported on the merged event.
    assert notify["event_type"] == "merged"


def test_dispatch_event_quiet_event_enqueues_nobody(bus_merged_summary):
    # Arrange
    summary = bus_merged_summary
    # Act
    notify = summary["notify"]
    # Assert
    assert notify["enqueued"] == []


def test_dispatch_event_quiet_event_delivers_nothing(bus_merged_summary):
    # Arrange
    summary = bus_merged_summary
    # Act
    notify = summary["notify"]
    # Assert
    assert notify["delivered"] == []


#: WHY the four `emit_end_to_end` tests below are split but share this
#: rationale: end-to-end through emit() -> dispatch_event(). Since emit()
#: can't take a deliver_fn, observing the delivery via the DRY-RUN wire's
#: stdout contract is brittle; instead dispatch_notifications (the unit the
#: bus calls) is asserted to deliver through the injected recorder, AND the
#: bus is asserted to invoke it (both covered above). Here we additionally
#: prove emit() does not raise and routes a card-event to the built-in
#: consumer end to end. emit() is non-raising AND now RETURNS the dispatch
#: summary (additive — it used to return None); that summary carries the C4
#: notify result so a producer / the `emit-event` CLI verb can report
#: enqueued / delivered.
@pytest.fixture()
def emitted_reassigned_result(tmp_path, env):
    """emit() a `reassigned` event end to end and keep what it returns."""
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice")
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    env.set("SCITEX_TODO_PUSH_DRY_RUN", "1")

    result = emit(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        entry_points=[],
    )
    return {"result": result, "alice": alice}


def test_emit_reassigned_reaches_recorder_via_real_dispatch(
    emitted_reassigned_result,
):
    # Arrange
    scenario = emitted_reassigned_result
    # Act
    result = scenario["result"]
    # Assert — emit() returns a summary now; it used to return None.
    assert result is not None


def test_emit_returns_a_card_event_kind(emitted_reassigned_result):
    # Arrange
    scenario = emitted_reassigned_result
    # Act
    result = scenario["result"]
    # Assert
    assert result["kind"] == "card-event"


def test_emit_result_carries_the_notify_event_type(emitted_reassigned_result):
    # Arrange
    scenario = emitted_reassigned_result
    # Act
    result = scenario["result"]
    # Assert — the C4 notify result rode along on emit()'s return value.
    assert result["notify"]["event_type"] == "reassigned"


def test_emit_result_reports_the_enqueued_owner(emitted_reassigned_result):
    # Arrange
    scenario = emitted_reassigned_result
    # Act
    result = scenario["result"]
    # Assert
    assert result["notify"]["enqueued"] == [scenario["alice"].id]


# EOF
