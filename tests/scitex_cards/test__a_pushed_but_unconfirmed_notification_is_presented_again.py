#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A notification pushed into a session that died is presented again.

THE RAIL BUILT FOR THIS OUTAGE WAS READING THE FIELD THE OUTAGE MOVES.
``_inbox_present.pending`` selected on ``seen``, and ``seen`` stopped meaning
"the consumer got it" the day the channel drain began advancing the cursor as
it PUSHES: ``record_push(advance_cursor=True)`` sets ``seen`` in the same
statement that writes ``pushed_at``. So a record handed to a session that then
died was seen, unconfirmed, and invisible to every later unseen-only poll.

The package already knew this in one place and denied it in another.
``_inbox_receipt.is_confirmed``'s docstring says plainly that ``seen`` is not
evidence of delivery, while ``_inbox_confirm``'s header promised "an
unconfirmed notification is still unseen, so the next poll returns it again"
and the MCP skill text repeated it to every agent. Both are corrected in the
same change: the promise was load-bearing and wrong, and agents were told
their crash-safety came from the store when it came from their own discipline
of polling with ``ack=False``.

THE GRACE TEST IS THE ONE THAT KEEPS THIS HONEST. Re-presenting a record the
consumer was handed seconds ago is a duplicate, not a recovery, and a rail
that duplicates is one its reader learns to skim — which is how the original
outage stayed invisible.
"""

import datetime as _dt

from scitex_cards._health_delivery import PUSH_CONFIRM_GRACE_SECONDS
from scitex_cards._inbox import enqueue
from scitex_cards._inbox_present import pending
from scitex_cards._inbox_receipt import record_push

AGENT = "agent-under-test"


def _stamp(seconds_ago: float) -> str:
    when = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=seconds_ago)
    return when.isoformat().replace("+00:00", "Z")


def _enqueue_one(store, body: str = "ping") -> str:
    return enqueue(AGENT, event_type="dm", card_id="c1", body=body, actor="peer", store=store)["id"]


def test_a_never_pushed_notification_is_presented(new_store):
    """THE POSITIVE CONTROL. Unchanged behaviour, and it runs first.

    Every "is presented again" assertion below is meaningless if the rail
    cannot present anything at all in this arrangement.
    """
    # Arrange
    store = new_store()
    _enqueue_one(store)
    # Act
    out = pending(AGENT, store=store)
    # Assert
    assert len(out) == 1


def test_a_pushed_but_unconfirmed_notification_is_presented_again(new_store):
    """THE DEFECT. Before this change the push made it invisible forever."""
    # Arrange
    store = new_store()
    nid = _enqueue_one(store)
    record_push(AGENT, [nid], at=_stamp(PUSH_CONFIRM_GRACE_SECONDS + 60), store=store)
    # Act
    out = pending(AGENT, store=store)
    # Assert
    assert [r["id"] for r in out] == [nid]


def test_a_freshly_pushed_notification_is_held_back(new_store):
    """A RECENT PUSH IS NOT A LOST ONE — the anti-duplicate half."""
    # Arrange
    store = new_store()
    nid = _enqueue_one(store)
    record_push(AGENT, [nid], at=_stamp(5), store=store)
    # Act
    out = pending(AGENT, store=store)
    # Assert
    assert out == []


def test_a_confirmed_notification_is_never_presented(new_store):
    """CONFIRMED IS THE ONLY THING THAT RETIRES A RECORD.

    Without this the change would trade one defect for a worse one: a rail
    that re-presents work the consumer already did, forever.
    """
    # Arrange
    from scitex_cards._inbox_confirm import confirm_notifications

    store = new_store()
    nid = _enqueue_one(store)
    record_push(AGENT, [nid], at=_stamp(PUSH_CONFIRM_GRACE_SECONDS + 60), store=store)
    confirm_notifications(AGENT, [nid], store=store)
    # Act
    out = pending(AGENT, store=store)
    # Assert
    assert out == []


# EOF
