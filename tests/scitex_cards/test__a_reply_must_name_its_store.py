#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A SPLIT STORE IS INVISIBLE UNLESS BOTH REPLIES NAME THEIR TARGET.

THE DEFECT. An agent polls its inbox from one store and confirms against
another — two containers whose `SCITEX_CARDS_DB` resolved differently, a
restart that silently repointed a loopback DSN from one port to another. Every
call SUCCEEDS. The poll returns nothing and the confirmation answers
``unknown`` for every id, and BOTH of those are exactly what a correct call
against a correct store looks like when there is genuinely nothing there.

That is the whole hazard: the failure is reported in the vocabulary of an
ordinary empty result, so the consumer stops looking. ``unknown`` in
particular reads as "those ids do not exist" — a statement about the ids,
when the truth is a statement about the DATABASE.

WHAT THESE PIN. Both replies carry ``store``: the target the call actually
used, resolved from the same argument the read/write went through. One label
alone is a value with nothing to compare against; the PAIR is the instrument.
Two that DISAGREE name the fault outright, which is the only thing the
consumer could not previously learn.

AND THE INSTRUMENT IS ONE-SIDED, which is pinned here because it is easy to
forget and dangerous to forget:

    DIFFER -> a split, positively identified
    AGREE  -> only that THIS CLIENT read and wrote in one place

Agreement is NOT proof that no split exists. The carded incident had the
delivery daemon on ``:5442`` while the client's poll and its acks were BOTH on
``:55432`` — two agreeing labels, four notifications still arriving from a
third store. The daemon resolves its own target and stamps nothing, so no
comparison available here can see it. Agreement is CANNOT-TELL, and
``test_a_poll_and_a_confirm_on_one_store_agree`` is therefore the WEAKEST test
in this file: it passes even against an implementation that reports the
ambient store for both. The disagreement test is the one with teeth.

DELIBERATELY A LABEL, NOT AN IDENTITY. ``instance_id`` would be strictly
stronger — it separates two different databases reached through one identical
loopback DSN, which a label cannot. It also OPENS A CONNECTION, and this is
the poll/ack path that every agent runs in a loop. The cheap half ships first;
see the card for the reasoning and what the expensive half would add.

No mocks (STX-NM): real stores, real records, the real backend verbs. The
split is produced by genuinely pointing the two calls at two different files,
because a simulated split would prove only that the simulation works.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards import _inbox
from scitex_cards._backend import LocalBackend
from scitex_cards._inbox_confirm import confirm_notifications

#: An UNREGISTERED recipient — the raw-name inbox key, so no registration step
#: can stand between the enqueue and the poll.
AGENT = "split-store-consumer"


@pytest.fixture()
def inbox(env):
    """A real store holding one UNSEEN notification for one raw-name agent."""
    env.set("SCITEX_CARDS_AGENT_ID", "split-store-tester")
    env.delete("SCITEX_CARDS_HUB_URL")
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    record = _inbox.enqueue(
        AGENT,
        event_type="commented",
        card_id="card-1",
        body="operator DM",
        actor="operator",
        ts="2026-08-12T00:00:00Z",
        store=store,
    )
    return {"store": store, "ids": [record["id"]]}


# --------------------------------------------------------------------------- #
# Each reply names the store it used                                          #
# --------------------------------------------------------------------------- #
def test_the_poll_names_the_store_it_read(inbox):
    # Arrange
    backend = LocalBackend()
    # Act
    payload = backend.poll_notifications(AGENT, store=inbox["store"])
    # Assert
    assert payload["store"] == inbox["store"]


def test_the_confirmation_names_the_store_it_wrote(inbox):
    # Arrange
    ids = inbox["ids"]
    # Act
    payload = confirm_notifications(AGENT, ids, store=inbox["store"])
    # Assert
    assert payload["store"] == inbox["store"]


# --------------------------------------------------------------------------- #
# The PAIR is the instrument — one label answers nothing on its own           #
# --------------------------------------------------------------------------- #
def test_a_poll_and_a_confirm_on_one_store_agree(inbox):
    # Arrange
    polled = LocalBackend().poll_notifications(AGENT, store=inbox["store"])
    # Act
    confirmed = confirm_notifications(AGENT, inbox["ids"], store=inbox["store"])
    # Assert
    assert confirmed["store"] == polled["store"]


def test_a_split_store_makes_the_two_replies_disagree(inbox, tmp_path):
    """THE POINT. Without this the split is reported as an ordinary empty result.

    Also the negative control for the two tests above: if either label were
    resolved from the AMBIENT environment rather than from the argument the
    call actually used, both would report the same store here and this would
    fail. A field that says the same thing regardless of what the call did is
    not a measurement of anything.
    """
    # Arrange
    elsewhere = str(tmp_path / "a-second-store.yaml")
    # Act
    polled = LocalBackend().poll_notifications(AGENT, store=inbox["store"])
    confirmed = confirm_notifications(AGENT, inbox["ids"], store=elsewhere)
    # Assert
    assert confirmed["store"] != polled["store"]


def test_the_split_confirmation_still_reports_unknown(inbox, tmp_path):
    """The old, indistinguishable signal is UNCHANGED — this adds, not replaces.

    Pinned deliberately: `unknown` remains exactly what a wrong-store
    confirmation answers, which is why the label had to be added rather than
    the classification changed. A consumer that already branches on `unknown`
    keeps working.
    """
    # Arrange
    elsewhere = str(tmp_path / "a-second-store.yaml")
    # Act
    confirmed = confirm_notifications(AGENT, inbox["ids"], store=elsewhere)
    # Assert
    assert confirmed["unknown"] == inbox["ids"]


# EOF
