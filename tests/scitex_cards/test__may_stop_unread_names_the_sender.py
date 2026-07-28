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


def _reason_for(records, monkeypatch, tmp_path):
    """The (inbox) item's reason, with poll_inbox stubbed to `records`.

    An EXPLICIT store is passed even though both store readers are stubbed:
    with ``store=None`` this resolves the real canonical board, and the suite's
    isolation guard rightly fails a test that touches the live fleet store.
    """
    monkeypatch.setattr(may_stop, "poll_inbox", lambda *a, **k: records)
    monkeypatch.setattr(may_stop, "list_tasks", lambda *a, **k: [])
    report = may_stop.may_stop("scitex-cards", store=tmp_path / "cards.db")
    for item in report.get("items", []):
        if item.get("card_id") == "(inbox)":
            return item["reason"]
    return ""


def test_the_reason_names_who_sent_it(monkeypatch, tmp_path):
    # Arrange — one unread DM from the operator.
    records = [{"actor": "operator", "body": "最重要タスク5つを挙げてください"}]

    # Act
    reason = _reason_for(records, monkeypatch, tmp_path)

    # Assert — the sender is the thing that makes it actionable.
    assert "operator" in reason


def test_the_reason_shows_the_message_text(monkeypatch, tmp_path):
    # Arrange
    records = [{"actor": "operator", "body": "please list your top five tasks"}]

    # Act
    reason = _reason_for(records, monkeypatch, tmp_path)

    # Assert
    assert "top five tasks" in reason


def test_the_reason_still_reports_the_count(monkeypatch, tmp_path):
    # Arrange — three unread, so the count still carries information.
    records = [
        {"actor": "operator", "body": "one"},
        {"actor": "scitex-ui", "body": "two"},
        {"actor": "operator", "body": "three"},
    ]

    # Act
    reason = _reason_for(records, monkeypatch, tmp_path)

    # Assert
    assert "3" in reason


def test_a_missing_actor_does_not_crash_the_gate(monkeypatch, tmp_path):
    """A malformed record must not take down the stop gate.

    This runs on every stop decision for every agent, so a KeyError here would
    be a fleet-wide outage triggered by one bad row.
    """
    # Arrange — no actor, no body.
    records = [{}]

    # Act
    reason = _reason_for(records, monkeypatch, tmp_path)

    # Assert
    assert "unknown sender" in reason


def test_a_long_body_is_truncated(monkeypatch, tmp_path):
    # Arrange — a body far past the snippet limit.
    records = [{"actor": "operator", "body": "x" * 500}]

    # Act
    reason = _reason_for(records, monkeypatch, tmp_path)

    # Assert — the ellipsis proves the cut, and keeps the gate line readable.
    assert "…" in reason
