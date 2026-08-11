#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A notification and its payload are ONE object, so no writer can omit half.

The defect these pin: three separate writers of the ``notifications`` table
each spelled their own INSERT column list, and all three omitted
``record_json``. The export/read path reconstructs every record from that
verbatim payload and refuses a row without one — and because that read
assembles the WHOLE document, a single payload-less row failed every card
write fleet-wide. Measured 2026-08-11: rows written 22:03, 22:17 and 22:50
took the board down three times in one night.

So the tests here are not about a helper's return value. They are about
whether the field-less shape is still REPRESENTABLE.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from scitex_cards._inbox_record import (
    NOTIFICATION_RECORD_KEYS,
    notification_columns,
    notification_payload,
    notification_record,
    rebuild_notification_record,
)


@pytest.fixture
def record() -> dict:
    """One enqueued notification, in the shape every backend builds."""
    return notification_record(
        id="n_000000000001",
        event_type="completed",
        card_id="card-1",
        body="a card was completed",
        actor="agent-a",
        ts="2026-08-11T22:03:54Z",
        seen=False,
        msg_id=None,
    )


def _row(**overrides) -> sqlite3.Row:
    """A ``notifications`` row, as the read path sees one."""
    columns = {
        "id": "n_000000000001",
        "recipient_id": "u_1",
        "event_type": "completed",
        "card_id": "card-1",
        "body": "a card was completed",
        "actor": "agent-a",
        "ts": "2026-08-11T22:03:54Z",
        "seen": 0,
        "msg_id": None,
        "record_json": None,
    }
    columns.update(overrides)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    names = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    conn.execute(f"CREATE TABLE notifications ({names})")
    conn.execute(f"INSERT INTO notifications ({names}) VALUES ({marks})", tuple(columns.values()))
    return conn.execute("SELECT * FROM notifications").fetchone()


def test_the_record_carries_exactly_the_documented_keys(record):
    # Arrange
    # Act
    # Assert — the shape the reader reconstructs; recipient_id and seq are NOT in it.
    assert tuple(record) == NOTIFICATION_RECORD_KEYS


def test_seen_is_a_json_bool_not_an_integer(record):
    # Arrange
    # Act
    decoded = json.loads(notification_payload(record))

    # Assert — the column is an INTEGER, the payload is a bool.
    assert decoded["seen"] is False


def test_the_payload_preserves_the_records_own_key_order(record):
    # Arrange
    # Act
    decoded = json.loads(notification_payload(record))

    # Assert — the export reproduces each record's key order, so this must too.
    assert tuple(decoded) == NOTIFICATION_RECORD_KEYS


def test_the_insert_columns_always_carry_the_payload(record):
    # Arrange
    # Act
    columns, _ = notification_columns(
        record, recipient_id="u_1", recipient_column="recipient_id"
    )

    # Assert — THE BARRIER: there is no spelling of this call that omits it.
    assert "record_json" in columns


def test_the_payload_value_travels_with_its_column(record):
    # Arrange
    # Act
    columns, values = notification_columns(
        record, recipient_id="u_1", recipient_column="recipient_id"
    )

    # Assert — column list and value tuple stay aligned by construction.
    assert json.loads(values[columns.index("record_json")])["id"] == record["id"]


def test_seen_reaches_the_column_as_an_integer(record):
    # Arrange
    # Act
    columns, values = notification_columns(
        record, recipient_id="u_1", recipient_column="recipient_id"
    )

    # Assert — the column is declared INTEGER NOT NULL.
    assert values[columns.index("seen")] == 0


def test_a_table_without_a_payload_column_is_named_not_assumed(record):
    # Arrange — the SQLite `inbox` table genuinely has no payload column.
    # Act
    columns, _ = notification_columns(
        record,
        recipient_id="u_1",
        recipient_column="recipient",
        payload_column=None,
    )

    # Assert
    assert "record_json" not in columns


def test_a_payload_less_row_rebuilds_to_the_record_that_wrote_it(record):
    # Arrange — the row a column-only writer left behind.
    row = _row()

    # Act
    rebuilt = rebuild_notification_record(row)

    # Assert — exact, not approximate: this is why the reader may repair.
    assert rebuilt == record


def test_the_rebuild_reproduces_the_key_order_too():
    # Arrange
    row = _row()

    # Act
    rebuilt = rebuild_notification_record(row)

    # Assert
    assert tuple(rebuilt) == NOTIFICATION_RECORD_KEYS


def test_a_row_missing_a_not_null_column_is_not_invented():
    # Arrange — no event_type means no recoverable record.
    row = _row(event_type="")

    # Act
    rebuilt = rebuild_notification_record(row)

    # Assert — None, never a partial record: inventing one is the stripped
    # export the payload column exists to prevent.
    assert rebuilt is None


def test_a_row_predating_the_msg_id_column_still_rebuilds():
    # Arrange — a DB written before msg_id existed has no such column.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE notifications (id, recipient_id, event_type, card_id, "
        "body, actor, ts, seen, record_json)"
    )
    conn.execute(
        "INSERT INTO notifications VALUES ('n_1','u_1','dm','c','b','a',"
        "'2026-08-11T22:03:54Z',0,NULL)"
    )
    row = conn.execute("SELECT * FROM notifications").fetchone()

    # Act
    rebuilt = rebuild_notification_record(row)

    # Assert — absent column reads as None, not as a failure to rebuild.
    assert rebuilt["msg_id"] is None


# EOF
