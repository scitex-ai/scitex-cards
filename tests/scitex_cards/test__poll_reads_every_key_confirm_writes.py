#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``poll_notifications`` must read every inbox key ``ack`` writes.

THE DEFECT, measured in the field by canary-resume-test as an OSCILLATING
``unconfirmed``: ack -> confirmed, next poll -> unconfirmed again, then empty,
then back, with NO ack in between. Reported three times, each reading a single
sample of a moving value.

    poll     _backend.py     recipient_id = user.id if user is not None
                             else agent          <- exactly ONE key
    confirm  _inbox_confirm  recipient_keys()    <- BOTH, acked in a loop
    drain    _mcp_channel    recipient_keys()    <- BOTH

The raw-name inbox and the ``u_*``-id inbox are DIFFERENT ROW SETS — the drain
keeps the raw name deliberately, "for back-compat records keyed by name". And
``resolve_user`` falls back to the raw name on ANY failure, so an intermittent
resolution made poll alternate between two inboxes while confirm covered both.
That produces redelivery-forever with NO concurrency and NO second store.

These tests need neither: a record enqueued under one key and a poll that
resolves to the other is the whole defect, expressible in one process.

A SECOND, INDEPENDENT MECHANISM produces the identical signature and is NOT
covered here: two store instances behind one ``store_uuid`` (sac measured
``poll`` against :55432 returning UNSEEN 0 while the row sat in the store
reached via :5442, and acking THERE cleared it). Fixing only one leaves the
other, and they are indistinguishable in the field — same symptom, and every
call reports success.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_cards import _inbox
from scitex_cards._backend import LocalBackend
from scitex_cards._inbox_confirm import recipient_keys
from scitex_cards._users import register_user


@pytest.fixture
def store(new_store) -> str:
    """A fresh throwaway store, as a DSN. See ``new_store`` in tests/conftest."""
    return new_store()


def _enqueue(key: str, store: Path) -> str:
    """Enqueue one record under ``key`` and return the id the store assigned."""
    record = _inbox.enqueue(
        key,
        event_type="dm",
        card_id="dm:a::b",
        body="hello",
        actor="someone",
        store=store,
    )
    return record["id"]


@pytest.fixture
def registered(store):
    """A REGISTERED agent, whose raw name and user-id are different keys.

    THIS FIXTURE IS WHAT MAKES THIS FILE DISCRIMINATE. My first version used an
    UNregistered name, where `resolve_user` returns None and both the old
    single-key code and the new union code fall back to the same raw name — so
    every test passed with the fix sabotaged out. The two keys must actually
    differ or nothing here is testing anything.
    """
    user = register_user(kind="agent", names=["alice"], store=store)
    assert user.id != "alice"
    return user


def test_a_record_under_the_raw_name_key_is_returned(store, registered):
    # Arrange — enqueued under the RAW name while poll resolves to the user-id:
    # the exact split that made `unconfirmed` oscillate in the field.
    nid = _enqueue("alice", store)
    # Act
    payload = LocalBackend().poll_notifications("alice", store=store)
    # Assert
    assert [n["id"] for n in payload["notifications"]] == [nid]


def test_a_record_under_the_user_id_key_is_also_returned(store, registered):
    # Arrange — the producer's key for a registered name.
    nid = _enqueue(registered.id, store)
    # Act
    payload = LocalBackend().poll_notifications("alice", store=store)
    # Assert
    assert [n["id"] for n in payload["notifications"]] == [nid]


def test_records_under_both_keys_are_both_returned(store, registered):
    # Arrange
    raw_id = _enqueue("alice", store)
    uid_id = _enqueue(registered.id, store)
    # Act
    payload = LocalBackend().poll_notifications("alice", store=store)
    # Assert
    assert sorted(n["id"] for n in payload["notifications"]) == sorted(
        [raw_id, uid_id]
    )


def test_confirming_a_raw_name_record_stops_it_coming_back(store, registered):
    # Arrange — the oscillation itself, stated as its absence.
    backend = LocalBackend()
    nid = _enqueue("alice", store)
    backend.ack_notifications("alice", [nid], store=store)
    # Act — sample repeatedly; one reading of a moving value is not a fact.
    readings = [
        backend.poll_notifications("alice", store=store)["unconfirmed"]
        for _ in range(5)
    ]
    # Assert
    assert readings == [[], [], [], [], []]


def test_every_key_confirm_writes_is_a_key_poll_reads(store):
    # Arrange
    agent = "someagent"
    # Act
    keys = recipient_keys(agent, store)
    # Assert — the shared resolver is the point: poll and confirm cannot
    # disagree by construction once both call this.
    assert keys[0] == agent


def test_a_record_is_not_duplicated_when_two_keys_resolve_to_one_inbox(store):
    # Arrange
    _enqueue("someagent", store)
    # Act
    payload = LocalBackend().poll_notifications("someagent", store=store)
    # Assert
    assert len(payload["notifications"]) == 1


def test_unconfirmed_matches_the_unseen_records_returned(store):
    # Arrange
    _enqueue("someagent", store)
    # Act
    payload = LocalBackend().poll_notifications("someagent", store=store)
    # Assert
    assert payload["unconfirmed"] == [
        n["id"] for n in payload["notifications"] if not n.get("seen")
    ]


def test_confirming_then_polling_leaves_nothing_unconfirmed(store):
    # Arrange
    backend = LocalBackend()
    nid = _enqueue("someagent", store)
    backend.ack_notifications("someagent", [nid], store=store)
    # Act
    payload = backend.poll_notifications("someagent", store=store)
    # Assert — the oscillation, stated as its absence: what ack confirmed must
    # not come back as unconfirmed on the very next read.
    assert payload["unconfirmed"] == []


def test_confirming_then_polling_repeatedly_stays_empty(store):
    # Arrange — sample MORE THAN ONCE, because a single reading of an
    # oscillating value looks exactly like a fact (the reporter's own lesson,
    # learned across three contradictory single-sample reports).
    backend = LocalBackend()
    nid = _enqueue("someagent", store)
    backend.ack_notifications("someagent", [nid], store=store)
    # Act
    readings = [
        backend.poll_notifications("someagent", store=store)["unconfirmed"]
        for _ in range(5)
    ]
    # Assert
    assert readings == [[], [], [], [], []]


# EOF
