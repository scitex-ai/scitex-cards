#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``GET /dm/thread/<peer>`` must carry the delivery state ALONGSIDE the
messages.

Mirrors the ``receipts`` key added in
``src/scitex_cards/_django/handlers/dm.py``.

WHY ALONGSIDE AND NOT INSIDE. A stored DM record is immutable
(``docs/design/dm-into-cards-db.md`` §3.2) and older clients already understand
its exact shape. Folding a mark into the message would break both at once — for
a value that is not part of the message anyway: "did this arrive" is a fact
ABOUT the message, held in a different table, written by a different party.
``reactions`` already established this seat; ``receipts`` takes the next one.

Django RequestFactory against a REAL store via ``?store=``; no mocks
(STX-NM / PA-306). AAA pattern, one assertion per test (STX-TQ007).

THE STORE IS THIS TEST'S OWN THROWAWAY POSTGRESQL SCHEMA. It used to be a
scratch ``tasks.yaml`` with "the database lands next to it" -- true while a
store was a file, and now describing nothing: a filename names no store and
the doors refuse it.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlencode

import pytest
from django.test import RequestFactory

from scitex_cards._django.handlers.dm import dm_thread_view
from scitex_cards._threads import append_message, mark_read, thread_key


@pytest.fixture()
def store(env) -> str:
    """This test's own throwaway PostgreSQL schema.

    FAILS rather than skips when the harness did not pin one: a skipped storage
    test and a passing one are indistinguishable in a summary line.
    """
    env.set("SCITEX_CARDS_STORE_GIT_AUTOCOMMIT", "0")
    dsn = os.environ.get("SCITEX_CARDS_DB", "")
    if "search_path" not in dsn:
        pytest.fail(
            "the root conftest did not pin $SCITEX_CARDS_DB to a throwaway "
            f"PostgreSQL schema; it holds {dsn!r}.",
            pytrace=False,
        )
    return dsn


def _q(store, **extra) -> str:
    """The query string for ``store``, ENCODED.

    A DSN carries ``?options=-csearch_path%3D<schema>``. Interpolated raw, its
    ``?`` starts a second query and the view receives a store truncated at the
    schema -- a wrong store that parses.
    """
    return urlencode({"store": str(store), **extra})


def _thread(store: str) -> dict:
    """The endpoint's payload for the operator <-> agent-x thread.

    ``mark_read`` is deliberately NOT passed: the pane's own poll acks as the
    OPERATOR, which must never be mistaken for the agent confirming.
    """
    request = RequestFactory().get(f"/dm/thread/agent-x?{_q(store)}")
    return json.loads(dm_thread_view(request, "agent-x").content)


def test_the_payload_carries_a_receipt_entry_for_the_message(store):
    """The mark has data to render from, in the same round trip as the
    message."""
    # Arrange
    sent = append_message("operator", "agent-x", "are you there", store=store)

    # Act
    receipts = _thread(store)["receipts"]

    # Assert
    assert sent["id"] in receipts


def test_an_unconfirmed_message_is_reported_pending(store):
    """The state 138 live operator DMs are in, served honestly."""
    # Arrange
    sent = append_message("operator", "agent-x", "are you there", store=store)

    # Act
    state = _thread(store)["receipts"][sent["id"]]["state"]

    # Assert
    assert state == "pending"


def test_the_agent_confirming_is_reported_received(store):
    """A receipt written by the recipient reaches the wire as ``received``."""
    # Arrange
    sent = append_message("operator", "agent-x", "are you there", store=store)
    mark_read(thread_key("operator", "agent-x"), "agent-x", store=store)

    # Act
    state = _thread(store)["receipts"][sent["id"]]["state"]

    # Assert
    assert state == "received"


def test_the_operator_opening_the_pane_does_not_confirm_their_own_message(store):
    """The pane acks as the operator on every poll — that must not fill the read dot.

    This is the render-path twin of the module's anti-lie test, and it is the
    one that would bite in production: the operator merely LOOKING at the thread
    must never make their own outgoing message read as delivered.
    """
    # Arrange
    sent = append_message("operator", "agent-x", "are you there", store=store)
    request = RequestFactory().get(f"/dm/thread/agent-x?{_q(store, mark_read='1')}")

    # Act
    payload = json.loads(dm_thread_view(request, "agent-x").content)

    # Assert
    assert payload["receipts"][sent["id"]]["state"] == "pending"


def test_the_messages_are_unchanged_by_the_new_key(store):
    """A client that ignores ``receipts`` sees exactly the payload it always
    did."""
    # Arrange
    append_message("operator", "agent-x", "are you there", store=store)
    baseline = _thread(store)["messages"]

    # Act
    again = _thread(store)["messages"]

    # Assert
    assert again == baseline


def test_no_receipt_field_is_folded_into_a_message_record(store):
    """The immutable record stays immutable — the state rides beside it."""
    # Arrange
    append_message("operator", "agent-x", "are you there", store=store)

    # Act
    keys = set(_thread(store)["messages"][0])

    # Assert
    assert keys.isdisjoint({"state", "receipts", "readers"})


# EOF
