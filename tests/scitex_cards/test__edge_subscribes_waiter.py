#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`set_edge` must SUBSCRIBE the waiter — the edge has to mean what its name says.

Reported by scitex-writer, MEASURED not inferred:

    depends_on edge + set_subscriber  ->  notification FIRES
    depends_on edge ALONE             ->  NOTHING. Total silence.

The whole reason to record "A depends_on B" is so that FINISHING B TELLS A. An
agent who wants to hear when their blocker clears reaches for `depends_on` — the
semantically obvious call, literally named for the relationship — and got
silence. Silence is INDISTINGUISHABLE from "the gate has not cleared yet", so
nobody ever finds out. Four real cards sat blocked on gates that had already
cleared, including a mutual deadlock made of two stale sentences.

THE RULE: the owner of the WAITING card is subscribed to the card they WAIT ON.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scitex_cards import _store


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    path = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _store.add_task(
        path, id="gate", title="the blocker", status="in_progress", agent="agent-b"
    )
    # `blocker` is required on a blocked card — the store warns otherwise, and it is
    # right to: a blocked card that names no gate is one nobody can clear.
    _store.add_task(
        path,
        id="waiter",
        title="waits on the gate",
        status="blocked",
        agent="agent-a",
        blocker="dependency",
    )
    return path


def _subs(path: Path, task_id: str) -> list[str]:
    return list(_store.get_task(path, task_id).get("subscribers") or [])


# --------------------------------------------------------------------------
# depends_on — the reported case
# --------------------------------------------------------------------------
def _add_depends_on_edge(store):
    """ "waiter depends_on gate": the waiter waits on the gate."""
    return _store.set_edge(
        store, action="add", kind="depends_on", source="waiter", target="gate"
    )


def test_depends_on_subscribes_the_waiters_owner_to_the_gate(store):
    # Arrange
    # Act
    _add_depends_on_edge(store)

    # Assert — finishing the GATE must now be able to tell the WAITER's owner.
    assert _subs(store, "gate") == ["agent-a"]


def test_depends_on_reports_who_it_subscribed(store):
    # Arrange — the caller must be able to SEE that delivery is wired, not
    # assume it: assuming it is the entire bug.
    # Act
    result = _add_depends_on_edge(store)

    # Assert
    assert result["subscribed"] == "agent-a"


def test_depends_on_does_not_subscribe_the_gate_owner_to_the_waiter(store):
    # Arrange — the relationship has a DIRECTION. Only the waiter needs telling.
    # Act
    _add_depends_on_edge(store)

    # Assert
    assert _subs(store, "waiter") == []


# --------------------------------------------------------------------------
# blocks — the same relationship pointing the other way
# --------------------------------------------------------------------------
def _add_blocks_edge(store):
    """ "gate blocks waiter" says the same thing as "waiter depends_on gate"."""
    return _store.set_edge(
        store, action="add", kind="blocks", source="gate", target="waiter"
    )


def test_blocks_subscribes_the_blocked_cards_owner_to_the_blocker(store):
    # Arrange — leaving this one silent would move the landmine one call left.
    # Act
    _add_blocks_edge(store)

    # Assert
    assert _subs(store, "gate") == ["agent-a"]


def test_blocks_reports_who_it_subscribed(store):
    # Arrange
    # Act
    result = _add_blocks_edge(store)

    # Assert
    assert result["subscribed"] == "agent-a"


# --------------------------------------------------------------------------
# the guards
# --------------------------------------------------------------------------
def test_subscribing_over_an_existing_subscription_makes_no_duplicate(store):
    # Arrange — already subscribed by hand.
    _store.set_subscriber(store, task_id="gate", who="agent-a", action="add")

    # Act
    _add_depends_on_edge(store)

    # Assert
    assert _subs(store, "gate") == ["agent-a"]


def test_subscribing_an_already_subscribed_owner_reports_nobody_new(store):
    # Arrange — already subscribed by hand.
    _store.set_subscriber(store, task_id="gate", who="agent-a", action="add")

    # Act
    result = _add_depends_on_edge(store)

    # Assert — it honestly reports that it added nobody new.
    assert result["subscribed"] is None


# Removing an edge keeping the subscription is DELIBERATE. The owner may have
# subscribed for their own reasons, and silently dropping that subscription would
# re-create this bug from the other side. An extra notification is a nuisance; a
# missing one strands a card.
def _add_then_remove_the_depends_on_edge(store):
    _add_depends_on_edge(store)
    return _store.set_edge(
        store, action="remove", kind="depends_on", source="waiter", target="gate"
    )


def test_removing_an_edge_really_drops_the_edge(store):
    # Arrange
    # Act
    _add_then_remove_the_depends_on_edge(store)

    # Assert — the premise: the edge is genuinely gone.
    assert _store.get_task(store, "waiter").get("depends_on") in (None, [])


def test_removing_an_edge_does_NOT_unsubscribe(store):
    # Arrange
    # Act
    _add_then_remove_the_depends_on_edge(store)

    # Assert — edge gone, subscription kept.
    assert _subs(store, "gate") == ["agent-a"]


def test_removing_an_edge_reports_that_it_subscribed_nobody(store):
    # Arrange
    # Act
    result = _add_then_remove_the_depends_on_edge(store)

    # Assert
    assert result["subscribed"] is None


def test_an_ownerless_waiter_subscribes_nobody_and_says_so(tmp_path: Path):
    # Arrange — there is nobody to tell. We do NOT invent a recipient:
    # `subscribed: None` says so plainly instead of letting the caller believe
    # delivery is wired.
    path = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _store.add_task(
        path, id="gate", title="gate", status="in_progress", agent="agent-b"
    )
    _store.add_task(
        path, id="orphan", title="no owner", status="blocked", agent="agent-b"
    )
    _store.update_task(path, "orphan", agent=None, assignee=None)  # strip the owner

    # Act
    result = _store.set_edge(
        path, action="add", kind="depends_on", source="orphan", target="gate"
    )

    # Assert
    assert result["subscribed"] is None


# --------------------------------------------------------------------------
# END-TO-END — the reporter's own experiment, as a test
# --------------------------------------------------------------------------
def test_completing_the_gate_notifies_the_waiters_owner_END_TO_END(store):
    """The claim that actually matters: does a notification REACH the waiter?

    A unit test on the subscribers list would pass even if the delivery path were
    broken — which is exactly the shape of the bug being fixed (a mechanism that
    looks wired and delivers nothing). So complete the gate with a DIFFERENT actor
    and assert the waiter's owner has a message in the inbox.

    The non-self actor matters: `actor == subscriber` is suppressed, so completing
    your OWN card notifies nobody. Testing that way is how you conclude, wrongly,
    that the whole mechanism is broken.
    """
    from scitex_cards import _inbox

    # Arrange — the edge alone. NO explicit set_subscriber: that is the point.
    _store.set_edge(
        store, action="add", kind="depends_on", source="waiter", target="gate"
    )

    # Act — someone ELSE finishes the gate.
    _store.complete_task(store, "gate", by="agent-b")

    # Assert — the waiter's owner was told.
    notes = _inbox.poll_inbox("agent-a", store=store, unseen_only=False)
    bodies = " ".join(str(n.get("body") or "") for n in notes)
    assert "gate" in bodies, (
        "completing the gate did not reach the waiting card's owner — the edge is "
        f"still a silent no-op. inbox={notes}"
    )
