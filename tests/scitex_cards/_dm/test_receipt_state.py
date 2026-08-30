#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What each delivery state is allowed to claim.

Mirrors ``src/scitex_cards/_dm_receipt_state.py``.

THE ONE RULE EVERY TEST HERE SERVES: ``read`` means CONFIRMED BY THE RECIPIENT.
It is never earned by a transport call returning, by a message merely existing,
or by anyone other than a party who was actually meant to read it. A wrong mark
is worse than no mark at all — operator DMs were acked-without-arriving for
weeks precisely because nothing on screen distinguished sent from received, so
a mark that lies re-creates the outage this feature exists to expose.

Every store here is an EXPLICIT throwaway from the ``new_store`` factory and
every call names its store. Nothing here resolves the ambient store or touches
the live fleet.
"""

from __future__ import annotations

import pytest

from scitex_cards._db import open_db
from scitex_cards._dm.receipt_state import (
    STATE_PENDING,
    STATE_RECEIVED,
    STATE_UNKNOWABLE,
    receipt_state_for_conn,
)
from scitex_cards._dm.write import insert_receipt, record_member_event
from scitex_cards._threads import append_message, mark_read, thread_key


@pytest.fixture()
def store(new_store) -> str:
    """An EXPLICIT throwaway store nobody else can resolve.

    One store serves both halves of every test here on purpose: the writer
    (``append_message`` / ``mark_read`` via ``store=``) and the reader
    (``conn``) MUST be looking at the same database, or a test can pass while
    scoring rows nobody wrote.
    """
    return new_store()


@pytest.fixture()
def conn(store: str):
    """A schema-complete connection to that same throwaway store.

    Closed from a ``finally`` rather than after the assertion: the schema
    teardown runs ``DROP ... CASCADE``, which BLOCKS on an open connection --
    a leak here hangs the run instead of failing it.
    """
    connection = open_db(store)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def key() -> str:
    """The operator <-> agent-x pair thread, named the way the app names it."""
    return thread_key("operator", "agent-x")


def _state(conn, key: str, message_id: str) -> str:
    return receipt_state_for_conn(conn, key)[message_id]["state"]


# --------------------------------------------------------------------------- #
# The three states                                                             #
# --------------------------------------------------------------------------- #
def test_a_stored_message_no_one_confirmed_is_pending(store, conn, key):
    """Durable is not received. This is the state 138 live operator DMs are in.

    The message is in the database and readable, so ``sent`` is earned;
    nobody has confirmed it, so ``read`` is not. Reporting anything else here
    would be the outage rendered as success.
    """
    # Arrange
    sent = append_message("operator", "agent-x", "are you there", store=store)

    # Act
    state = _state(conn, key, sent["id"])

    # Assert
    assert state == STATE_PENDING


def test_the_recipient_confirming_turns_the_message_received(store, conn, key):
    """A receipt written BY the recipient is the one thing that earns ``read``."""
    # Arrange
    sent = append_message("operator", "agent-x", "are you there", store=store)
    mark_read(key, "agent-x", store=store)

    # Act
    state = _state(conn, key, sent["id"])

    # Assert
    assert state == STATE_RECEIVED


def test_a_message_with_no_recipient_at_all_is_unknowable(store, conn, key):
    """Nobody left to confirm it, so "not yet" would be a promise we cannot keep.

    A departed peer is a LEAVE row, not a deleted one, so the message survives
    with an empty audience. That is the honest "cannot tell": confirmation is
    not merely missing, it is no longer expressible.
    """
    # Arrange
    sent = append_message("operator", "agent-x", "are you there", store=store)
    record_member_event(conn, key, "agent-x", "leave")
    conn.commit()

    # Act
    state = _state(conn, key, sent["id"])

    # Assert
    assert state == STATE_UNKNOWABLE


# --------------------------------------------------------------------------- #
# The anti-lie tests — every way ``read`` could be earned dishonestly            #
# --------------------------------------------------------------------------- #
def test_a_sender_reading_their_own_message_never_confirms_it(store, conn, key):
    """The operator re-reading their own DM is not the agent receiving it.

    The sender is removed from the audience before any receipt is counted, so a
    self-receipt cannot reach the tally. Without this, merely opening the thread
    pane would fill the read dot on everything the operator ever sent — a mark that
    means nothing, on every message, forever.
    """
    # Arrange
    sent = append_message("operator", "agent-x", "are you there", store=store)
    insert_receipt(conn, sent["id"], "operator")
    conn.commit()

    # Act
    state = _state(conn, key, sent["id"])

    # Assert
    assert state == STATE_PENDING


def test_a_receipt_from_someone_outside_the_thread_does_not_confirm(store, conn, key):
    """Only a party who could SEE the message can confirm it."""
    # Arrange
    sent = append_message("operator", "agent-x", "are you there", store=store)
    insert_receipt(conn, sent["id"], "some-other-agent")
    conn.commit()

    # Act
    state = _state(conn, key, sent["id"])

    # Assert
    assert state == STATE_PENDING


def test_one_member_confirming_does_not_speak_for_the_others(store, conn, key):
    """In a group thread ``read`` requires EVERY recipient, not the first one.

    Otherwise one live agent's ack would paint a message as received while a
    second, dead agent never saw it — the same "someone got it, so everyone
    did" inference that let the DM outage run for weeks.
    """
    # Arrange
    sent = append_message("operator", "agent-x", "are you there", store=store)
    record_member_event(conn, key, "agent-y", "join")
    insert_receipt(conn, sent["id"], "agent-x")
    conn.commit()

    # Act
    state = _state(conn, key, sent["id"])

    # Assert
    assert state == STATE_PENDING


def test_two_messages_sharing_a_second_and_a_sender_are_scored_apart(store, conn, key):
    """The join is on message id, so a timestamp collision cannot leak a mark.

    MEASURED on the live store: two distinct durable messages shared one
    ``(thread, ts, sender)`` tuple — that tuple is also the inbox dedupe key and
    DM stamps are second-resolution, so matching on it is many-to-one BY
    CONSTRUCTION. Scoring off that join marks a message nothing was
    ever delivered for, which is exactly the failure these marks exist to catch.
    """
    # Arrange
    stamp = "2026-07-29T12:00:00Z"
    first = append_message("operator", "agent-x", "same second", store=store, ts=stamp)
    second = append_message("operator", "agent-x", "same second", store=store, ts=stamp)
    insert_receipt(conn, first["id"], "agent-x")
    conn.commit()

    # Act
    states = receipt_state_for_conn(conn, key)

    # Assert
    assert (states[first["id"]]["state"], states[second["id"]]["state"]) == (
        STATE_RECEIVED,
        STATE_PENDING,
    )


# --------------------------------------------------------------------------- #
# What the payload carries                                                     #
# --------------------------------------------------------------------------- #
def test_readers_names_who_actually_confirmed(store, conn, key):
    """The mark's tooltip can say WHO, so the state is auditable, not just a
    colour."""
    # Arrange
    sent = append_message("operator", "agent-x", "are you there", store=store)
    mark_read(key, "agent-x", store=store)

    # Act
    readers = receipt_state_for_conn(conn, key)[sent["id"]]["readers"]

    # Assert
    assert readers == ["agent-x"]


def test_every_live_message_gets_an_entry(store, conn, key):
    """No gaps: a client never has to invent a state for a missing key."""
    # Arrange
    first = append_message("operator", "agent-x", "one", store=store)
    second = append_message("operator", "agent-x", "two", store=store)

    # Act
    states = receipt_state_for_conn(conn, key)

    # Assert
    assert set(states) == {first["id"], second["id"]}


# EOF
