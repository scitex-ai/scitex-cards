#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Receipts must be written where the notifications are.

THE DEFECT THIS CLOSES, measured on the live store 2026-08-11 23:30Z::

    notifications rows                     8
    rows with pushed_at set                0
    rows with confirmed_at set             0
    ~/.scitex/cards/.inboxes.json.lock     present, 22:04 that day
    ~/.scitex/cards/inboxes.json           DOES NOT EXIST

#780 moved enqueue/poll/ack into PostgreSQL; ``_inbox_receipt`` did not come
along, and its dispatch asked a TWO-valued question (``_use_sqlite()``) of a
THREE-valued world. So the shared-inbox case fell into the file branch: every
push receipt and every recipient confirmation went to a JSON sidecar that was
never even created, while the rows they described sat in PostgreSQL. The lock
file with no file beside it is the proof — the file rail was taken at runtime,
found no such recipient, and reported success to a caller that had just
delivered a message.

WHAT IT COST. ``pushed_at`` never landed, so the delivery doctor built after the
2026-07-29 loss of five operator DMs had nothing to go red about — it was
reading a different database from the one under test. ``confirmed_at`` never
landed, so ``unconfirmed_ids`` reported "nothing outstanding" for every agent on
a rail where everything was outstanding.

Skipped, loudly, when no throwaway PostgreSQL is configured: these WRITE, so
they must never run against the live store. Set SCITEX_CARDS_TEST_DSN to a
scratch database or schema.
"""

from __future__ import annotations

import os
from typing import Iterator

import pytest

pytest.importorskip("psycopg")

TEST_DSN_ENV = "SCITEX_CARDS_TEST_DSN"
_DSN = os.environ.get(TEST_DSN_ENV)

pytestmark = pytest.mark.skipif(
    not _DSN,
    reason=(
        f"${TEST_DSN_ENV} is unset. These tests WRITE receipts, so they need a "
        "throwaway database or schema — never the live store."
    ),
)

_RECIPIENT = "agent-receipts"
_STAMP = "2026-08-11T23:30:00Z"


@pytest.fixture
def postgres_mode() -> Iterator[None]:
    """Select the shared inbox for the duration of one test, then restore.

    Real environment variables, set and unset by hand. The backend resolver
    reads ``os.environ``, and which backend it picks is exactly what this file
    exists to pin down — a fixture that rewrote the resolver instead would prove
    nothing about the deployment the bug was found in.
    """
    keys = {"SCITEX_CARDS_INBOX_BACKEND": "postgres", "SCITEX_CARDS_INBOX_DSN": _DSN}
    before = {key: os.environ.get(key) for key in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for key, was in before.items():
            if was is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = was


@pytest.fixture
def rail(postgres_mode) -> Iterator[object]:
    """The receipt backend under test, on a clean table, in postgres mode."""
    import psycopg

    from scitex_cards import _inbox_postgres as inbox
    from scitex_cards import _inbox_receipt_postgres as receipts

    with psycopg.connect(_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM notifications")
    inbox.enqueue(
        _RECIPIENT,
        event_type="dm",
        card_id="c1",
        body="hello",
        actor="operator",
        ts="2026-08-11T23:00:00Z",
        store=_DSN,
    )
    yield receipts


@pytest.fixture
def enqueued_id(rail) -> str:
    """The id of the one notification these tests stamp."""
    from scitex_cards import _inbox_postgres as inbox

    return inbox.poll_inbox(_RECIPIENT, store=_DSN)[0]["id"]


class TestAPushIsRecordedOnTheRowItDescribes:
    def test_the_push_stamp_lands_in_the_database(self, rail, enqueued_id):
        # Arrange
        from scitex_cards._inbox_receipt import PUSHED_AT

        # Act
        rail.stamp(
            _RECIPIENT,
            [enqueued_id],
            column=PUSHED_AT,
            stamp=_STAMP,
            advance_cursor=True,
            store=_DSN,
        )

        # Assert
        got = rail.receipts(_RECIPIENT, _DSN)[0]
        assert got[PUSHED_AT] == _STAMP

    def test_it_reports_the_id_it_stamped(self, rail, enqueued_id):
        """The caller reports these as delivered, so they must be real."""
        # Arrange
        from scitex_cards._inbox_receipt import PUSHED_AT

        # Act
        stamped = rail.stamp(
            _RECIPIENT,
            [enqueued_id],
            column=PUSHED_AT,
            stamp=_STAMP,
            advance_cursor=True,
            store=_DSN,
        )

        # Assert
        assert stamped == [enqueued_id]

    def test_a_push_advances_the_cursor_in_the_same_write(self, rail, enqueued_id):
        """One statement, so a crash cannot leave the two facts disagreeing."""
        # Arrange
        from scitex_cards._inbox_receipt import PUSHED_AT

        # Act
        rail.stamp(
            _RECIPIENT,
            [enqueued_id],
            column=PUSHED_AT,
            stamp=_STAMP,
            advance_cursor=True,
            store=_DSN,
        )

        # Assert
        assert rail.receipts(_RECIPIENT, _DSN)[0]["seen"] is True


class TestTheFirstStampWins:
    def test_a_retried_push_keeps_the_original_time(self, rail, enqueued_id):
        """Age must measure how long it went unanswered, not the last retry."""
        # Arrange
        from scitex_cards._inbox_receipt import PUSHED_AT

        rail.stamp(
            _RECIPIENT,
            [enqueued_id],
            column=PUSHED_AT,
            stamp=_STAMP,
            advance_cursor=True,
            store=_DSN,
        )

        # Act
        rail.stamp(
            _RECIPIENT,
            [enqueued_id],
            column=PUSHED_AT,
            stamp="2026-08-12T09:00:00Z",
            advance_cursor=True,
            store=_DSN,
        )

        # Assert
        assert rail.receipts(_RECIPIENT, _DSN)[0][PUSHED_AT] == _STAMP


class TestConfirmationIsADifferentFactFromTheCursor:
    def test_confirming_does_not_touch_seen(self, rail, enqueued_id):
        """Conflating the two is the whole defect the receipts exist for."""
        # Arrange
        from scitex_cards._inbox_receipt import CONFIRMED_AT

        # Act
        rail.stamp(
            _RECIPIENT,
            [enqueued_id],
            column=CONFIRMED_AT,
            stamp=_STAMP,
            advance_cursor=False,
            store=_DSN,
        )

        # Assert
        assert rail.receipts(_RECIPIENT, _DSN)[0]["seen"] is False

    def test_the_confirmation_stamp_lands(self, rail, enqueued_id):
        """``confirmed_at`` is the ONLY evidence a recipient received it."""
        # Arrange
        from scitex_cards._inbox_receipt import CONFIRMED_AT

        # Act
        rail.stamp(
            _RECIPIENT,
            [enqueued_id],
            column=CONFIRMED_AT,
            stamp=_STAMP,
            advance_cursor=False,
            store=_DSN,
        )

        # Assert
        assert rail.receipts(_RECIPIENT, _DSN)[0][CONFIRMED_AT] == _STAMP


class TestAnUnknownIdIsNotStamped:
    def test_it_returns_nothing_for_an_id_this_recipient_does_not_have(
        self, rail, enqueued_id
    ):
        """Reporting a stamp that did not happen is how a message gets lost."""
        # Arrange
        from scitex_cards._inbox_receipt import PUSHED_AT

        # Act
        stamped = rail.stamp(
            _RECIPIENT,
            ["n_not_here"],
            column=PUSHED_AT,
            stamp=_STAMP,
            advance_cursor=True,
            store=_DSN,
        )

        # Assert
        assert stamped == []


class TestTheStampTargetIsNotCallerData:
    def test_a_column_that_is_not_a_receipt_is_refused(self, rail, enqueued_id):
        """The name is interpolated into SQL; this is what keeps that safe."""
        # Arrange
        bogus = "seen = 1; DROP TABLE notifications; --"

        # Act
        def stamping():
            rail.stamp(
                _RECIPIENT,
                [enqueued_id],
                column=bogus,
                stamp=_STAMP,
                advance_cursor=False,
                store=_DSN,
            )

        # Assert
        with pytest.raises(ValueError):
            stamping()


class TestReadingIsReadOnly:
    def test_receipts_of_an_unknown_recipient_is_empty(self, rail):
        """A doctor must be able to measure without creating what it measures."""
        # Arrange
        nobody = "agent-who-never-existed"

        # Act
        got = rail.receipts(nobody, _DSN)

        # Assert
        assert got == []


# EOF
