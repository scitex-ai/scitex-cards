#!/usr/bin/env python3
"""A notification row must record WHICH NODE WROTE IT.

WHY THIS FILE EXISTS. `notifications.origin_node` has been in the schema since
the sync-column migration, declared for the stated purpose of surviving a host
boundary -- `_db_sync_columns`: "crosses a host boundary carries origin_node,
row_uuid, revision, updated_at, deleted_at". Measured on the live store
2026-08-21, before this change::

    sweep_state      598 rows   origin_node populated 598  ['scitex-compute-04']
    notifications  7,737 rows   origin_node populated   0
    task_comments 13,191 rows   origin_node populated   0
    tasks          5,719 rows   origin_node populated   0

One writer of four filled it in.

WHAT THAT COST. When stale digests began arriving from an unidentified producer,
the rows could not say where they came from, so the question was chased by
ELIMINATION instead: a six-host unit sweep, a sequence analysis, and two refuted
hypotheses, across two agents and two sessions -- for a fact the row was designed
to carry. Both agents independently concluded the same thing: this column is the
only artifact that could ever have distinguished "sent to me" from "copied into
my store".

WHY IT IS EMITTED BY `notification_columns` RATHER THAN AT A CALL SITE. That
function is the barrier: its own docstring records that three separate writers of
this table each hand-wrote a column list and all three omitted the payload, so
the list is DERIVED from the record instead. `origin_node` was omitted by every
one of them too. Emitting it beside the payload is the same fix for the same
class, and it means a future backend cannot be added without answering "does it
carry an origin, and under what name".

WHY A SHAPE MAY SAY None. The retired single-host rail had no sync columns at
all: it never crossed a boundary, so it carried no origin. The nine-column
INSERT that was correct there and lethal on the store is the reason `payload`
lives in the shape; `origin` follows it for the same reason, stated rather than
implied.

That rail is gone, and with it the second shape -- but ``origin_column=None``
is still a value ``notification_columns`` must handle, so the over-reach
control below passes it DIRECTLY rather than through a constant that no longer
exists. The control is what stops the writer emitting a column unconditionally,
and dropping it because one caller went away would leave the function free to
regress the moment another caller with no origin appears.
"""

import pytest

from scitex_cards._inbox_record import notification_columns, notification_record
from scitex_cards._inbox_shape import POSTGRES_SHAPE


@pytest.fixture
def record():
    return notification_record(
        id="n_test",
        event_type="reminder",
        card_id="some-card",
        body="body",
        actor="an-actor",
        ts="2026-08-21T00:00:00Z",
        seen=False,
        msg_id=None,
    )


def _postgres_columns(record):
    return notification_columns(
        record,
        recipient_id="me",
        recipient_column=POSTGRES_SHAPE.recipient,
        payload_column=POSTGRES_SHAPE.payload,
        origin_column=POSTGRES_SHAPE.origin,
    )


def test_the_postgres_shape_names_the_origin_column():
    # Arrange
    shape = POSTGRES_SHAPE
    # Act
    named = shape.origin
    # Assert
    assert named == "origin_node"


def test_a_postgres_insert_carries_the_origin_column(record):
    # Arrange
    expected = "origin_node"
    # Act
    columns, _ = _postgres_columns(record)
    # Assert
    assert expected in columns


def test_the_origin_value_is_not_empty(record):
    # Arrange — a column present but NULL is the state this change exists to
    # end; emitting the name without a value would satisfy the test above and
    # leave 7,737 more unattributable rows.
    columns, values = _postgres_columns(record)
    # Act
    written = values[columns.index("origin_node")]
    # Assert
    assert written


def test_the_origin_value_matches_the_shared_resolver(record):
    # Arrange — the value must come from `_db_sweep_state._origin_node`, the
    # helper `sweep_state` already uses. A second spelling of "which node am I"
    # is how two answers to one question begin.
    from scitex_cards._db_sweep_state import _origin_node

    expected = _origin_node()
    columns, values = _postgres_columns(record)
    # Act
    written = values[columns.index("origin_node")]
    # Assert
    assert written == expected


def test_a_shape_without_an_origin_column_does_not_carry_it(record):
    # Arrange — THE OVER-REACH CONTROL, and it is not decoration: without it,
    # a writer that emits `origin_node` unconditionally passes every other test
    # in this file. It used to be reached through the retired rail's shape,
    # which really had no such column; the column is now passed as None
    # directly, which is the same condition without depending on a constant
    # that no longer exists.
    # Act
    columns, _ = notification_columns(
        record,
        recipient_id="me",
        recipient_column=POSTGRES_SHAPE.recipient,
        payload_column=POSTGRES_SHAPE.payload,
        origin_column=None,
    )
    # Assert
    assert "origin_node" not in columns


def test_the_payload_is_still_emitted_alongside_it(record):
    # Arrange — the pre-existing invariant must survive. The payload column is
    # what a fleet-wide outage bought on 2026-08-09; adding a neighbour to it
    # must not displace it.
    expected = POSTGRES_SHAPE.payload
    # Act
    columns, _ = _postgres_columns(record)
    # Assert
    assert expected in columns
