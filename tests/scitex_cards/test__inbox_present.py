#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRESENTATION — never demand an ack for a message you did not show.

The outage this rail exists for: delivery had ONE rail (the MCP channel push),
a spec/registration name mismatch made Claude Code discard every push, and
``send()`` kept returning normally while three weeks of operator DMs vanished
(228 inbox rows, ZERO unseen).

The FIX for that could easily have been a second outage. A hook that simply
refused a stop while unacked messages existed would have DEADLOCKED every agent
that morning: the messages had never been shown, so no agent COULD have acked
them. So :func:`~scitex_cards._inbox_present.present` returns the rendered text
AND the ids that text contains, and the ONLY ids a caller may demand are the
second value. These tests pin that pairing, not the wording — wording is free
to change, the pairing is the safety property.

No mocks. Real store, real ``_inbox`` records: the bug being guarded against
was mock-shaped (every layer reported success while nothing arrived), so a
mocked store here would test the wrong thing on purpose.
"""

from __future__ import annotations

import os

from scitex_cards import _inbox
from scitex_cards._inbox_present import (
    MAX_PRESENTED,
    PRESENT_BUDGET_CHARS,
    pending,
    present,
)
from scitex_cards._users import register_user

AGENT = "present-tester"


def _store():
    """The per-test scratch store the suite's conftest pinned us to."""
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def _seed(count=1, agent=AGENT, body="operator DM", store=None):
    """Enqueue ``count`` distinct unseen notifications; return their ids."""
    target = store or _store()
    return [
        _inbox.enqueue(
            agent,
            event_type="dm",
            card_id=f"dm:operator::{agent}",
            body=f"{body} {i}",
            actor="operator",
            ts=f"2026-07-29T07:{i:02d}:00Z",
            store=target,
        )["id"]
        for i in range(count)
    ]


def _records(count, body_chars=200):
    """Synthetic records, large enough to exercise the budget."""
    return [
        {
            "id": f"n_{i:04d}",
            "actor": "operator",
            "event_type": "dm",
            "card_id": "dm:operator::x",
            "body": "x" * body_chars,
            "ts": f"2026-07-29T07:{i:02d}:00Z",
        }
        for i in range(count)
    ]


# --------------------------------------------------------------------------- #
# The pull is a PURE READ                                                     #
# --------------------------------------------------------------------------- #
def test_pending_returns_the_unseen_notification():
    # Arrange
    ids = _seed(1)

    # Act
    got = pending(AGENT, _store())

    # Assert
    assert [r["id"] for r in got] == ids


def test_pending_does_not_advance_the_cursor():
    """READING NEVER CONFIRMS. A read that acks turns a transient failure into
    permanent loss — the exact defect that destroyed five operator DMs."""
    # Arrange
    _seed(1)
    pending(AGENT, _store())

    # Act
    still_unseen = _inbox.poll_inbox(
        AGENT, unseen_only=True, mark_seen=False, store=_store()
    )

    # Assert
    assert len(still_unseen) == 1


def test_pending_finds_notifications_filed_under_the_resolved_user_id():
    """A producer files under the stable ``u_*`` id for a REGISTERED agent.
    Reading only the raw name is the same silent-miss shape as the outage:
    the message is in the store, readable, and the reader looks elsewhere."""
    # Arrange
    user = register_user(kind="agent", names=["u-keyed-agent"], store=_store())
    filed = _inbox.enqueue(
        user.id,
        event_type="dm",
        card_id="dm:operator::u-keyed-agent",
        body="filed under the resolved id",
        actor="operator",
        store=_store(),
    )["id"]

    # Act
    got = pending("u-keyed-agent", _store())

    # Assert
    assert [r["id"] for r in got] == [filed]


def test_pending_reports_nothing_for_an_agent_with_no_mail():
    # Arrange — nothing enqueued for this name.

    # Act
    got = pending("nobody-writes-to-me", _store())

    # Assert
    assert got == []


# --------------------------------------------------------------------------- #
# THE DEADLOCK GUARD: presented ids ARE the shown ids                         #
# --------------------------------------------------------------------------- #
def test_every_presented_id_has_its_own_text_in_the_rendering():
    """LOAD-BEARING. The caller demands acks for exactly these ids, so an id
    here whose content is absent is an ack demanded for an unseen message."""
    # Arrange
    records = _records(MAX_PRESENTED * 3)

    # Act
    text, ids = present(records)

    # Assert
    assert all(nid in text for nid in ids)


def test_a_withheld_message_is_not_demanded():
    """Overflow is NOT lost and NOT demanded — it stays unconfirmed, so the
    next poll returns it and the next turn presents it."""
    # Arrange
    records = _records(MAX_PRESENTED * 3)

    # Act
    _text, ids = present(records)

    # Assert
    assert len(ids) < len(records)


def test_a_withheld_message_is_counted_out_loud():
    """An omission the agent cannot see is a lie about their inbox."""
    # Arrange
    records = _records(MAX_PRESENTED * 3)

    # Act
    text, _ids = present(records)

    # Assert
    assert "more unread" in text


def test_a_record_with_no_id_is_never_presented():
    """An id-less record cannot be acked, so demanding one would deadlock."""
    # Arrange
    records = [{"actor": "operator", "body": "malformed, no id"}]

    # Act
    _text, ids = present(records)

    # Assert
    assert ids == []


def test_a_record_with_no_id_renders_nothing_to_block_on():
    # Arrange
    records = [{"actor": "operator", "body": "malformed, no id"}]

    # Act
    text, _ids = present(records)

    # Assert
    assert text == ""


def test_no_records_presents_no_ids():
    # Arrange — an empty inbox.

    # Act
    _text, ids = present([])

    # Assert
    assert ids == []


# --------------------------------------------------------------------------- #
# The message itself must actually be in there                                #
# --------------------------------------------------------------------------- #
def test_the_rendering_carries_the_message_body():
    """A count is unactionable BY CONSTRUCTION: it cannot distinguish an
    operator question from a card-status echo, so deferring it is rational."""
    # Arrange
    records = [{"id": "n_1", "actor": "operator", "body": "please confirm receipt"}]

    # Act
    text, _ids = present(records)

    # Assert
    assert "please confirm receipt" in text


def test_the_rendering_names_the_sender():
    # Arrange
    records = [{"id": "n_1", "actor": "operator", "body": "hello"}]

    # Act
    text, _ids = present(records)

    # Assert
    assert "operator" in text


def test_a_missing_actor_does_not_break_the_rendering():
    # Arrange
    records = [{"id": "n_1", "body": "hello"}]

    # Act
    text, _ids = present(records)

    # Assert
    assert "unknown sender" in text


# --------------------------------------------------------------------------- #
# BUDGET — over-cap output is spilled to a file, i.e. NOT presented           #
# --------------------------------------------------------------------------- #
def test_the_rendering_respects_the_character_budget():
    """Claude Code caps injected hook context at 10,000 chars and spills the
    overflow to a file. A presentation that got spilled was not presented."""
    # Arrange
    records = _records(200, body_chars=2000)

    # Act
    text, _ids = present(records)

    # Assert
    assert len(text) <= PRESENT_BUDGET_CHARS * 1.2


def test_one_enormous_message_is_still_shown():
    """An unshown message is the failure mode; an ugly one is not."""
    # Arrange
    records = [{"id": "n_1", "actor": "operator", "body": "y" * 50_000}]

    # Act
    _text, ids = present(records)

    # Assert
    assert ids == ["n_1"]


def test_the_message_cap_bounds_how_many_are_shown():
    # Arrange
    records = _records(MAX_PRESENTED * 5, body_chars=1)

    # Act
    _text, ids = present(records)

    # Assert
    assert len(ids) == MAX_PRESENTED


# EOF
