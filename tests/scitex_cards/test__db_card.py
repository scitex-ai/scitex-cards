#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``read_card`` — one indexed row, not a board rebuild.

The primitive `update_task` needs before it can report what it PERSISTED
rather than the merge it was asked to write (see
cards-concurrent-update-reports-success-but-silently-rolls-back). Both other
routes were blocked: the mirror commits unconditionally, so an in-transaction
re-read is impossible, and reading through `load_tasks` rebuilds every card on
a ~5,200-card board.

The tests that matter here are the two that are easy to get wrong: a MISSING
card must be distinguishable from an EMPTY one, and the read must return the
verbatim payload rather than a reassembly from typed columns — otherwise a
field this package has never heard of is silently dropped on the way back,
which is exactly the class of bug the return value is being fixed to avoid.
"""

from __future__ import annotations

import pytest

from scitex_cards._db_card import read_card
from scitex_cards._store import add_task, update_task


@pytest.fixture()
def seeded():
    """One real card written through the real write path.

    No store is named: ``tests/conftest.py`` bootstraps an empty canonical
    database per test and pins every store-selecting env var at it, so the
    writer and `read_card` resolve the same store the same way. Pointing this
    at a bare `tmp_path` would hand them an unprovisioned path, and the store
    reader correctly REFUSES a database that does not exist.
    """
    add_task(
        id="read-me",
        title="Read me",
        status="deferred",
        assignee="alice",
        note="a note",
    )


def test_it_returns_the_stored_card(seeded):
    # Arrange
    # Act
    card = read_card("read-me")

    # Assert
    assert card["title"] == "Read me"


def test_a_missing_id_returns_none(seeded):
    # Arrange
    # Act
    card = read_card("no-such-card")

    # Assert — None means NO ROW, which a caller must be able to tell from a
    # row that exists and carries nothing.
    assert card is None


def test_it_sees_a_write_that_already_landed(seeded):
    """The whole point: it reports the STORE, not a caller's intent.

    `update_task` returns its in-memory merge, so a test that trusted the
    verb's return value would pass even if nothing had been written. This
    reads the row back independently.
    """
    # Arrange
    update_task(task_id="read-me", status="in_progress")

    # Act
    card = read_card("read-me")

    # Assert
    assert card["status"] == "in_progress"


def test_it_round_trips_a_field_the_schema_has_no_column_for(seeded):
    """Decoding `card_json` rather than reassembling from typed columns.

    `_db_payload`'s doctrine is "the typed columns are the INDEX; card_json is
    the TRUTH". A reassembly would drop anything without a column — and the
    fields most likely to lack one are the newest, which is precisely when a
    silent drop is hardest to notice.
    """
    # Arrange — `parked` is a real free-text field with no dedicated column.
    update_task(task_id="read-me", parked="standing goal, children carry it")

    # Act
    card = read_card("read-me")

    # Assert
    assert card["parked"] == "standing goal, children carry it"


def test_an_empty_id_returns_none(seeded):
    # Arrange
    # Act
    card = read_card("")

    # Assert — no lookup, no exception; an empty id is simply not a row.
    assert card is None


# EOF
