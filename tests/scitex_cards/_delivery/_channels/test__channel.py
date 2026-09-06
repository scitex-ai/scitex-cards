#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the delivery PORT + the slice-1 LogChannel.

No mocks: the LogChannel writes via the real stdlib logger (captured with
pytest's ``caplog``) and returns a real frozen ``DeliveryResult``.
"""

from __future__ import annotations

import logging

import pytest

from scitex_cards._delivery._channel import DeliveryChannel, DeliveryResult, Status
from scitex_cards._delivery._channels.log import LogChannel

_NOTE = {
    "id": "n_abc",
    "event_type": "reassigned",
    "card_id": "c1",
    "body": "Card c1 reassigned to you",
}


def _deliver_and_capture(caplog):
    """Deliver ``_NOTE`` through a real LogChannel, capturing the log lines."""
    ch = LogChannel()
    with caplog.at_level(logging.INFO, logger="scitex_cards._delivery._channels.log"):
        result = ch.deliver(recipient="u_alice", address="", notification=_NOTE)
    return result, "\n".join(r.getMessage() for r in caplog.records)


def test_status_enum_has_exactly_three_values():
    # Arrange
    expected = {"sent", "failed", "skipped"}
    # Act
    actual = {s.value for s in Status}
    # Assert
    assert actual == expected


def test_delivery_result_detail_defaults_to_none():
    # Arrange
    # Act
    r = DeliveryResult(status=Status.SENT, channel="log")
    # Assert
    assert r.detail is None


def test_delivery_result_status_cannot_be_reassigned():
    # Arrange
    r = DeliveryResult(status=Status.SENT, channel="log")
    # Act
    # Assert — frozen dataclass → cannot reassign
    with pytest.raises(Exception):
        r.status = Status.FAILED


def test_log_channel_satisfies_delivery_protocol():
    # Arrange
    # Act
    ch = LogChannel()
    # Assert
    assert isinstance(ch, DeliveryChannel)


def test_log_channel_reports_its_name_as_log():
    # Arrange
    # Act
    ch = LogChannel()
    # Assert
    assert ch.name == "log"


def test_log_channel_deliver_returns_sent_status(caplog):
    # Arrange
    # Act
    result, _joined = _deliver_and_capture(caplog)
    # Assert
    assert result.status == Status.SENT


def test_log_channel_deliver_returns_log_channel_name(caplog):
    # Arrange
    # Act
    result, _joined = _deliver_and_capture(caplog)
    # Assert
    assert result.channel == "log"


def test_log_channel_line_carries_the_recipient(caplog):
    # Arrange
    # Act
    _result, joined = _deliver_and_capture(caplog)
    # Assert — the human-readable line carries the key fields.
    assert "u_alice" in joined


def test_log_channel_line_carries_the_notification_id(caplog):
    # Arrange
    # Act
    _result, joined = _deliver_and_capture(caplog)
    # Assert
    assert "n_abc" in joined


def test_log_channel_line_carries_the_event_type(caplog):
    # Arrange
    # Act
    _result, joined = _deliver_and_capture(caplog)
    # Assert
    assert "reassigned" in joined


# EOF
