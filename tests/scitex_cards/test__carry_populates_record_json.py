#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A carried notification must arrive with its payload, not with a NULL.

WHY THIS FILE EXISTS — the 2026-08-09 fleet board outage, filed by
scitex-agent-container-04. ONE carried row took every card verb down for ~20
minutes: 3556 cards unreadable and unwritable, while `resolve_store` and
`health` kept working because the store itself was fine. One malformed
notification refused everything.

    ExportRefused: notifications row 'n_211db00605cf' has no record_json
    payload — this DB predates schema v3's payload columns and cannot be
    back-filled ... Exporting stripped records is worse than exporting none.

THE NULL WAS BY CONSTRUCTION, not bad luck. `record_json` is in neither
SOURCE_COLUMNS nor TARGET_COLUMNS, and it CANNOT be in the source tuple: the
SQLite `inbox` table has no such column. So every row the carry ever wrote
landed with a NULL payload. The triggering row was repaired by hand; the writer
that produced it was not, which is why the incident card says the same outage
recurs on the next carry at N rows instead of one.

AND THE GUARD'S ESCAPE HATCH IS GONE. `card_payload_json`'s docstring says the
NULL is load-bearing — it makes the read guard refuse the whole DB "instead of
quietly handing back a card whose fields changed shape on the way through". That
was a good trade WHILE it fell back to YAML. The YAML tier was removed, so
"refuse and degrade" silently became "refuse and die". A guard whose fallback
was deleted is not the same guard.

This file covers the WRITER (fix 1 of 2). The guard's behaviour in postgres mode
with nothing beneath it is fix 2 and is tracked separately — refusing the RECORD
loudly keeps the board up; refusing the STORE converts one bad row into a total
outage, which is worse than either option the guard was choosing between.
"""

import json
import sqlite3

from scitex_cards._inbox_carry import (
    SOURCE_COLUMNS,
    TARGET_COLUMNS,
    carry_rows,
    payload_for_row,
)

#: The target table, matching the shape `carry_rows` writes into. Built here so
#: the end-to-end test exercises the real INSERT rather than the helper alone —
#: the outage came from the STATEMENT omitting a column, which a unit test of
#: the payload builder would never have caught.
_NOTIFICATIONS_DDL = """
CREATE TABLE notifications(
    id TEXT PRIMARY KEY, recipient_id TEXT, event_type TEXT, card_id TEXT,
    body TEXT, actor TEXT, ts TEXT, seen INTEGER, msg_id TEXT,
    pushed_at TEXT, confirmed_at TEXT, record_json TEXT)
"""

#: The actual row from the incident, in SOURCE_COLUMNS order.
INCIDENT_ROW = (
    "n_211db00605cf",
    "scitex-cards",
    "dm",
    "rail-test",
    "c2c rail test: enqueued into PostgreSQL",
    "operator",
    "2026-08-09T14:03:42Z",
    0,
    None,
    None,
    None,
)


def test_the_source_really_has_no_payload_column_to_copy():
    """POSITIVE CONTROL for the whole premise. If `record_json` were available
    in the source tuple, the fix would be a copy and this synthesis would be
    unnecessary — so the absence is what makes the rest of the file meaningful."""
    # Arrange
    both = set(SOURCE_COLUMNS) | set(TARGET_COLUMNS)
    # Act
    present = "record_json" in both
    # Assert
    assert present is False


def test_a_carried_row_gets_a_payload():
    """THE FIX, on the exact row that took the board down."""
    # Arrange
    row = INCIDENT_ROW
    # Act
    payload = payload_for_row(row)
    # Assert
    assert payload is not None


def test_the_payload_round_trips_to_the_source_record():
    """Lossless: every source field except the recipient survives the trip, so
    a reader reconstructing from the payload sees what the sidecar held."""
    # Arrange
    expected = {
        name: value
        for name, value in zip(SOURCE_COLUMNS, INCIDENT_ROW)
        if name != "recipient"
    }
    # Act
    restored = json.loads(payload_for_row(INCIDENT_ROW))
    # Assert
    assert restored == expected


def test_the_payload_does_not_carry_the_recipient():
    """THE QUESTION THE INCIDENT REPORT COULD NOT ANSWER FROM OUTSIDE.

    It asked whether the payload should carry `recipient` (the source column the
    carry reads) or `recipient_id` (the target column it writes), noting the two
    are indistinguishable until something reads the payload back.

    Neither. `_db_sections._insert_notifications` — the normal writer — iterates
    `inboxes[recipient_id]` and stores `card_payload_json(r)` where `r` is the
    record inside that recipient's list. The recipient is the map KEY and lives
    in the `recipient_id` COLUMN; it has never been a record field. Embedding it
    here would make two writers produce different shapes for the same logical
    row, which is the divergence the payload exists to prevent."""
    # Arrange
    restored = json.loads(payload_for_row(INCIDENT_ROW))
    # Act
    recipient_keys = {"recipient", "recipient_id"} & set(restored)
    # Assert
    assert recipient_keys == set()


def test_the_id_survives_so_the_payload_is_attributable():
    """A payload that cannot be tied back to its row is not evidence of
    anything — and `id` is what the refusal message names when it fires."""
    # Arrange
    row = INCIDENT_ROW
    # Act
    restored = json.loads(payload_for_row(row))
    # Assert
    assert restored["id"] == "n_211db00605cf"


def test_carry_rows_writes_a_payload_end_to_end():
    """THE ONE THAT WOULD HAVE CAUGHT THE OUTAGE. The defect was in the INSERT
    STATEMENT — `record_json` was in neither column tuple — so a unit test of
    the payload builder alone would have passed while the board stayed down.

    Measured against the unfixed writer on this exact row:
        UNFIXED carry_rows -> ('n_211db00605cf', None)
    """
    # Arrange
    conn = sqlite3.connect(":memory:")
    conn.execute(_NOTIFICATIONS_DDL)
    # Act
    carry_rows([INCIDENT_ROW], conn, placeholder="?")
    # Assert
    assert conn.execute("SELECT record_json FROM notifications").fetchone()[0]


def test_carry_rows_still_writes_the_recipient_to_its_column():
    """Keeping the recipient OUT of the payload must not drop it from the ROW.
    It moves to the `recipient_id` column, which is where the normal writer puts
    it and where every reader looks."""
    # Arrange
    conn = sqlite3.connect(":memory:")
    conn.execute(_NOTIFICATIONS_DDL)
    # Act
    carry_rows([INCIDENT_ROW], conn, placeholder="?")
    # Assert
    assert conn.execute("SELECT recipient_id FROM notifications").fetchone()[0] == (
        "scitex-cards"
    )


def test_carry_rows_is_still_idempotent_by_id():
    """The payload column must not break ON CONFLICT DO NOTHING. Re-running a
    carry is normal — an interrupted run must resume without duplicating, and a
    duplicate here is a second delivery of a message already received."""
    # Arrange
    conn = sqlite3.connect(":memory:")
    conn.execute(_NOTIFICATIONS_DDL)
    carry_rows([INCIDENT_ROW], conn, placeholder="?")
    # Act
    carry_rows([INCIDENT_ROW], conn, placeholder="?")
    # Assert
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1


def test_an_unrepresentable_record_still_yields_none():
    """The NULL contract is PRESERVED, deliberately. `card_payload_json` returns
    None for a record that cannot round-trip through JSON, and that refusal is
    correct — the bug was emitting NULL for records that were perfectly
    representable and simply never asked. A fix that made this function
    incapable of returning None would delete a real guard while closing a bug."""
    # Arrange
    unrepresentable = tuple(
        object() if name == "body" else value
        for name, value in zip(SOURCE_COLUMNS, INCIDENT_ROW)
    )
    # Act
    payload = payload_for_row(unrepresentable)
    # Assert
    assert payload is None


# EOF
