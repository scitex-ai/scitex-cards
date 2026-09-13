#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PostgreSQL doorbells wake consumers without becoming a data rail."""

import scitex_cards
from scitex_cards import _notification_watch as notification_watch
from scitex_cards._inbox_postgres import enqueue, poll_inbox


def _remote_watch_error():
    try:
        scitex_cards.watch_notifications("agent:remote")
    except Exception as exc:  # noqa: BLE001 - asserted by the caller
        return exc
    return None


def test_watch_is_ready_before_enqueue_and_yields_only_a_hint():
    # Arrange
    recipient = "agent:watch-target"
    with scitex_cards.watch_notifications(recipient, timeout=2.0) as events:
        # Act
        record = enqueue(
            recipient,
            event_type="dm",
            card_id="dm:a::b",
            body="durable body",
            actor="agent:sender",
            msg_id="m_watch",
            exchange_id="xch_20260912T000000Z_test_abcdef",
        )
        hint = next(events)
    # Assert
    assert (
        record is not None and record["id"].startswith("n_"),
        hint["recipient_id"],
        hint["hint"],
        hint["status"]["code"],
        set(hint["status"]),
        "poll_notifications" in hint["status"]["message"],
        "body" not in hint and "exchange_id" not in hint,
    ) == (
        True,
        recipient,
        "poll_notifications",
        202,
        {"kind", "code", "message"},
        True,
        True,
    )


def test_watch_timeout_is_a_normal_empty_iteration():
    # Arrange
    with scitex_cards.watch_notifications(
        "agent:no-event", timeout=0.01
    ) as events:
        # Act
        observed = list(events)
    # Assert
    assert observed == []


def test_explicit_notification_dsn_is_not_inferred_from_store_port(env):
    # Arrange
    direct = "postgresql://direct-session-endpoint:6543/cards"
    env.set("SCITEX_CARDS_NOTIFY_DSN", direct)
    # Act
    resolved = notification_watch.resolve_notification_dsn(
        "postgresql://transaction-pool:55432/cards"
    )
    # Assert
    assert resolved == direct


def test_invalid_explicit_notification_dsn_fails_loud(env):
    # Arrange
    env.set("SCITEX_CARDS_NOTIFY_DSN", "55433")
    # Act
    try:
        notification_watch.resolve_notification_dsn()
    except ValueError as exc:
        detail = str(exc)
    else:
        detail = ""
    # Assert
    assert "SCITEX_CARDS_NOTIFY_DSN" in detail and "PostgreSQL DSN" in detail


def test_one_doorbell_drains_multiple_durable_rows():
    # Arrange
    recipient = "agent:coalesced"
    with scitex_cards.watch_notifications(recipient, timeout=2.0) as events:
        enqueue(
            recipient,
            event_type="dm",
            card_id="dm:a::c",
            body="one",
            actor="agent:sender",
            msg_id="m_coalesce_1",
        )
        enqueue(
            recipient,
            event_type="dm",
            card_id="dm:a::c",
            body="two",
            actor="agent:sender",
            msg_id="m_coalesce_2",
        )
        # Act
        hint = next(events)
        drained = poll_inbox(recipient)
    # Assert
    assert (
        "coalesce" in hint["status"]["message"],
        [row["body"] for row in drained],
    ) == (True, ["one", "two"])


def test_remote_watch_refuses_with_canonical_actionable_status(env):
    # Arrange
    env.set("SCITEX_CARDS_HUB_URL", "http://127.0.0.1:9")
    # Act
    captured = _remote_watch_error()
    # Assert
    status = captured.status.to_dict()
    assert (
        isinstance(captured, scitex_cards.NotificationWatchUnavailable),
        set(status),
        status["code"],
        "poll_notifications" in status["message"],
    ) == (True, {"kind", "code", "message"}, 503, True)


# EOF
