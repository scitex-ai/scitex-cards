#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The postgres enqueue must write `record_json`, and this must be checkable in CI.

WHY THIS FILE EXISTS — 2026-08-11, three fleet-wide outages of card WRITES.
`_inbox_postgres.enqueue` listed nine columns and left `record_json` out, so
every notification it wrote landed with a NULL payload. A NULL payload makes the
read guard refuse the WHOLE DATABASE, and since the YAML fallback was removed
that means add_task / update_task / comment_task all fail — over a notification
row, which cannot make a card write lossy.

WHY THE OBVIOUS TEST WOULD NOT HAVE CAUGHT IT. The natural test is to call
`enqueue` and read the row back. But every test in `test__inbox_postgres.py` is
`skipif` on `$SCITEX_CARDS_TEST_DSN`, which is unset in CI — 12 skips on the run
that shipped this defect. A guard that only runs when someone remembers to point
it at a scratch database is not a guard for the environment where the outage
happened. That reasoning is #803's and it still holds: the primary check here
must run with NO database.

WHAT CHANGED, AND WHY THE CHECK MOVED. #803's always-runs check read the INSERT
out of the function's own source with a regex and asserted the literal string
`record_json` appeared in it. That guarded a SPELLING. The column list is no
longer spelled in this module at all: it is DERIVED from the record by
`_inbox_record.notification_columns`, precisely because three separate writers
of this table each hand-wrote their own list and all three omitted the payload.

So the always-runs check now calls that function and inspects what it produces.
This is strictly stronger than the regex — it exercises the code that builds the
real statement rather than the text of one call site, it cannot pass vacuously
on a reformat, and it covers every writer that goes through the constructor
rather than this one. The end-to-end check below is unchanged and still runs
wherever a scratch DSN exists.
"""

import os

import pytest

from scitex_cards import _inbox_postgres as pg
from scitex_cards._inbox_record import notification_columns, notification_record


def _columns_the_enqueue_would_write() -> tuple:
    """The column list `enqueue` builds, without touching a database."""
    record = notification_record(
        id="n_000000000001",
        event_type="commented",
        card_id="card-payload-guard",
        body="payload guard",
        actor="tester",
        ts="2026-08-11T22:03:54Z",
    )
    columns, _values = notification_columns(
        record,
        recipient_id="payload-guard-agent",
        recipient_column=pg._RECIPIENT,
        payload_column=pg._SHAPE.payload,
    )
    return columns


def test_the_postgres_shape_declares_a_payload_column():
    """POSITIVE CONTROL. The column list is built from the shape, so a shape
    that stopped naming a payload column would make every check below pass
    vacuously while the outage returned."""
    # Arrange
    shape = pg._SHAPE
    # Act
    payload_column = shape.payload
    # Assert
    assert payload_column == "record_json"


def test_the_written_columns_include_record_json():
    """THE FIX. `record_json` must be among the columns written."""
    # Arrange
    # Act
    columns = _columns_the_enqueue_would_write()
    # Assert
    assert "record_json" in columns


def test_every_column_has_a_value():
    """Guards a DIFFERENT bug: a column list and a VALUES list of unequal
    length, which raises at execution.

    Under the derived construction this is now true BY CONSTRUCTION — the two
    tuples are built in one pass — which is the point. It is kept because a
    mismatch is the failure a future refactor of that function would produce,
    and it is cheap to exclude.
    """
    # Arrange
    record = notification_record(
        id="n_000000000001",
        event_type="commented",
        card_id="c",
        body="b",
        actor="tester",
        ts="2026-08-11T22:03:54Z",
    )
    # Act
    columns, values = notification_columns(
        record, recipient_id="r", recipient_column=pg._RECIPIENT
    )
    # Assert
    assert len(columns) == len(values)


def test_the_payload_is_built_from_the_record_the_function_returns():
    """The record dict is built, returned to the caller, and must be the SAME
    object handed to the database — not a second construction that could drift
    from it. Deriving the columns FROM the record is what guarantees that: the
    payload cannot describe a different record than the one returned."""
    import json

    # Arrange
    record = notification_record(
        id="n_000000000001",
        event_type="commented",
        card_id="c",
        body="the body the caller sees",
        actor="tester",
        ts="2026-08-11T22:03:54Z",
    )
    # Act
    columns, values = notification_columns(
        record, recipient_id="r", recipient_column=pg._RECIPIENT
    )
    stored = json.loads(values[columns.index("record_json")])
    # Assert
    assert stored == record


# --------------------------------------------------------------------------- #
# The end-to-end check. Stronger, but only runs where a scratch DSN exists —    #
# which is why it is NOT the primary guard above.                              #
# --------------------------------------------------------------------------- #
_DSN = os.environ.get("SCITEX_CARDS_TEST_DSN")


@pytest.mark.skipif(not _DSN, reason="$SCITEX_CARDS_TEST_DSN unset; writes rows")
def test_an_enqueued_row_lands_with_a_payload():
    """The assertion the outage actually needed: read the row back and confirm
    the database holds a payload, not a NULL."""
    # Arrange
    import psycopg

    record = pg.enqueue(
        "payload-guard-agent",
        event_type="commented",
        card_id="card-payload-guard",
        body="payload guard",
        actor="tester",
        store=_DSN,
    )
    # Act
    with psycopg.connect(_DSN, autocommit=True) as conn:
        row = conn.execute(
            "SELECT record_json FROM notifications WHERE id = %s", (record["id"],)
        ).fetchone()
    # Assert
    assert row[0] is not None


# EOF
