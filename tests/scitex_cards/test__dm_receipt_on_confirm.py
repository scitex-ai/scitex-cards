#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Confirming a DM notification records the recipient's read receipt.

THE BUG THIS CLOSES, measured on the live store 2026-07-30. Every
``operator -> scitex-cards`` DM had zero receipts while every
``scitex-cards -> operator`` DM had one, and across the WHOLE
``dm_receipts`` table only three readers had ever written a row — the
operator's browser plus two agents that poll. Every other agent in the
fleet had written none, because a receipt was only written by
``dm_list(ack=True)`` and an agent that receives DM bodies over the
channel push never calls it. So the operator's read lamp could not light
for most of the fleet, and "the agent has not read this" was
indistinguishable from "the agent is dead" — the one distinction the
feature exists to make.

The enabling change is that ``_inbox.enqueue`` now carries ``msg_id``.
Without it a confirmed notification could not be joined back to the
message it delivered: the only available key was
``(event_type, card_id, ts, actor)``, and DM timestamps are
second-resolution, so that key is many-to-one BY CONSTRUCTION.
``test_two_dms_in_the_same_second_stay_distinct`` pins exactly that —
it is the case that was measured collapsing on the live store.

``test_confirming_a_card_event_writes_no_receipt`` is the control that
keeps the others honest: a bridge that fired for every event type would
pass every positive test here while inventing receipts for cards.
"""

from __future__ import annotations

import pytest

from scitex_cards import _inbox
from scitex_cards._inbox_confirm import confirm_notifications


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """An isolated store + inbox DB, so the live fleet store is never touched.

    The SQLite inbox is selected EXPLICITLY. ``tests/scitex_cards/conftest.py``
    pins ``SCITEX_TODO_INBOX_BACKEND=yaml`` for every test, and the file
    backend resolves the legacy embedded ``inboxes:`` section by YAML-parsing
    the task store — which, on the canonical store, is a SQLite file. That is
    the shipped configuration this feature runs in, so pinning sqlite here
    tests the real thing rather than the break-glass path.
    """
    monkeypatch.setenv("SCITEX_CARDS_DB", str(tmp_path / "cards.db"))
    monkeypatch.setenv("SCITEX_TODO_INBOX_DB", str(tmp_path / "inbox.db"))
    monkeypatch.setenv("SCITEX_TODO_INBOX_BACKEND", "sqlite")
    return tmp_path / "cards.db"


def _send(store_path, sender, to, body, ts=None):
    """Commit a real DM through the real write path. Returns the record."""
    from scitex_cards._dm_ids import pair_thread_id
    from scitex_cards._dm_write import append

    return append(
        pair_thread_id(sender, to),
        sender,
        body,
        store=store_path,
        ts=ts,
    )


def _readers_of(store_path, message_id):
    """The readers who have confirmed ``message_id``, straight from the store."""
    from scitex_cards._dm_read import _open

    conn = _open(None, store_path)
    try:
        rows = conn.execute(
            "SELECT reader FROM dm_receipts WHERE message_id = ?", (message_id,)
        ).fetchall()
    finally:
        conn.close()
    return {r["reader"] for r in rows}


def test_enqueue_carries_the_message_id(store):
    """The record must report msg_id — the join key everything else needs."""
    # Arrange
    _inbox.enqueue(
        "agent-a",
        event_type="dm",
        card_id="dm:x::y",
        body="hi",
        actor="operator",
        msg_id="m_abc123",
        store=store,
    )

    # Act
    records = _inbox.poll_inbox("agent-a", unseen_only=False, store=store)

    # Assert
    assert records[0]["msg_id"] == "m_abc123"


def test_msg_id_is_present_even_when_absent(store):
    """Always-present key: a card event reports None, never a missing key."""
    # Arrange
    _inbox.enqueue(
        "agent-a",
        event_type="created",
        card_id="card-1",
        body="hi",
        actor="operator",
        store=store,
    )

    # Act
    records = _inbox.poll_inbox("agent-a", unseen_only=False, store=store)

    # Assert
    assert records[0]["msg_id"] is None


def test_two_dms_in_the_same_second_stay_distinct(store):
    """The measured collapse: same (type, card, ts, actor), different messages."""
    # Arrange
    same = {
        "event_type": "dm",
        "card_id": "dm:x::y",
        "actor": "operator",
        "ts": "2026-07-30T01:00:00Z",
        "store": store,
    }
    _inbox.enqueue("agent-a", body="first", msg_id="m_one", **same)
    _inbox.enqueue("agent-a", body="second", msg_id="m_two", **same)

    # Act
    records = _inbox.poll_inbox("agent-a", unseen_only=False, store=store)

    # Assert
    assert [r["msg_id"] for r in records] == ["m_one", "m_two"]


def test_confirming_a_dm_writes_the_readers_receipt(store):
    """The whole point: the recipient's confirm becomes a durable receipt."""
    # Arrange
    sent = _send(store, "operator", "agent-a", "please read me")
    queued = _inbox.enqueue(
        "agent-a",
        event_type="dm",
        card_id=sent["thread_id"],
        body=sent["body"],
        actor="operator",
        ts=sent["ts"],
        msg_id=sent["id"],
        store=store,
    )

    # Act
    confirm_notifications("agent-a", [queued["id"]], store=store)

    # Assert
    assert "agent-a" in _readers_of(store, sent["id"])


def test_no_receipt_before_the_confirm(store):
    """Enqueuing is not confirming — the lamp must stay dark until confirmed."""
    # Arrange
    sent = _send(store, "operator", "agent-a", "unread")

    # Act
    _inbox.enqueue(
        "agent-a",
        event_type="dm",
        card_id=sent["thread_id"],
        body=sent["body"],
        actor="operator",
        ts=sent["ts"],
        msg_id=sent["id"],
        store=store,
    )

    # Assert
    assert _readers_of(store, sent["id"]) == set()


def test_confirming_a_card_event_writes_no_receipt(store):
    """CONTROL. A bridge that fired for every event type would invent receipts."""
    # Arrange
    sent = _send(store, "operator", "agent-a", "a real message")
    queued = _inbox.enqueue(
        "agent-a",
        event_type="created",
        card_id="card-1",
        body="a card event",
        actor="operator",
        msg_id=sent["id"],
        store=store,
    )

    # Act
    confirm_notifications("agent-a", [queued["id"]], store=store)

    # Assert
    assert _readers_of(store, sent["id"]) == set()


def test_confirming_is_idempotent_for_receipts(store):
    """A retrying consumer must not be punished, and must not double-write."""
    # Arrange
    sent = _send(store, "operator", "agent-a", "twice")
    queued = _inbox.enqueue(
        "agent-a",
        event_type="dm",
        card_id=sent["thread_id"],
        body=sent["body"],
        actor="operator",
        ts=sent["ts"],
        msg_id=sent["id"],
        store=store,
    )
    confirm_notifications("agent-a", [queued["id"]], store=store)

    # Act
    confirm_notifications("agent-a", [queued["id"]], store=store)

    # Assert
    assert _readers_of(store, sent["id"]) == {"agent-a"}


def test_a_legacy_row_without_msg_id_confirms_without_raising(store):
    """Rows enqueued before the column exist; confirming them must still work."""
    # Arrange
    queued = _inbox.enqueue(
        "agent-a",
        event_type="dm",
        card_id="dm:x::y",
        body="legacy",
        actor="operator",
        store=store,
    )

    # Act
    result = confirm_notifications("agent-a", [queued["id"]], store=store)

    # Assert
    assert result["confirmed"] == [queued["id"]]


# EOF
