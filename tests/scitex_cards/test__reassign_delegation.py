#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Delegation keeps responsibility — reassign auto-subscribes the delegator.

Operator directive 2026-07-18 (「渡しました、委任しましたで終わられると
困る」) mechanizing constitution §2 "ownership never dangles": handing a
card off must NOT end the delegator's accountability. Both reassign verbs
(single + bulk) keep the PREVIOUS owner and the card's CREATOR on
``subscribers`` through the handoff, so notify rules and late-nudges still
reach the people who delegated. Leaving the loop is an explicit
``set_subscriber`` remove, never a side effect of handing off.

Real store files, AAA, no mocks (STX-NM).
"""

from __future__ import annotations

import pytest

from scitex_cards._model import load_tasks
from scitex_cards._store import add_task, reassign_all, reassign_task


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return str(path)


def _card(store, task_id: str, owner: str, created_by: str) -> None:
    add_task(
        store=store,
        id=task_id,
        title=f"card {task_id}",
        status="in_progress",
        assignee=owner,
        agent=owner,
        created_by=created_by,
    )


def _by_id(store, task_id: str) -> dict:
    return next(t for t in load_tasks(store) if t.get("id") == task_id)


# === reassign_task (single) ================================================


#: WHY the two `subscribes_previous_owner_and_creator` tests below are split
#: but share this rationale: a handoff must keep the delegation chain WITHOUT
#: double-notifying the new owner. Those pull in opposite directions — the
#: first wants names added to `subscribers`, the second wants one name kept
#: OUT — so an implementation that simply subscribes everyone involved passes
#: the accountability claim while spamming the incoming owner as both OWNER
#: and subscriber.
@pytest.fixture()
def handed_off_card(store):
    """alice owns a card carol created; alice hands it to bob."""
    _card(store, "t1", owner="alice", created_by="carol")
    reassign_task(store, "t1", "bob", by="alice")
    return list(_by_id(store, "t1").get("subscribers") or [])


def test_reassign_subscribes_previous_owner_and_creator(handed_off_card):
    # Arrange
    keepers = {"alice", "carol"}
    # Act
    subs = handed_off_card
    # Assert — both the delegator and the creator stay in the loop.
    assert keepers <= set(subs)


def test_reassign_does_not_subscribe_the_new_owner(handed_off_card):
    # Arrange
    new_owner = "bob"
    # Act
    subs = handed_off_card
    # Assert — the new owner is notified as OWNER, not twice.
    assert new_owner not in subs


#: WHY the three `never_duplicates` tests below are split but share this
#: rationale: a card can bounce between the same people repeatedly, and each
#: keeper must appear EXACTLY once however many times it moves. The state
#: after the first hop is checked on its own because a duplicate introduced
#: there and a duplicate introduced by the later round trip are different
#: bugs — and the second is invisible if the first assert already failed.
@pytest.fixture()
def repeatedly_handed_off_card(store):
    """alice → bob, then back to alice, then out to carol."""
    _card(store, "t2", owner="alice", created_by="alice")
    reassign_task(store, "t2", "bob", by="alice")
    after_first_hop = list(_by_id(store, "t2").get("subscribers") or [])
    # The card comes BACK to alice, then leaves again.
    reassign_task(store, "t2", "alice", by="bob")
    reassign_task(store, "t2", "carol", by="alice")
    return {
        "after_first_hop": after_first_hop,
        "final": list(_by_id(store, "t2").get("subscribers") or []),
    }


def test_first_handoff_subscribes_the_delegator_once(repeatedly_handed_off_card):
    # Arrange
    scenario = repeatedly_handed_off_card
    # Act
    subs = scenario["after_first_hop"]
    # Assert — alice is subscribed exactly once by her own handoff.
    assert subs.count("alice") == 1


def test_reassign_never_duplicates_an_existing_subscriber(
    repeatedly_handed_off_card,
):
    # Arrange
    scenario = repeatedly_handed_off_card
    # Act
    subs = scenario["final"]
    # Assert — alice re-owning and re-delegating does not re-add her.
    assert subs.count("alice") == 1


def test_repeated_handoffs_keep_every_delegator_once(repeatedly_handed_off_card):
    # Arrange
    scenario = repeatedly_handed_off_card
    # Act
    subs = scenario["final"]
    # Assert — bob delegated once and appears once.
    assert subs.count("bob") == 1


#: WHY the two `same_owner_noop` tests below are split but share this
#: rationale: reassigning to the CURRENT owner is the documented no-op, which
#: means two things — it reports no change, and it leaves no subscriber
#: residue behind. A no-op that still quietly subscribes the "previous" owner
#: to their own card is how a self-reassign turns into a self-notification.
@pytest.fixture()
def same_owner_reassign(store):
    """Reassign a card to the owner it already has."""
    _card(store, "t3", owner="alice", created_by="alice")
    result = reassign_task(store, "t3", "alice", by="alice")
    return {"result": result, "subscribers": _by_id(store, "t3").get("subscribers")}


def test_same_owner_noop_reports_no_change(same_owner_reassign):
    # Arrange
    scenario = same_owner_reassign
    # Act
    result = scenario["result"]
    # Assert
    assert result["changed"] is False


def test_same_owner_noop_adds_no_subscribers(same_owner_reassign):
    # Arrange
    scenario = same_owner_reassign
    # Act
    subscribers = scenario["subscribers"]
    # Assert — no write, no subscriber side effects.
    assert subscribers in (None, [])


# === reassign_all (bulk) ===================================================


#: WHY the three `bulk_reassign` tests below are split but share this
#: rationale: the bulk verb must uphold the SAME delegation contract as the
#: single verb, on EVERY card it moves — and report how many it moved. The
#: per-card chain differs by card (b1 was created by its owner, b2 by carol),
#: so "every moved card keeps its chain" and "no moved card subscribes the new
#: owner" are checked across the whole cohort rather than on a sample.
@pytest.fixture()
def bulk_reassigned_cohort(store):
    """alice owns two cards, one created by carol; the cohort moves to bob."""
    _card(store, "b1", owner="alice", created_by="alice")
    _card(store, "b2", owner="alice", created_by="carol")
    result = reassign_all(store, "alice", "bob", by="alice")
    return {
        "result": result,
        "subscribers": {
            tid: set(_by_id(store, tid).get("subscribers") or [])
            for tid in ("b1", "b2")
        },
    }


def test_bulk_reassign_reports_the_moved_card_count(bulk_reassigned_cohort):
    # Arrange
    scenario = bulk_reassigned_cohort
    # Act
    result = scenario["result"]
    # Assert
    assert result["count"] == 2


def test_bulk_reassign_subscribes_delegator_on_every_moved_card(
    bulk_reassigned_cohort,
):
    # Arrange — b1 was created by its owner; b2 by carol.
    expected = {"b1": {"alice"}, "b2": {"alice", "carol"}}
    # Act
    subscribers = bulk_reassigned_cohort["subscribers"]
    # Assert — every moved card keeps its delegation chain.
    assert all(keepers <= subscribers[tid] for tid, keepers in expected.items())


def test_bulk_reassign_does_not_subscribe_the_new_owner(bulk_reassigned_cohort):
    # Arrange
    new_owner = "bob"
    # Act
    subscribers = bulk_reassigned_cohort["subscribers"]
    # Assert — the incoming owner is never double-notified.
    assert all(new_owner not in subs for subs in subscribers.values())


# EOF
