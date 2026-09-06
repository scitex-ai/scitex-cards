#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`unconfirmed` describes the INBOX, not whichever page the caller fetched.

WHY THIS FILE EXISTS — measured 2026-08-11 by scitex-db, reproduced first-person
on 0.35.1. `poll_notifications` reported `unconfirmed: []` while the health
doctor counted 16 notifications as pushed-and-never-confirmed, on the same
inbox, at the same moment. Two surfaces of one package disagreeing about one
fact, and the consumer-facing one said "nothing to do".

TWO INDEPENDENT CAUSES, and fixing either alone leaves the field useless:

  1. IT KEYED ON `seen`. The channel drain advances `seen` when it pushes a
     record (`record_push` advances the cursor — see
     `test__inbox_receipt.py::test_a_pushed_record_stops_being_returned_as_unseen`).
     So every pushed notification looked confirmed the instant it was handed to
     a transport, while `confirmed_at` — the only actual evidence of arrival —
     stayed NULL.

  2. IT WAS COMPUTED OVER THE RETURNED PAGE. The default page is unseen-only,
     and the drain has already marked the rows seen, so the page is EMPTY and
     the field was empty with it — by construction, regardless of which column
     it read. Fixing only (1) yields a correct predicate applied to nothing,
     which looks fixed and reports the same [].

The second is the load-bearing one: it is why a consumer had to query the rail
directly to find a notification it had just acted on.
"""

import pytest

from scitex_cards import _inbox
from scitex_cards._inbox_confirm import confirm_notifications
from scitex_cards._inbox_receipt import (
    CONFIRMED_AT,
    is_confirmed,
    receipts,
    record_push,
    unconfirmed_ids,
)

AGENT = "unconfirmed-agent"

#: The one real non-server inbox backend left. The file rail was retired 2026-08-23.
BACKENDS = ("yaml",)


@pytest.fixture(params=BACKENDS)
def store(request, env, tmp_path):
    """A real store on each real inbox backend, in turn."""
    env.set("SCITEX_CARDS_INBOX_BACKEND", request.param)
    return tmp_path / "tasks.yaml"


@pytest.fixture()
def drained(store):
    """One record that the drain has pushed — seen advanced, never confirmed.

    This is the state EVERY notification on the live rail passes through, and
    the one both defects made invisible.
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


def _receipt(store, notification_id):
    for record in receipts(AGENT, store=store):
        if record["id"] == notification_id:
            return record
    raise AssertionError(f"{notification_id} not in {AGENT}'s inbox")


def test_the_drain_really_does_empty_the_default_page(drained):
    """POSITIVE CONTROL for cause 2. If a drained record were still returned by
    the default page, the old page-scoped computation would have worked and this
    whole file would be testing a bug that cannot happen."""
    # Arrange
    store = drained["store"]
    # Act
    page = _inbox.poll_inbox(AGENT, unseen_only=True, store=store)
    # Assert
    assert page == []


def test_the_drained_record_really_is_unconfirmed(drained):
    """POSITIVE CONTROL for cause 1. The row must genuinely lack a confirmation,
    or `unconfirmed` reporting it would be the bug rather than the fix."""
    # Arrange
    store = drained["store"]
    # Act
    receipt = _receipt(store, drained["id"])
    # Assert
    assert receipt[CONFIRMED_AT] is None


def test_unconfirmed_ids_reports_a_drained_record(drained):
    """THE FIX. Seen by the drain, never confirmed by anyone — and therefore
    still outstanding, which is what the caller asked."""
    # Arrange
    store = drained["store"]
    # Act
    outstanding = unconfirmed_ids(AGENT, store=store)
    # Assert
    assert outstanding == [drained["id"]]


def test_confirming_removes_it_from_unconfirmed(drained):
    """The other half of the signal: a real confirmation must clear it, or the
    field is a constant and equally useless in the opposite direction."""
    # Arrange
    store = drained["store"]
    confirm_notifications(AGENT, [drained["id"]], store=store)
    # Act
    outstanding = unconfirmed_ids(AGENT, store=store)
    # Assert
    assert outstanding == []


def test_is_confirmed_is_false_for_a_pushed_record(drained):
    """The predicate itself, at the point the two questions diverge: this record
    IS seen and is NOT confirmed."""
    # Arrange
    store = drained["store"]
    # Act
    receipt = _receipt(store, drained["id"])
    # Assert
    assert is_confirmed(receipt) is False


def test_the_two_questions_actually_disagree_on_this_record(drained):
    """THE HEART OF IT. `seen` says handled, `confirmed_at` says never arrived,
    about the SAME row at the SAME moment. Any surface keying on `seen` answers
    'did the drain run?' while appearing to answer 'did the recipient get it?'."""
    # Arrange
    store = drained["store"]
    # Act
    receipt = _receipt(store, drained["id"])
    # Assert
    assert (bool(receipt.get("seen")), is_confirmed(receipt)) == (True, False)


def test_scoping_to_ids_still_reports_only_the_unconfirmed_ones(drained):
    """Scoped queries must not resurrect the page-scoping bug: asking about an
    id that IS confirmed returns nothing, rather than everything outstanding."""
    # Arrange
    store = drained["store"]
    confirm_notifications(AGENT, [drained["id"]], store=store)
    # Act
    outstanding = unconfirmed_ids(AGENT, [drained["id"]], store=store)
    # Assert
    assert outstanding == []


# EOF
