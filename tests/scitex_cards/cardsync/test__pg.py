#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The PostgreSQL card store: what it reads, and that it refuses to write.

No database and no mock. The row-to-card projection is a pure function, so
it is called with real rows; the write refusal is a real call to the real
method, and the two helpers below turn the raised exception into a value so
each test can carry exactly one assertion.

What is NOT covered here is the psycopg round-trip itself. That needs a live
store, and saying so is better than a fake that would agree with whatever
the real driver did.
"""

from __future__ import annotations

from scitex_cards.cardsync._pg import (
    PgCardStore,
    ReadOnlyStoreError,
    rows_to_cards,
)


def _write_raises(store) -> type[BaseException] | None:
    """The exception TYPE a write raises, or None if it returned."""
    try:
        store.write("c1", '{"id": "c1"}', None)
    except BaseException as exc:  # noqa: BLE001 - the type IS the assertion
        return type(exc)
    return None


def _refusal_message(store) -> str:
    """The refusal text, so a test can assert on what it tells the reader."""
    try:
        store.write("c1", '{"id": "c1"}', None)
    except ReadOnlyStoreError as exc:
        return str(exc)
    return ""


# -- the projection -------------------------------------------------------
def test_rows_become_an_id_keyed_mapping():
    # Arrange
    rows = [("c1", '{"id": "c1"}'), ("c2", '{"id": "c2"}')]
    # Act
    cards = rows_to_cards(rows)
    # Assert
    assert set(cards) == {"c1", "c2"}


def test_the_payload_is_carried_verbatim():
    # Arrange — key order and spacing must survive; the CAS compares bytes
    raw = '{"id":"c1",  "status":"open","note":"a\\nb"}'
    # Act
    cards = rows_to_cards([("c1", raw)])
    # Assert
    assert cards["c1"] == raw


def test_a_null_payload_row_is_dropped():
    # Arrange — a row with no card_json says nothing about the card
    rows = [("c1", '{"id": "c1"}'), ("c2", None)]
    # Act
    cards = rows_to_cards(rows)
    # Assert
    assert "c2" not in cards


def test_dropping_a_null_payload_keeps_the_others():
    # Arrange
    rows = [("c1", '{"id": "c1"}'), ("c2", None)]
    # Act
    cards = rows_to_cards(rows)
    # Assert
    assert cards == {"c1": '{"id": "c1"}'}


def test_an_empty_table_reads_as_an_empty_mapping():
    # Arrange
    rows: list[tuple[str, str]] = []
    # Act
    cards = rows_to_cards(rows)
    # Assert
    assert cards == {}


# -- the refusal ----------------------------------------------------------
def test_writing_raises_rather_than_returning_false():
    # Arrange — False would be counted as a lost race, hiding the no-op
    store = PgCardStore("laptop", "postgresql://nowhere/db")
    # Act
    raised = _write_raises(store)
    # Assert
    assert raised is ReadOnlyStoreError


def test_the_refusal_names_the_store():
    # Arrange
    store = PgCardStore("laptop", "postgresql://nowhere/db")
    # Act
    message = _refusal_message(store)
    # Assert
    assert "laptop" in message


def test_the_refusal_names_what_would_unblock_it():
    # Arrange — an error that only says "no" sends the reader hunting
    store = PgCardStore("laptop", "postgresql://nowhere/db")
    # Act
    message = _refusal_message(store)
    # Assert
    assert "expected_revision" in message


def test_refusing_does_not_need_a_reachable_database():
    # Arrange — the refusal is a property of the store, not of connectivity
    store = PgCardStore("unreachable", "postgresql://127.0.0.1:1/nope")
    # Act
    raised = _write_raises(store)
    # Assert
    assert raised is ReadOnlyStoreError


# -- the store carries the name reconciliation reports against ------------
def test_the_store_exposes_its_name():
    # Arrange
    store = PgCardStore("scitex-04", "postgresql://nowhere/db")
    # Act
    name = store.name
    # Assert
    assert name == "scitex-04"

# EOF
