#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``reassign_all`` — bulk owner change: one lock, one batch event.

The bulk-reassignment primitive ``sac agents rename`` needs
(``todo-reassign-all-bulk-primitive``). Every card owned by ``old_owner``
moves to ``new_owner`` in ONE atomic locked write; ONE canonical
``reassigned_batch`` event is emitted for the whole cohort (NOT one
``reassigned`` per card — that would be a notification flood).

Per-card semantics mirror :func:`scitex_cards._store.reassign_task`
EXACTLY: ``agent = assignee = new_owner``, ``scope = "agent:<new>"``, an
audit comment ``"reassigned <old> -> <new> by <actor>"``.

The event is captured via the documented in-process ``entry_points=``
injection seam (a real fake handler) — no mocks, no monkeypatch
(STX-NM / PA-306). AAA pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scitex_cards._model import load_tasks
from scitex_cards._store import add_task, reassign_all

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
    out = [e for e in sink.events if e.get("kind") == "card-event"]
    if ev_type is not None:
        out = [e for e in out if e.get("type") == ev_type]
    return out


def _by_id(store, tid: str) -> dict:
    return [t for t in load_tasks(store) if t["id"] == tid][0]


# === N cards owned by X all move to Y in ONE call ==========================


#: WHY the nine `all_cards_move` tests below are split but share this
#: rationale: one bulk call has to get TWO independent things right — what it
#: WROTE to every card (agent, assignee and scope moving together, since a
#: half-moved card is owned by one agent and scoped to another) and what it
#: REPORTED back (count, changed, card_ids, from_owner, to_owner, actor). The
#: report is what `sac agents rename` prints and acts on, so a correct write
#: with a wrong report is still a broken primitive — and vice versa. Nine
#: claims behind one first-assert means eight of them never run.
@pytest.fixture()
def bulk_move_of_three_cards(tmp_path: Path):
    """Three cards owned by proj-old, all moved to proj-new in one call."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    card_ids = ("c-1", "c-2", "c-3")
    for cid in card_ids:
        add_task(store=store, id=cid, title=cid, agent="proj-old")
    result = reassign_all(store, "proj-old", "proj-new", by="operator")
    return {
        "result": result,
        "cards": [_by_id(store, cid) for cid in card_ids],
    }


def test_every_moved_card_has_the_new_agent(bulk_move_of_three_cards):
    # Arrange
    scenario = bulk_move_of_three_cards
    # Act
    cards = scenario["cards"]
    # Assert
    assert all(t["agent"] == "proj-new" for t in cards)


def test_every_moved_card_has_the_new_assignee(bulk_move_of_three_cards):
    # Arrange
    scenario = bulk_move_of_three_cards
    # Act
    cards = scenario["cards"]
    # Assert — agent and assignee move together, never one without the other.
    assert all(t["assignee"] == "proj-new" for t in cards)


def test_every_moved_card_has_the_new_scope(bulk_move_of_three_cards):
    # Arrange
    scenario = bulk_move_of_three_cards
    # Act
    cards = scenario["cards"]
    # Assert — a card scoped to the old owner would be invisible to the new one.
    assert all(t["scope"] == "agent:proj-new" for t in cards)


def test_all_cards_of_old_owner_move_to_new_owner(bulk_move_of_three_cards):
    # Arrange
    scenario = bulk_move_of_three_cards
    # Act
    result = scenario["result"]
    # Assert
    assert result["count"] == 3


def test_bulk_result_reports_that_it_changed(bulk_move_of_three_cards):
    # Arrange
    scenario = bulk_move_of_three_cards
    # Act
    result = scenario["result"]
    # Assert
    assert result["changed"] is True


def test_bulk_result_lists_every_moved_card_id(bulk_move_of_three_cards):
    # Arrange
    scenario = bulk_move_of_three_cards
    # Act
    result = scenario["result"]
    # Assert
    assert sorted(result["card_ids"]) == ["c-1", "c-2", "c-3"]


def test_bulk_result_names_the_from_owner(bulk_move_of_three_cards):
    # Arrange
    scenario = bulk_move_of_three_cards
    # Act
    result = scenario["result"]
    # Assert
    assert result["from_owner"] == "proj-old"


def test_bulk_result_names_the_to_owner(bulk_move_of_three_cards):
    # Arrange
    scenario = bulk_move_of_three_cards
    # Act
    result = scenario["result"]
    # Assert
    assert result["to_owner"] == "proj-new"


def test_bulk_result_names_the_acting_operator(bulk_move_of_three_cards):
    # Arrange
    scenario = bulk_move_of_three_cards
    # Act
    result = scenario["result"]
    # Assert
    assert result["actor"] == "operator"


def test_each_moved_card_gets_audit_comment(tmp_path: Path):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    for cid in ("c-1", "c-2"):
        add_task(store=store, id=cid, title=cid, agent="proj-old")
    # Act
    reassign_all(store, "proj-old", "proj-new", by="operator")
    # Assert — the identical reassign_task audit comment on each card.
    for cid in ("c-1", "c-2"):
        texts = [c.get("text") for c in _by_id(store, cid).get("comments") or []]
        assert any(
            "reassigned proj-old -> proj-new by operator" in (x or "") for x in texts
        )


# === EXACTLY ONE reassigned_batch event, NOT N events ======================


#: WHY the two `one_batch_event` tests below are split but share this
#: rationale: the whole point of the batch event is that it does NOT flood.
#: "One batch event was emitted" and "zero per-card events were emitted" are
#: different failures: an implementation that emits the batch AND keeps
#: emitting one `reassigned` per card satisfies the first claim completely
#: while being exactly the notification flood this design exists to avoid.
@pytest.fixture()
def bulk_move_capturing_events(tmp_path: Path):
    """Four cards moved in one call, with every emitted event captured."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    for cid in ("c-1", "c-2", "c-3", "c-4"):
        add_task(store=store, id=cid, title=cid, agent="proj-old")
    sink = _Capturing()
    reassign_all(store, "proj-old", "proj-new", by="operator", entry_points=_eps(sink))
    return sink


def test_emits_exactly_one_batch_event_not_n(bulk_move_capturing_events):
    # Arrange
    sink = bulk_move_capturing_events
    # Act
    batch = _card_events(sink, "reassigned_batch")
    # Assert
    assert len(batch) == 1


def test_emits_no_per_card_reassigned_events(bulk_move_capturing_events):
    # Arrange
    sink = bulk_move_capturing_events
    # Act
    per_card = _card_events(sink, "reassigned")
    # Assert — one event models the ACT; N events would be the flood.
    assert per_card == []


#: WHY the five `batch_event_payload` tests below are split but share this
#: rationale: the single event models the ACT, not the rows — so its payload
#: is the ONLY record a downstream consumer gets of who moved what, from whom,
#: to whom, and how many. Each field is a separate thing a consumer reads and
#: a separate thing that silently goes missing.
@pytest.fixture()
def batch_event_payload(tmp_path: Path):
    """The one `reassigned_batch` event emitted for a three-card move."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    for cid in ("c-1", "c-2", "c-3"):
        add_task(store=store, id=cid, title=cid, agent="proj-old")
    sink = _Capturing()
    reassign_all(store, "proj-old", "proj-new", by="operator", entry_points=_eps(sink))
    return _card_events(sink, "reassigned_batch")[0]


def test_batch_event_names_the_from_owner(batch_event_payload):
    # Arrange
    event = batch_event_payload
    # Act
    from_owner = event["from_owner"]
    # Assert
    assert from_owner == "proj-old"


def test_batch_event_names_the_to_owner(batch_event_payload):
    # Arrange
    event = batch_event_payload
    # Act
    to_owner = event["to_owner"]
    # Assert
    assert to_owner == "proj-new"


def test_batch_event_names_the_actor(batch_event_payload):
    # Arrange
    event = batch_event_payload
    # Act
    actor = event["actor"]
    # Assert
    assert actor == "operator"


def test_batch_event_payload_carries_count_and_card_ids(batch_event_payload):
    # Arrange
    event = batch_event_payload
    # Act
    count = event["count"]
    # Assert
    assert count == 3


def test_batch_event_lists_every_moved_card_id(batch_event_payload):
    # Arrange
    event = batch_event_payload
    # Act
    ids = sorted(event["card_ids"])
    # Assert
    assert ids == ["c-1", "c-2", "c-3"]


# === Idempotent — no matches ⇒ count 0, changed False, no event ============


#: WHY the five `no_matches` tests below are split but share this rationale:
#: a bulk move that matches nothing must be a TOTAL no-op — it reports zero,
#: reports unchanged, lists no ids, emits NO event at all, and leaves the
#: unrelated card exactly as it was. The event claim is the one that matters
#: most operationally (an empty batch event still wakes every consumer), and
#: the untouched-card claim is the one that catches an over-broad match.
@pytest.fixture()
def bulk_move_matching_nobody(tmp_path: Path):
    """Nobody is owned by `ghost`, so the move should touch nothing."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", agent="proj-keep")
    sink = _Capturing()
    result = reassign_all(
        store, "ghost", "proj-new", by="operator", entry_points=_eps(sink)
    )
    return {"result": result, "sink": sink, "card": _by_id(store, "c-1")}


def test_no_matches_is_noop_no_event(bulk_move_matching_nobody):
    # Arrange
    scenario = bulk_move_matching_nobody
    # Act
    result = scenario["result"]
    # Assert
    assert result["count"] == 0


def test_no_matches_reports_unchanged(bulk_move_matching_nobody):
    # Arrange
    scenario = bulk_move_matching_nobody
    # Act
    result = scenario["result"]
    # Assert
    assert result["changed"] is False


def test_no_matches_lists_no_card_ids(bulk_move_matching_nobody):
    # Arrange
    scenario = bulk_move_matching_nobody
    # Act
    result = scenario["result"]
    # Assert
    assert result["card_ids"] == []


def test_no_matches_emits_no_event_at_all(bulk_move_matching_nobody):
    # Arrange
    scenario = bulk_move_matching_nobody
    # Act
    events = _card_events(scenario["sink"])
    # Assert — even an empty batch event would wake every consumer.
    assert events == []


def test_no_matches_leaves_the_other_card_untouched(bulk_move_matching_nobody):
    # Arrange
    scenario = bulk_move_matching_nobody
    # Act
    card = scenario["card"]
    # Assert
    assert card["agent"] == "proj-keep"


#: WHY the three `second_call` tests below are split but share this rationale:
#: idempotence — after a move the old owner has no cards left, so re-running
#: the SAME move must be the same total no-op as never matching at all
#: (count 0, unchanged, and crucially no second event for work already done).
@pytest.fixture()
def repeated_bulk_move(tmp_path: Path):
    """Run the same move twice; capture only what the SECOND run did."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    reassign_all(store, "proj-old", "proj-new")
    sink = _Capturing()
    result = reassign_all(store, "proj-old", "proj-new", entry_points=_eps(sink))
    return {"result": result, "sink": sink}


def test_second_call_after_move_is_noop(repeated_bulk_move):
    # Arrange
    scenario = repeated_bulk_move
    # Act
    result = scenario["result"]
    # Assert
    assert result["count"] == 0


def test_second_call_reports_unchanged(repeated_bulk_move):
    # Arrange
    scenario = repeated_bulk_move
    # Act
    result = scenario["result"]
    # Assert
    assert result["changed"] is False


def test_second_call_emits_no_event(repeated_bulk_move):
    # Arrange
    scenario = repeated_bulk_move
    # Act
    events = _card_events(scenario["sink"])
    # Assert — no re-notification for work already done.
    assert events == []


# === Cards owned by OTHER agents are untouched =============================


#: WHY the five `only_matching_owner` tests below are split but share this
#: rationale: a bulk write is only safe if it is SCOPED. The cards it should
#: move must move, and the bystander must be left alone — both in its owner
#: AND in its scope. The scope claim is asserted as a NEGATIVE because the
#: fixture's add_task never sets a scope, so there is no original value to
#: compare against; what matters is that reassign_all did not rescope a card
#: it had no business touching.
@pytest.fixture()
def bulk_move_with_a_bystander(tmp_path: Path):
    """A mix of owners: two proj-old cards move, one proj-other must not."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    add_task(store=store, id="c-2", title="y", agent="proj-other")
    add_task(store=store, id="c-3", title="z", agent="proj-old")
    result = reassign_all(store, "proj-old", "proj-new", by="operator")
    return {
        "result": result,
        "moved": [_by_id(store, "c-1"), _by_id(store, "c-3")],
        "bystander": _by_id(store, "c-2"),
    }


def test_only_matching_owner_moves_others_untouched(bulk_move_with_a_bystander):
    # Arrange
    scenario = bulk_move_with_a_bystander
    # Act
    result = scenario["result"]
    # Assert — only the proj-old cards are reported as moved.
    assert sorted(result["card_ids"]) == ["c-1", "c-3"]


def test_matching_cards_all_get_the_new_owner(bulk_move_with_a_bystander):
    # Arrange
    scenario = bulk_move_with_a_bystander
    # Act
    moved = scenario["moved"]
    # Assert
    assert all(t["agent"] == "proj-new" for t in moved)


def test_bystander_card_keeps_its_own_owner(bulk_move_with_a_bystander):
    # Arrange
    scenario = bulk_move_with_a_bystander
    # Act
    bystander = scenario["bystander"]
    # Assert
    assert bystander["agent"] == "proj-other"


def test_bystander_card_is_not_rescoped(bulk_move_with_a_bystander):
    # Arrange
    scenario = bulk_move_with_a_bystander
    # Act
    bystander = scenario["bystander"]
    # Assert — reassign_all must NOT have rescoped a card it did not move.
    assert bystander.get("scope") != "agent:proj-new"


#: WHY the three `legacy_assignee` tests below are split but share this
#: rationale: a card owned only via the legacy `assignee` field (no `agent`)
#: must still be MATCHED by the bulk move, and once moved it must carry BOTH
#: owner fields — matching it but only half-writing it would leave a card the
#: next run can no longer find.
@pytest.fixture()
def bulk_move_of_legacy_assignee_card(tmp_path: Path):
    """A card owned only via the legacy `assignee` field (no agent)."""
    from conftest import seed_db_from_doc

    doc = {
        "tasks": [
            {"id": "c-1", "title": "x", "status": "pending", "assignee": "proj-old"}
        ]
    }
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    result = reassign_all(store, "proj-old", "proj-new", by="operator")
    return {"result": result, "card": _by_id(store, "c-1")}


def test_matches_legacy_assignee_only_owner(bulk_move_of_legacy_assignee_card):
    # Arrange
    scenario = bulk_move_of_legacy_assignee_card
    # Act
    result = scenario["result"]
    # Assert — the assignee-owned card is matched.
    assert result["card_ids"] == ["c-1"]


def test_legacy_assignee_card_gains_the_new_agent(
    bulk_move_of_legacy_assignee_card,
):
    # Arrange
    scenario = bulk_move_of_legacy_assignee_card
    # Act
    card = scenario["card"]
    # Assert
    assert card["agent"] == "proj-new"


def test_legacy_assignee_card_gets_the_new_assignee(
    bulk_move_of_legacy_assignee_card,
):
    # Arrange
    scenario = bulk_move_of_legacy_assignee_card
    # Act
    card = scenario["card"]
    # Assert
    assert card["assignee"] == "proj-new"


# === old_owner == new_owner raises ValueError =============================


def test_same_owner_raises_value_error(tmp_path: Path):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    # Act
    ctx = pytest.raises(ValueError)
    # Assert
    with ctx:
        reassign_all(store, "proj-old", "proj-old", by="operator")


#: WHY the two `missing_owner` tests below are split but share this rationale:
#: an empty owner on EITHER side is a caller bug, and each side is validated
#: separately — a guard that only checks `old_owner` happily moves a whole
#: cohort into the empty string, orphaning every card it touched.
def test_missing_old_owner_raises_value_error(tmp_path: Path):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    # Act
    ctx = pytest.raises(ValueError)
    # Assert
    with ctx:
        reassign_all(store, "", "proj-new")


def test_missing_new_owner_raises_value_error(tmp_path: Path):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    # Act
    ctx = pytest.raises(ValueError)
    # Assert
    with ctx:
        reassign_all(store, "proj-old", "")


# === Other fields preserved on moved cards ================================


#: WHY the five `preserve_other_fields` tests below are split but share this
#: rationale: a bulk owner change must rewrite the OWNER and nothing else.
#: Title, status and priority each survive on their own, and the audit comment
#: must be ADDED rather than replacing the card's history — which is why both
#: "the pre-existing comment is still there" and "the count grew by exactly
#: one" are checked. A write that replaced the comment list would keep the
#: count plausible while erasing the record.
@pytest.fixture()
def bulk_move_of_a_populated_card(tmp_path: Path):
    """A card with title, status, priority, and an existing comment, moved."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(
        store=store,
        id="c-1",
        title="keep-me",
        agent="proj-old",
        status="in_progress",
        priority=2,
    )
    from scitex_cards._store import comment_task

    comment_task(store=store, task_id="c-1", text="pre-existing", by="alice")
    n_before = len(_by_id(store, "c-1").get("comments") or [])
    reassign_all(store, "proj-old", "proj-new", by="operator")
    return {"card": _by_id(store, "c-1"), "n_before": n_before}


def test_moved_cards_preserve_other_fields(bulk_move_of_a_populated_card):
    # Arrange
    scenario = bulk_move_of_a_populated_card
    # Act
    card = scenario["card"]
    # Assert
    assert card["title"] == "keep-me"


def test_moved_card_preserves_its_status(bulk_move_of_a_populated_card):
    # Arrange
    scenario = bulk_move_of_a_populated_card
    # Act
    card = scenario["card"]
    # Assert
    assert card["status"] == "in_progress"


def test_moved_card_preserves_its_priority(bulk_move_of_a_populated_card):
    # Arrange
    scenario = bulk_move_of_a_populated_card
    # Act
    card = scenario["card"]
    # Assert
    assert card["priority"] == 2


def test_moved_card_keeps_its_pre_existing_comment(bulk_move_of_a_populated_card):
    # Arrange
    scenario = bulk_move_of_a_populated_card
    # Act
    texts = [c.get("text") for c in scenario["card"].get("comments") or []]
    # Assert
    assert any("pre-existing" in (x or "") for x in texts)


def test_moved_card_gains_exactly_one_audit_comment(bulk_move_of_a_populated_card):
    # Arrange
    scenario = bulk_move_of_a_populated_card
    # Act
    n_after = len(scenario["card"].get("comments") or [])
    # Assert — the audit comment is ADDED, not a replacement.
    assert n_after == scenario["n_before"] + 1


# === FAIL-SOFT — a raising handler must not break the bulk write ==========


#: WHY the three `emit_raises` tests below are split but share this rationale:
#: a downstream handler blowing up must not roll back or abort the bulk write
#: that already happened. The call has to return normally AND the owner change
#: has to be on disk — a fail-soft that swallows the exception but loses the
#: write is the worst of both.
@pytest.fixture()
def bulk_move_with_an_exploding_handler(tmp_path: Path):
    """Move two cards while the entry-point handler raises."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    for cid in ("c-1", "c-2"):
        add_task(store=store, id=cid, title=cid, agent="proj-old")

    def _boom(_event):
        raise RuntimeError("handler exploded")

    bad = [_FakeEP("boom", _boom)]
    # Must NOT raise.
    result = reassign_all(
        store, "proj-old", "proj-new", by="operator", entry_points=bad
    )
    return {
        "result": result,
        "cards": [_by_id(store, cid) for cid in ("c-1", "c-2")],
    }


def test_persists_even_when_emit_raises(bulk_move_with_an_exploding_handler):
    # Arrange
    scenario = bulk_move_with_an_exploding_handler
    # Act
    result = scenario["result"]
    # Assert — the call returned normally despite the exploding handler.
    assert result["changed"] is True


def test_exploding_handler_still_reports_the_moved_count(
    bulk_move_with_an_exploding_handler,
):
    # Arrange
    scenario = bulk_move_with_an_exploding_handler
    # Act
    result = scenario["result"]
    # Assert
    assert result["count"] == 2


def test_exploding_handler_does_not_lose_the_bulk_write(
    bulk_move_with_an_exploding_handler,
):
    # Arrange
    scenario = bulk_move_with_an_exploding_handler
    # Act
    cards = scenario["cards"]
    # Assert — the owner change really is on disk.
    assert all(t["agent"] == "proj-new" for t in cards)


# EOF
