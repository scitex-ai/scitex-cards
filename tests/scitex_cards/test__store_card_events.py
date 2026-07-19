#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C5 — store mutations emit canonical card-events + atomic reassign primitive.

The card-event/notification foundation epic, card
``cenf-c5-store-event-producers-20260626`` (+ the reassign-verb child
``todo-reassign-verb-with-owner-notify-20260626``). The mutating
:mod:`scitex_cards._store` verbs now ALSO emit a canonical
:class:`scitex_cards._events.Event` onto the hook bus, and a new
:func:`scitex_cards._store.reassign_task` primitive does an atomic
owner-change.

Mutation → event mapping under test:

    add_task        → ``created``
    comment_task    → ``commented``       (IN ADDITION to ``card-message``)
    update_task     → ``status_changed``  on a non-done flip; ``completed``
                      on a flip to done
    complete_task   → ``completed``
    resolve_task    → ``status_changed`` {from,to:done}
    reassign_task   → ``reassigned`` {from_owner,to_owner}

EMIT-ONLY: there is intentionally NO consumer yet (delivery is C4, a
separate card). Tests capture the emitted card-event via the documented
in-process ``entry_points=`` injection seam (a real fake handler) — no
mocks, no monkeypatch (STX-NM / PA-306). AAA pattern.
"""

from __future__ import annotations

from pathlib import Path

from scitex_cards._model import load_tasks
from scitex_cards._store import (
    add_task,
    comment_task,
    complete_task,
    reassign_task,
    resolve_task,
    update_task,
)

# === In-process injection seam (real fake handler, no mocks) ===============


class _Capturing:
    """Concrete fake entry-point handler that records every event."""

    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(dict(event))


class _FakeEP:
    def __init__(self, name: str, fn):
        self.name = name
        self._fn = fn

    def load(self):
        return self._fn


def _eps(sink: _Capturing) -> list[_FakeEP]:
    return [_FakeEP("captor", sink)]


def _card_events(sink: _Capturing, ev_type: str | None = None) -> list[dict]:
    """Only the C1 canonical card-events (the bus also fans legacy kinds —
    e.g. ``card-message`` from comment_task — to the same plugin set)."""
    out = [e for e in sink.events if e.get("kind") == "card-event"]
    if ev_type is not None:
        out = [e for e in out if e.get("type") == ev_type]
    return out


# === add_task → created ====================================================


def test_add_task_emits_exactly_one_created(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    sink = _Capturing()
    # Act
    add_task(
        store=store,
        id="c-1",
        title="x",
        entry_points=_eps(sink),
        assignee="agent:test-suite",
    )
    # Assert
    created = _card_events(sink, "created")
    assert len(created) == 1


def test_created_event_carries_card_id(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    sink = _Capturing()
    # Act
    add_task(
        store=store,
        id="c-1",
        title="x",
        entry_points=_eps(sink),
        assignee="agent:test-suite",
    )
    # Assert
    assert _card_events(sink, "created")[0]["card_id"] == "c-1"


def test_created_event_actor_is_creating_user(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    sink = _Capturing()
    # Act
    add_task(
        store=store,
        id="c-1",
        title="x",
        created_by="operator",
        entry_points=_eps(sink),
        assignee="agent:test-suite",
    )
    # Assert — an explicit created_by becomes the event actor.
    assert _card_events(sink, "created")[0]["actor"] == "operator"


# === comment_task → commented (+ the legacy card-message still fires) ======


def test_comment_task_emits_commented(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", assignee="agent:test-suite")
    sink = _Capturing()
    # Act
    comment_task(
        store=store,
        task_id="c-1",
        text="hi",
        by="operator",
        entry_points=_eps(sink),
    )
    # Assert
    assert len(_card_events(sink, "commented")) == 1


#: The `commented` event is ADDITIVE: the legacy `card-message` dispatch must
#: keep firing alongside it, or every existing consumer goes deaf at once. Both
#: halves are asserted separately below, because "the new event fired" and "the
#: old one still does" fail for opposite reasons and need naming apart.
def _commented_sink(tmp_path: Path) -> _Capturing:
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", assignee="agent:test-suite")
    sink = _Capturing()
    comment_task(
        store=store,
        task_id="c-1",
        text="hi",
        by="operator",
        entry_points=_eps(sink),
    )
    return sink


def test_comment_task_still_emits_card_message(tmp_path: Path):
    # Arrange
    sink = _commented_sink(tmp_path)
    # Act
    legacy = [e for e in sink.events if e.get("kind") == "card-message"]
    # Assert — the legacy dispatch was added to, not replaced.
    assert legacy != []


def test_comment_task_also_emits_the_canonical_event(tmp_path: Path):
    # Arrange
    sink = _commented_sink(tmp_path)
    # Act
    canonical = _card_events(sink, "commented")
    # Assert
    assert canonical != []


def test_commented_event_carries_body_and_actor(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", assignee="agent:test-suite")
    sink = _Capturing()
    # Act
    comment_task(
        store=store,
        task_id="c-1",
        text="hello there",
        by="alice",
        entry_points=_eps(sink),
    )
    # Assert
    e = _card_events(sink, "commented")[0]
    assert (
        e["card_id"] == "c-1" and e["actor"] == "alice" and e["body"] == "hello there"
    )


# === update_task → status_changed / completed =============================


def test_status_flip_emits_status_changed_with_from_to(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store, id="c-1", title="x", status="pending", assignee="agent:test-suite"
    )
    sink = _Capturing()
    # Act
    update_task(store, "c-1", status="in_progress", entry_points=_eps(sink))
    # Assert
    e = _card_events(sink, "status_changed")[0]
    assert (e["from"], e["to"]) == ("pending", "in_progress")


#: A flip to done is modelled as a `completed` event ONLY. The pair of tests
#: below split that into its two halves — the `completed` DID fire, and no
#: duplicate `status_changed` came with it. A consumer that acted on both would
#: double-count every completion, so the absence is as load-bearing as the
#: presence and deserves its own name.
def _flip_to_done_sink(tmp_path: Path) -> _Capturing:
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store,
        id="c-1",
        title="x",
        status="in_progress",
        assignee="agent:test-suite",
    )
    sink = _Capturing()
    update_task(store, "c-1", status="done", entry_points=_eps(sink))
    return sink


def test_update_to_done_emits_completed(tmp_path: Path):
    # Arrange
    sink = _flip_to_done_sink(tmp_path)
    # Act
    completed = _card_events(sink, "completed")
    # Assert
    assert len(completed) == 1


def test_update_to_done_emits_no_status_changed(tmp_path: Path):
    # Arrange
    sink = _flip_to_done_sink(tmp_path)
    # Act
    status_changed = _card_events(sink, "status_changed")
    # Assert — no duplicate event, or every completion counts twice.
    assert status_changed == []


#: Touching a non-status field must emit NEITHER status event. Split so a
#: regression names which one leaked.
def _note_only_sink(tmp_path: Path) -> _Capturing:
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store, id="c-1", title="x", status="pending", assignee="agent:test-suite"
    )
    sink = _Capturing()
    update_task(store, "c-1", note="just a note", entry_points=_eps(sink))
    return sink


def test_update_without_status_change_emits_no_status_changed(tmp_path: Path):
    # Arrange
    sink = _note_only_sink(tmp_path)
    # Act
    status_changed = _card_events(sink, "status_changed")
    # Assert
    assert status_changed == []


def test_update_without_status_change_emits_no_completed(tmp_path: Path):
    # Arrange
    sink = _note_only_sink(tmp_path)
    # Act
    completed = _card_events(sink, "completed")
    # Assert
    assert completed == []


def test_update_status_to_same_value_emits_no_event(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store, id="c-1", title="x", status="pending", assignee="agent:test-suite"
    )
    sink = _Capturing()
    # Act
    update_task(store, "c-1", status="pending", entry_points=_eps(sink))
    # Assert — re-setting status to its current value is not a flip.
    assert _card_events(sink) == []


# === complete_task → completed =============================================


def test_complete_task_emits_completed(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store,
        id="c-1",
        title="x",
        status="in_progress",
        assignee="agent:test-suite",
    )
    sink = _Capturing()
    # Act
    complete_task(store, "c-1", by="operator", entry_points=_eps(sink))
    # Assert
    e = _card_events(sink, "completed")
    assert len(e) == 1 and e[0]["card_id"] == "c-1" and e[0]["actor"] == "operator"


# The first completion transitions; the second is an idempotent no-op and must
# emit nothing at all.
def test_recomplete_done_task_emits_no_event(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store,
        id="c-1",
        title="x",
        status="in_progress",
        assignee="agent:test-suite",
    )
    complete_task(store, "c-1")
    sink = _Capturing()
    # Act
    complete_task(store, "c-1", entry_points=_eps(sink))
    # Assert
    assert _card_events(sink) == []


# === resolve_task → status_changed {from, to: done} =======================


def test_resolve_task_emits_status_changed_to_done(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store,
        id="c-1",
        title="x",
        status="blocked",
        blocker="operator-decision",
        assignee="agent:test-suite",
    )
    sink = _Capturing()
    # Act
    resolve_task(store, "c-1", actor="operator", entry_points=_eps(sink))
    # Assert
    e = _card_events(sink, "status_changed")
    assert len(e) == 1 and (e[0]["from"], e[0]["to"]) == ("blocked", "done")


def test_resolve_already_done_emits_no_event(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store, id="c-1", title="x", status="done", assignee="agent:test-suite"
    )
    sink = _Capturing()
    # Act
    resolve_task(store, "c-1", actor="operator", entry_points=_eps(sink))
    # Assert — resolving an already-done card is a no-op.
    assert _card_events(sink) == []


# === reassign_task — atomic owner change + reassigned event ================


#: ATOMIC means all three owner fields move together in ONE write. Each is
#: asserted on its own below: a partial reassign that moved `agent` but left
#: `scope` behind would put the card in one agent's queue and another's filter,
#: and a single compound assertion would only say "reassign is broken".
def _reassigned_card(tmp_path: Path) -> dict:
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    reassign_task(store, "c-1", "proj-new", by="operator")
    return [t for t in load_tasks(store) if t["id"] == "c-1"][0]


def test_reassign_sets_the_agent_field(tmp_path: Path):
    # Arrange
    card = _reassigned_card(tmp_path)
    # Act
    owner = card["agent"]
    # Assert
    assert owner == "proj-new"


def test_reassign_sets_the_assignee_field(tmp_path: Path):
    # Arrange
    card = _reassigned_card(tmp_path)
    # Act
    assignee = card["assignee"]
    # Assert
    assert assignee == "proj-new"


def test_reassign_sets_the_scope_field(tmp_path: Path):
    # Arrange
    card = _reassigned_card(tmp_path)
    # Act
    scope = card["scope"]
    # Assert — the scope filter must follow the owner, or the card goes missing.
    assert scope == "agent:proj-new"


def test_reassign_appends_audit_comment(tmp_path: Path):
    # Arrange
    card = _reassigned_card(tmp_path)
    # Act
    texts = [c.get("text") for c in card.get("comments") or []]
    # Assert
    assert any(
        "reassigned proj-old -> proj-new by operator" in (x or "") for x in texts
    )


#: The `reassigned` event, captured through the injection seam. Five tests split
#: what one asserted: that exactly one fired, and each field of its payload. The
#: payload IS the notification — a recipient resolver reading `to_owner` cannot
#: recover from it being wrong, so each field is pinned by name.
def _reassigned_events(tmp_path: Path) -> list[dict]:
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    sink = _Capturing()
    reassign_task(store, "c-1", "proj-new", by="operator", entry_points=_eps(sink))
    return _card_events(sink, "reassigned")


def test_reassign_emits_exactly_one_reassigned(tmp_path: Path):
    # Arrange
    events = _reassigned_events(tmp_path)
    # Act
    fired = len(events)
    # Assert
    assert fired == 1


def test_reassigned_event_carries_the_card_id(tmp_path: Path):
    # Arrange
    events = _reassigned_events(tmp_path)
    # Act
    card_id = events[0]["card_id"]
    # Assert
    assert card_id == "c-1"


def test_reassigned_event_carries_the_previous_owner(tmp_path: Path):
    # Arrange
    events = _reassigned_events(tmp_path)
    # Act
    from_owner = events[0]["from_owner"]
    # Assert
    assert from_owner == "proj-old"


def test_reassigned_event_carries_the_new_owner(tmp_path: Path):
    # Arrange
    events = _reassigned_events(tmp_path)
    # Act
    to_owner = events[0]["to_owner"]
    # Assert — this is the field delivery resolves the recipient from.
    assert to_owner == "proj-new"


def test_reassigned_event_carries_the_actor(tmp_path: Path):
    # Arrange
    events = _reassigned_events(tmp_path)
    # Act
    actor = events[0]["actor"]
    # Assert
    assert actor == "operator"


def test_reassign_to_same_owner_is_noop_no_event(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    sink = _Capturing()
    # Act
    result = reassign_task(
        store, "c-1", "proj-old", by="operator", entry_points=_eps(sink)
    )
    # Assert — the owner is already proj-old, so nothing changed.
    assert result["changed"] is False


def test_reassign_to_same_owner_writes_no_audit_comment(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    before = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    n_comments_before = len(before.get("comments") or [])
    # Act
    reassign_task(store, "c-1", "proj-old", by="operator")
    # Assert — a no-op must not leave an audit trail of a move that never was.
    after = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    assert len(after.get("comments") or []) == n_comments_before


def test_reassign_to_same_owner_emits_no_event(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    sink = _Capturing()
    # Act
    reassign_task(store, "c-1", "proj-old", by="operator", entry_points=_eps(sink))
    # Assert — no move, so no owner to notify.
    assert _card_events(sink, "reassigned") == []


#: Reassign once, then reassign identically again. The second call is the one
#: under test: it must report `changed=False` AND stay silent on the bus.
def _second_identical_reassign(tmp_path: Path):
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    reassign_task(store, "c-1", "proj-new", by="operator")
    sink = _Capturing()
    result = reassign_task(
        store, "c-1", "proj-new", by="operator", entry_points=_eps(sink)
    )
    return result, sink


def test_reassign_is_idempotent_second_call_noop(tmp_path: Path):
    # Arrange
    result, _sink = _second_identical_reassign(tmp_path)
    # Act
    changed = result["changed"]
    # Assert
    assert changed is False


def test_reassign_second_identical_call_emits_no_event(tmp_path: Path):
    # Arrange
    _result, sink = _second_identical_reassign(tmp_path)
    # Act
    events = _card_events(sink, "reassigned")
    # Assert — a retried reassign must not re-notify the owner.
    assert events == []


#: A card with no owner at all. ``add_task`` now REQUIRES an owner (fail-loud),
#: so a raw owner-less row is written to exercise reassign-from-None: the event
#: must carry ``from_owner=None`` rather than invent a placeholder, and the card
#: must end up genuinely owned.
def _reassigned_from_unowned(tmp_path: Path):
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks:\n  - id: c-1\n    title: x\n    status: pending\n")
    sink = _Capturing()
    reassign_task(store, "c-1", "proj-new", by="operator", entry_points=_eps(sink))
    card = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    return _card_events(sink, "reassigned")[0], card


def test_reassign_from_unassigned_records_placeholder(tmp_path: Path):
    # Arrange
    event, _card = _reassigned_from_unowned(tmp_path)
    # Act
    transition = (event["from_owner"], event["to_owner"])
    # Assert — no prior owner is reported as None, not as a made-up name.
    assert transition == (None, "proj-new")


def test_reassign_from_unassigned_leaves_the_card_owned(tmp_path: Path):
    # Arrange
    _event, card = _reassigned_from_unowned(tmp_path)
    # Act
    owner = card["agent"]
    # Assert
    assert owner == "proj-new"


# === FAIL-SOFT proof — a raising handler must not break the mutation =======


def _bad_eps() -> list[_FakeEP]:
    def _boom(_event):
        raise RuntimeError("handler exploded")

    return [_FakeEP("boom", _boom)]


#: A raising handler must not break `add_task`. Two things have to hold, and a
#: single test could pass on only one: the call RETURNED normally (it did not
#: propagate the handler's exception) and the card is DURABLY on disk (it did
#: not roll the write back on the way out).
def _added_with_exploding_handler(tmp_path: Path):
    store = tmp_path / "tasks.yaml"
    inserted = add_task(
        store=store,
        id="c-1",
        title="x",
        entry_points=_bad_eps(),
        assignee="agent:test-suite",
    )
    return inserted, store


def test_add_task_returns_normally_when_emit_raises(tmp_path: Path):
    # Arrange
    inserted, _store_path = _added_with_exploding_handler(tmp_path)
    # Act
    card_id = inserted["id"]
    # Assert — the handler exploded; the caller never saw it.
    assert card_id == "c-1"


def test_add_task_persists_even_when_emit_raises(tmp_path: Path):
    # Arrange
    _inserted, store = _added_with_exploding_handler(tmp_path)
    # Act
    matching = [t for t in load_tasks(store) if t["id"] == "c-1"]
    # Assert — the write is durable, not rolled back by the failed emit.
    assert matching != []


def test_complete_task_persists_even_when_emit_raises(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store,
        id="c-1",
        title="x",
        status="in_progress",
        assignee="agent:test-suite",
    )
    # Act
    complete_task(store, "c-1", entry_points=_bad_eps())
    # Assert — the call did not raise, and the done transition persisted.
    t = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    assert t["status"] == "done"


def test_update_status_persists_even_when_emit_raises(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store, id="c-1", title="x", status="pending", assignee="agent:test-suite"
    )
    # Act
    update_task(store, "c-1", status="in_progress", entry_points=_bad_eps())
    # Assert — the call did not raise, and the flip persisted.
    t = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    assert t["status"] == "in_progress"


# The bad handler receives BOTH the card-message and the commented emit, so
# this covers the pair in one shot.
def test_comment_task_persists_even_when_emit_raises(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", assignee="agent:test-suite")
    # Act
    comment_task(store=store, task_id="c-1", text="hi", entry_points=_bad_eps())
    # Assert — the call did not raise, and the comment landed on disk.
    t = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    assert any(c.get("text") == "hi" for c in t.get("comments") or [])


def _reassigned_with_exploding_handler(tmp_path: Path):
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    result = reassign_task(
        store, "c-1", "proj-new", by="operator", entry_points=_bad_eps()
    )
    card = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    return result, card


def test_reassign_returns_normally_when_emit_raises(tmp_path: Path):
    # Arrange
    result, _card = _reassigned_with_exploding_handler(tmp_path)
    # Act
    changed = result["changed"]
    # Assert — the handler exploded; the caller still got its answer.
    assert changed is True


def test_reassign_persists_even_when_emit_raises(tmp_path: Path):
    # Arrange
    _result, card = _reassigned_with_exploding_handler(tmp_path)
    # Act
    owner_fields = (card["agent"], card["scope"])
    # Assert — the owner change is durable despite the failed emit.
    assert owner_fields == ("proj-new", "agent:proj-new")


# === store-threading regression — the REAL enqueue lands in the SAME store =


# The captor tests above inject `entry_points=`, which intercepts the event
# at the plugin level — BEFORE the built-in `dispatch_notifications` consumer
# re-loads the card to resolve recipients. So they never exercised whether
# that card-load uses the mutation's OWN store. It did not: the emit calls
# omitted `store=`, so `dispatch_notifications` re-resolved the DEFAULT store,
# failed to find a card written to a different store, and silently dropped
# the notification. These tests drive the REAL dispatch (no injection) and
# assert the notification lands in the mutation's own store's inbox.


# The assignee differs from the actor, so the recipient is kept rather than
# filtered out as a self-notification.
def _created_notifications(tmp_path: Path):
    from scitex_cards import _inbox

    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", assignee="alice", created_by="operator")
    return _inbox.poll_inbox("alice", unseen_only=False, mark_seen=False, store=store)


def test_add_task_enqueues_created_notification_into_same_store(tmp_path: Path):
    # Arrange
    recs = _created_notifications(tmp_path)
    # Act
    delivered = len(recs)
    # Assert — exactly one notification, in THIS store's inbox, not the default.
    assert delivered == 1


def test_the_created_notification_names_the_new_card(tmp_path: Path):
    # Arrange
    recs = _created_notifications(tmp_path)
    # Act
    card_id = recs[0]["card_id"]
    # Assert
    assert card_id == "c-1"


def test_reassign_enqueues_notification_into_same_store(tmp_path: Path):
    # Arrange
    from scitex_cards import _inbox

    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    # Act
    reassign_task(store, "c-1", "proj-new", by="operator")
    # Assert — the new owner is notified in THIS store's inbox.
    recs = _inbox.poll_inbox(
        "proj-new", unseen_only=False, mark_seen=False, store=store
    )
    assert any(r.get("card_id") == "c-1" for r in recs)


# EOF
