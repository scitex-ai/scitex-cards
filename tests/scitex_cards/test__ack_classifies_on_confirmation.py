#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`confirmed` vs `already_confirmed` must answer "did THIS call confirm it".

WHY THIS FILE EXISTS. The classification was read off `_inbox.ack`'s return —
the ids whose CURSOR this call advanced. The cursor is the wrong instrument: the
channel drain advances `seen` when it pushes a record, so by the time a consumer
acts on a notification the flip has already happened and `ack` honestly reports
flipping nothing.

Every first ack of a pushed record therefore came back `already_confirmed` —
which reads as "you have already done this, no action needed" — to a consumer
who had just delivered it for the first time. A careful consumer stops there.
Measured 2026-08-11 across two agents: twenty acks, twenty `already_confirmed`,
zero `confirmed`, including an inert 28-hour-old row and a first-person trial on
0.35.1 where the notification was read, acted on, and acked exactly once.

Note what was NOT wrong: `_inbox.ack` was honest, and the classification code
contained no error. A right answer to the wrong question has no defect to find
by reading it, which is why this survived being written down and reviewed.
"""

import pytest

from scitex_cards import _inbox
from scitex_cards._inbox_confirm import confirm_notifications
from scitex_cards._inbox_receipt import record_push

AGENT = "ack-class-agent"

#: The one real non-server inbox backend left. The file rail was retired 2026-08-23.
BACKENDS = ("yaml",)


@pytest.fixture(params=BACKENDS)
def store(request, env, tmp_path):
    env.set("SCITEX_CARDS_INBOX_BACKEND", request.param)
    return tmp_path / "tasks.yaml"


@pytest.fixture()
def drained(store):
    """A record the drain has pushed: `seen` advanced, never confirmed.

    Every notification on the live rail passes through this state, and it is the
    one the old cursor-based classification could not describe.
    """
    record = _inbox.enqueue(
        AGENT,
        event_type="commented",
        card_id="card-1",
        body="operator DM",
        actor="operator",
        ts="2026-08-11T06:00:00Z",
        store=store,
    )
    record_push(AGENT, [record["id"]], at="2026-08-11T06:00:04Z", store=store)
    return {"store": store, "id": record["id"]}


def test_the_drain_really_did_advance_the_cursor(drained):
    """POSITIVE CONTROL. If the pushed record were still unseen, `_inbox.ack`
    would flip it and the OLD classification would have been right — there would
    be no bug here to fix."""
    # Arrange
    store = drained["store"]
    # Act
    unseen = _inbox.poll_inbox(AGENT, unseen_only=True, store=store)
    # Assert
    assert unseen == []


def test_first_ack_of_a_drained_record_reports_confirmed(drained):
    """THE FIX. This is the exact call that returned `already_confirmed` twenty
    times in a row to consumers delivering a notification for the first time."""
    # Arrange
    store = drained["store"]
    # Act
    result = confirm_notifications(AGENT, [drained["id"]], store=store)
    # Assert
    assert result["confirmed"] == [drained["id"]]


def test_first_ack_of_a_drained_record_is_not_already_confirmed(drained):
    """The other half of the same claim, asserted separately so a failure says
    WHICH bucket went wrong rather than only that they disagree."""
    # Arrange
    store = drained["store"]
    # Act
    result = confirm_notifications(AGENT, [drained["id"]], store=store)
    # Assert
    assert result["already_confirmed"] == []


def test_a_second_ack_really_does_report_already_confirmed(drained):
    """`already_confirmed` must still mean something. A retry is a normal
    operation and must be distinguishable from a first delivery, or the fix has
    simply moved the lie to the other bucket."""
    # Arrange
    store = drained["store"]
    confirm_notifications(AGENT, [drained["id"]], store=store)
    # Act
    again = confirm_notifications(AGENT, [drained["id"]], store=store)
    # Assert
    assert again["already_confirmed"] == [drained["id"]]


def test_a_second_ack_reports_nothing_newly_confirmed(drained):
    # Arrange
    store = drained["store"]
    confirm_notifications(AGENT, [drained["id"]], store=store)
    # Act
    again = confirm_notifications(AGENT, [drained["id"]], store=store)
    # Assert
    assert again["confirmed"] == []


def test_an_id_this_inbox_never_held_is_unknown_not_confirmed(drained):
    """The third bucket must not absorb the change: a typo stays a typo."""
    # Arrange
    store = drained["store"]
    # Act
    result = confirm_notifications(AGENT, ["n_never_existed"], store=store)
    # Assert
    assert result["unknown"] == ["n_never_existed"]


def test_the_cursor_is_still_advanced_by_an_ack(drained):
    """The cursor advance is CORRECT behaviour and must survive: an acked
    notification must not come back as unseen. Only its RETURN stopped being
    the classifier."""
    # Arrange
    store = drained["store"]
    confirm_notifications(AGENT, [drained["id"]], store=store)
    # Act
    unseen = _inbox.poll_inbox(AGENT, unseen_only=True, store=store)
    # Assert
    assert unseen == []


# EOF
