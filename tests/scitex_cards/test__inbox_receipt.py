#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PUSHED IS NOT CONFIRMED — the receipts that keep a lost notification visible.

The defect these pin (measured 2026-07-29): the channel drain ack'd a record the
instant ``await send(params)`` returned. That call proves only that our own
stdout writer took the bytes — a JSON-RPC notification has no reply — and Claude
Code silently DISCARDS a push from a server missing from its launch-line
allowlist. One stale name in an agent spec therefore destroyed weeks of operator
DMs: 228 rows enqueued for the agent, consumed, ZERO unseen, every check green.

So ``pushed_at`` and ``confirmed_at`` are now separate facts. The drain may only
write the first; the second is the recipient's own act. A record that was pushed
and never confirmed stays on the row forever, where the health doctor can see it.

SQLite is RETIRED as an inbox backend (operator ruling 2026-08-23); the file
break-glass backend (``SCITEX_CARDS_INBOX_BACKEND=yaml``, what this suite's
conftest already pins) and the PostgreSQL backend production actually runs
are the two real options left. This suite covers the file backend directly;
the PostgreSQL dispatch is covered by :mod:`scitex_cards._inbox_receipt_postgres`'s
own suite (a live server is out of scope for this file's fixtures).

No mocks (STX-NM / PA-306): a real store, real ``_inbox`` records, the real
``drain_once`` with a real async send callable, and the real file inbox
backend.
"""

from __future__ import annotations

import asyncio

import pytest

from scitex_cards import _inbox
from scitex_cards._inbox_confirm import confirm_notifications
from scitex_cards._inbox_receipt import (
    CONFIRMED_AT,
    PUSHED_AT,
    receipts,
    record_confirmation,
    record_push,
)
from scitex_cards._mcp_channel import drain_once

AGENT = "receipt-agent"

#: The one real non-server inbox backend left. SQLite was retired 2026-08-23.
BACKENDS = ("yaml",)


class _SendRecorder:
    """A real async ``send`` callable — records every pushed params payload."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, params: dict) -> None:
        self.calls.append(params)


class _FailingSend:
    """A real async ``send`` that always raises — the push never lands."""

    async def __call__(self, params: dict) -> None:
        raise RuntimeError("simulated push failure")


def _enqueue(store, index=1, agent=AGENT):
    """One real unseen notification; returns its record."""
    return _inbox.enqueue(
        agent,
        event_type="commented",
        card_id=f"card-{index}",
        body=f"operator DM {index}",
        actor="operator",
        ts=f"2026-07-29T07:0{index}:00Z",
        store=store,
    )


def _receipt(store, notification_id, agent=AGENT):
    """Read ONE record's receipts back off the real store."""
    for record in receipts(agent, store=store):
        if record["id"] == notification_id:
            return record
    raise AssertionError(f"{notification_id} not found in {agent}'s inbox")


@pytest.fixture(params=BACKENDS)
def store(request, env, tmp_path):
    """A real store on each real inbox backend, in turn."""
    env.set("SCITEX_CARDS_INBOX_BACKEND", request.param)
    return tmp_path / "tasks.yaml"


@pytest.fixture()
def pushed(store):
    """One record, recorded as pushed at a fixed instant."""
    record = _enqueue(store)
    record_push(AGENT, [record["id"]], at="2026-07-29T07:05:00Z", store=store)
    return {"store": store, "id": record["id"]}


# --------------------------------------------------------------------------- #
# record_push — the cursor advance stops claiming delivery                     #
# --------------------------------------------------------------------------- #
def test_a_pushed_record_carries_the_push_time(pushed):
    # Arrange
    # Act
    receipt = _receipt(pushed["store"], pushed["id"])
    # Assert
    assert receipt[PUSHED_AT] == "2026-07-29T07:05:00Z"


def test_a_pushed_record_is_not_confirmed_by_being_pushed(pushed):
    # Arrange — the whole defect: handing bytes to a transport is not arrival.
    # Act
    receipt = _receipt(pushed["store"], pushed["id"])
    # Assert
    assert receipt[CONFIRMED_AT] is None


def test_a_pushed_record_stops_being_returned_as_unseen(pushed):
    # Arrange — the bound: exactly one push per record, so no redelivery storm.
    # Act
    unseen = _inbox.poll_inbox(AGENT, unseen_only=True, store=pushed["store"])
    # Assert
    assert unseen == []


def test_re_pushing_keeps_the_first_push_time(pushed):
    # Arrange — the age of an unconfirmed push must measure how long it has gone
    # unanswered, not how recently something retried it.
    record_push(AGENT, [pushed["id"]], at="2026-07-30T09:00:00Z", store=pushed["store"])
    # Act
    receipt = _receipt(pushed["store"], pushed["id"])
    # Assert
    assert receipt[PUSHED_AT] == "2026-07-29T07:05:00Z"


def test_pushing_an_id_this_inbox_never_held_reports_nothing(store):
    # Arrange
    _enqueue(store)
    # Act
    stamped = record_push(AGENT, ["n_doesnotexist"], store=store)
    # Assert
    assert stamped == []


# --------------------------------------------------------------------------- #
# record_confirmation — the only arrival evidence that exists                  #
# --------------------------------------------------------------------------- #
def test_a_confirmed_record_carries_the_confirmation_time(pushed):
    # Arrange
    record_confirmation(
        AGENT, [pushed["id"]], at="2026-07-29T07:06:00Z", store=pushed["store"]
    )
    # Act
    receipt = _receipt(pushed["store"], pushed["id"])
    # Assert
    assert receipt[CONFIRMED_AT] == "2026-07-29T07:06:00Z"


def test_re_confirming_keeps_the_first_confirmation_time(pushed):
    # Arrange — a retrying consumer must not be punished for retrying.
    record_confirmation(
        AGENT, [pushed["id"]], at="2026-07-29T07:06:00Z", store=pushed["store"]
    )
    record_confirmation(
        AGENT, [pushed["id"]], at="2026-07-29T08:00:00Z", store=pushed["store"]
    )
    # Act
    receipt = _receipt(pushed["store"], pushed["id"])
    # Assert
    assert receipt[CONFIRMED_AT] == "2026-07-29T07:06:00Z"


def test_confirming_a_record_the_drain_already_pushed_still_records_arrival(pushed):
    # Arrange — THE LOAD-BEARING CASE. The drain already flipped `seen`, so a
    # confirm keyed off that flip would record nothing for exactly the records
    # the delivery check exists to judge.
    confirm_notifications(AGENT, [pushed["id"]], store=pushed["store"])
    # Act
    receipt = _receipt(pushed["store"], pushed["id"])
    # Assert
    assert receipt[CONFIRMED_AT] is not None


def test_confirming_reports_the_id_as_known_not_unknown(pushed):
    """The #617 return contract: a record this inbox HELD must never come back
    as `unknown`, which is reserved for a typo.

    This asserted `already_confirmed == [id]` until 2026-08-11. That was a PROXY
    for the intent in its own name — under the old cursor-based classification,
    `already_confirmed` was the only non-`unknown` bucket a drained record could
    reach, because the drain had already advanced `seen` and `_inbox.ack` flipped
    nothing. Now that classification keys on the confirmation stamp, a FIRST
    confirm of a drained record correctly reports `confirmed`, and the proxy no
    longer tracks the intent.

    So the assertion moves to what the name always claimed. Which bucket a
    record lands in is pinned by `test__ack_classifies_on_confirmation.py`; this
    one guards the contract it was written for.
    """
    # Arrange — the #617 return contract must survive the new stamp.
    # Act
    result = confirm_notifications(AGENT, [pushed["id"]], store=pushed["store"])
    # Assert
    assert result["unknown"] == []


# --------------------------------------------------------------------------- #
# the REAL drain writes the receipt                                            #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def drained(store):
    """One record drained through the real ``drain_once`` and a real send."""
    record = _enqueue(store)
    recorder = _SendRecorder()
    pushed_count = asyncio.run(drain_once(AGENT, recorder, store=store))
    return {"store": store, "id": record["id"], "pushed": pushed_count}


def test_the_drain_actually_pushed_the_record(drained):
    """The precondition — without it every receipt assertion below is vacuous."""
    # Arrange
    # Act
    pushed_count = drained["pushed"]
    # Assert
    assert pushed_count == 1


def test_the_drain_records_that_it_pushed(drained):
    # Arrange
    # Act
    receipt = _receipt(drained["store"], drained["id"])
    # Assert
    assert receipt[PUSHED_AT] is not None


def test_the_drain_does_not_claim_the_recipient_received_it(drained):
    # Arrange — the transport gives no receipt, so the drain must not invent one.
    # Act
    receipt = _receipt(drained["store"], drained["id"])
    # Assert
    assert receipt[CONFIRMED_AT] is None


def test_a_push_that_raised_leaves_no_push_receipt(store):
    # Arrange
    record = _enqueue(store)
    asyncio.run(drain_once(AGENT, _FailingSend(), store=store))
    # Act
    receipt = _receipt(store, record["id"])
    # Assert
    assert receipt[PUSHED_AT] is None


def test_a_push_that_raised_leaves_the_record_unseen_for_the_next_drain(store):
    # Arrange
    _enqueue(store)
    asyncio.run(drain_once(AGENT, _FailingSend(), store=store))
    # Act
    unseen = _inbox.poll_inbox(AGENT, unseen_only=True, store=store)
    # Assert
    assert len(unseen) == 1


# EOF
