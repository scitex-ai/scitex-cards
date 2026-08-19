#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""An unread-inbox reason must name the sender, not just count.

2026-07-28: the operator asked sac for its top-5 tasks TWICE and told it they
were migrating off Telegram onto cards DMs. All three arrived as the number 4,
sat unread for two hours, and sac kept answering on Telegram. The operator
believed they had messaged us, the agent believed nothing had arrived, and both
were right.

A bare count is unactionable BY CONSTRUCTION — it cannot distinguish a direct
question from a card-status echo, so deferring it is the rational response. The
records already carried actor and body (``_threads`` dm-dispatch enqueues them),
so the count was discarding data it already had.

This matters more than an inbox nicety because sac's decider forwards this
``reason`` VERBATIM — deliberately, so it need not know our output format. The
wording here IS the whole user-visible signal.
"""

from __future__ import annotations

import scitex_cards._may_stop as may_stop
from scitex_cards._inbox import enqueue


def _reason_for(records, tmp_path):
    """The (inbox) item's reason, from REAL enqueued notifications.

    No patching. Each record is written into a real store with the package's
    own `enqueue`, and `may_stop` reads it back through the real `poll_inbox`.
    That matters here beyond rule-compliance: this test asserts the WORDING of
    a reason built from `actor` and `body`, and those fields reach the reason
    only if the enqueue path actually persists them. A stubbed `poll_inbox`
    hands back a dict the test wrote itself, so it would stay green even if
    enqueue silently dropped `actor` — which is the exact class of bug (data
    present but discarded) this file was written for.

    An EXPLICIT store is passed: with `store=None` this resolves the real
    canonical board, and the suite's isolation guard rightly fails a test that
    touches the live fleet store.
    """
    store = tmp_path / "cards.db"
    for i, rec in enumerate(records):
        enqueue(
            "scitex-cards",
            event_type="dm",
            card_id="dm:test",
            body=rec.get("body", ""),
            actor=rec.get("actor"),
            msg_id="n_test_%d" % i,
            store=store,
        )
    report = may_stop.may_stop("scitex-cards", store=store)
    for item in report.get("items", []):
        if item.get("card_id") == "(inbox)":
            return item["reason"]
    return ""


def test_the_reason_names_who_sent_it(tmp_path):
    # Arrange — one unread DM from the operator.
    records = [{"actor": "operator", "body": "最重要タスク5つを挙げてください"}]

    # Act
    reason = _reason_for(records, tmp_path)

    # Assert — the sender is the thing that makes it actionable.
    assert "operator" in reason


def test_the_reason_shows_the_message_text(tmp_path):
    # Arrange
    records = [{"actor": "operator", "body": "please list your top five tasks"}]

    # Act
    reason = _reason_for(records, tmp_path)

    # Assert
    assert "top five tasks" in reason


def test_the_reason_still_reports_the_count(tmp_path):
    # Arrange — three unread, so the count still carries information.
    records = [
        {"actor": "operator", "body": "one"},
        {"actor": "scitex-ui", "body": "two"},
        {"actor": "operator", "body": "three"},
    ]

    # Act
    reason = _reason_for(records, tmp_path)

    # Assert
    assert "3" in reason


def test_a_missing_actor_does_not_crash_the_gate(tmp_path):
    """A malformed record must not take down the stop gate.

    This runs on every stop decision for every agent, so a KeyError here would
    be a fleet-wide outage triggered by one bad row.
    """
    # Arrange — no actor, no body.
    records = [{}]

    # Act
    reason = _reason_for(records, tmp_path)

    # Assert
    assert "unknown sender" in reason


def test_a_long_body_is_truncated(tmp_path):
    # Arrange — a body far past the snippet limit.
    records = [{"actor": "operator", "body": "x" * 500}]

    # Act
    reason = _reason_for(records, tmp_path)

    # Assert — the ellipsis proves the cut, and keeps the gate line readable.
    assert "…" in reason
