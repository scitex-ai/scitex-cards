#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The shared inbox must cross hosts, and must never lose an unseen message.

These are the proofs the fix is accountable for, kept as tests rather than a
one-off script so they run again every time someone touches the backend.

The cross-host case is simulated the way it actually fails: a DIFFERENT
HOME and a DIFFERENT cwd for the reader, so nothing local could satisfy the
read even if the code tried to. That is precisely what the old per-host
retired per-host inbox did — 4901 rows on one machine, 162 on another, and the
operator's messages reaching nobody.

Skipped, loudly, when no throwaway Postgres is configured: these must never
run against the live store. Set SCITEX_CARDS_TEST_DSN to a scratch database
or schema.
"""

from __future__ import annotations

import json
import os
from typing import Iterator

import pytest

from scitex_cards._inbox_record import NOTIFICATION_RECORD_KEYS

pytest.importorskip("psycopg")

TEST_DSN_ENV = "SCITEX_CARDS_TEST_DSN"
_DSN = os.environ.get(TEST_DSN_ENV)

pytestmark = pytest.mark.skipif(
    not _DSN,
    reason=(
        f"${TEST_DSN_ENV} is unset. These tests WRITE notifications, so they "
        "need a throwaway database or schema — never the live store."
    ),
)


@pytest.fixture
def inbox() -> Iterator[object]:
    """The backend under test, with a clean table for each test."""
    import psycopg

    from scitex_cards import _inbox_postgres as pg

    with psycopg.connect(_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM notifications")
    yield pg


@pytest.fixture
def elsewhere(tmp_path) -> Iterator[None]:
    """Move HOME and cwd, so a local file cannot satisfy the next read."""
    old_home, old_cwd = os.environ.get("HOME"), os.getcwd()
    other = tmp_path / "another-host"
    other.mkdir()
    os.environ["HOME"] = str(other)
    os.chdir(other)
    try:
        yield
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        os.chdir(old_cwd)


@pytest.fixture
def unreachable_message(inbox) -> str:
    """The refusal text from an unreachable DSN.

    The raising call lives here so each test below asserts exactly once —
    a test that both raises and inspects hides the second check behind the
    first failure.
    """
    dead = "postgresql://nobody@127.0.0.1:59999/nothing"
    with pytest.raises(inbox.InboxUnavailableError) as excinfo:
        inbox.enqueue(
            "agent-x", event_type="dm", card_id="c9", body="x",
            actor="op", store=dead,
        )
    return str(excinfo.value)


def _enqueue(pg, recipient="agent-x", body="hello", **kw):
    kw.setdefault("event_type", "dm")
    kw.setdefault("card_id", "c1")
    kw.setdefault("actor", "operator")
    kw.setdefault("ts", "2026-08-09T19:00:00Z")
    return pg.enqueue(recipient, body=body, store=_DSN, **kw)


class TestItCrossesHosts:
    """The whole point: enqueued on one machine, readable on another."""

    def test_a_notification_enqueued_here_is_visible_from_another_home(
        self, inbox, elsewhere
    ):
        # Arrange
        _enqueue(inbox, body="hello from A")
        # Act
        got = inbox.poll_inbox("agent-x", store=_DSN)
        # Assert
        assert len(got) == 1

    def test_the_body_survives_the_crossing(self, inbox, elsewhere):
        # Arrange
        _enqueue(inbox, body="hello from A")
        # Act
        got = inbox.poll_inbox("agent-x", store=_DSN)
        # Assert
        assert got[0]["body"] == "hello from A"

    def test_it_arrives_unseen(self, inbox, elsewhere):
        """A delivered-but-unread message is the state that must survive."""
        # Arrange
        _enqueue(inbox)
        # Act
        got = inbox.poll_inbox("agent-x", store=_DSN)
        # Assert
        assert got[0]["seen"] is False


class TestReadingNeverMarksSeen:
    """Handing a message over is not the same as confirming it arrived."""

    def test_polling_twice_still_returns_it(self, inbox):
        # Arrange
        _enqueue(inbox)
        inbox.poll_inbox("agent-x", store=_DSN)
        # Act
        again = inbox.poll_inbox("agent-x", store=_DSN)
        # Assert
        assert len(again) == 1

    def test_a_consumer_that_dies_before_acking_loses_nothing(self, inbox):
        """The 2026-07-29 incident: ack-on-read destroyed five operator DMs."""
        # Arrange
        _enqueue(inbox)
        inbox.poll_inbox("agent-x", store=_DSN)  # consumer dies here
        # Act
        survived = inbox.poll_inbox("agent-x", store=_DSN)
        # Assert
        assert len(survived) == 1


class TestAckIsIdempotent:
    """A retrying consumer must not be punished for retrying."""

    def test_the_first_ack_reports_the_flip(self, inbox):
        # Arrange
        rec = _enqueue(inbox)
        # Act
        flipped = inbox.ack("agent-x", [rec["id"]], store=_DSN)
        # Assert
        assert flipped == [rec["id"]]

    def test_the_second_ack_is_a_no_op(self, inbox):
        # Arrange
        rec = _enqueue(inbox)
        inbox.ack("agent-x", [rec["id"]], store=_DSN)
        # Act
        again = inbox.ack("agent-x", [rec["id"]], store=_DSN)
        # Assert
        assert again == []

    def test_an_acked_message_leaves_the_unseen_view(self, inbox):
        # Arrange
        rec = _enqueue(inbox)
        inbox.ack("agent-x", [rec["id"]], store=_DSN)
        # Act
        unseen = inbox.poll_inbox("agent-x", store=_DSN)
        # Assert
        assert unseen == []

    def test_an_acked_message_is_still_in_the_history(self, inbox):
        """Acked means delivered, not deleted."""
        # Arrange
        rec = _enqueue(inbox)
        inbox.ack("agent-x", [rec["id"]], store=_DSN)
        # Act
        everything = inbox.poll_inbox("agent-x", unseen_only=False, store=_DSN)
        # Assert
        assert len(everything) == 1


class TestEnqueueIsIdempotent:
    """A re-dispatch of the same message must not double-deliver."""

    def test_the_same_msg_id_is_deduped(self, inbox):
        # Arrange
        _enqueue(inbox, recipient="agent-y", msg_id="m-1")
        # Act
        second = _enqueue(inbox, recipient="agent-y", msg_id="m-1")
        # Assert
        assert second is None

    def test_only_one_row_results(self, inbox):
        # Arrange
        _enqueue(inbox, recipient="agent-y", msg_id="m-1")
        _enqueue(inbox, recipient="agent-y", msg_id="m-1")
        # Act
        got = inbox.poll_inbox("agent-y", store=_DSN)
        # Assert
        assert len(got) == 1

    def test_two_distinct_messages_in_the_same_second_both_survive(self, inbox):
        """DM timestamps are second-resolution, so ts alone is many-to-one.

        Measured on the live store: two distinct durable messages collapsed
        onto one notification and the second was never delivered. `msg_id`
        makes the dedup key exact.
        """
        # Arrange
        _enqueue(inbox, recipient="agent-z", body="first", msg_id="m-A")
        _enqueue(inbox, recipient="agent-z", body="second", msg_id="m-B")
        # Act
        got = inbox.poll_inbox("agent-z", store=_DSN)
        # Assert
        assert len(got) == 2


class TestItRefusesToFallBack:
    """A silent local fallback is the bug, re-implemented on purpose."""

    def test_an_unreachable_dsn_raises(self, inbox):
        # Arrange
        dead = "postgresql://nobody@127.0.0.1:59999/nothing"
        # Act
        # Assert
        with pytest.raises(inbox.InboxUnavailableError):
            inbox.enqueue(
                "agent-x", event_type="dm", card_id="c9", body="x",
                actor="op", store=dead,
            )

    def test_the_error_names_where_it_tried_to_go(self, unreachable_message):
        """An error that does not name the target is unactionable."""
        # Arrange
        message = unreachable_message
        # Act
        names_the_port = "59999" in message
        # Assert
        assert names_the_port

    def test_the_error_says_it_is_not_falling_back(self, unreachable_message):
        # Arrange
        message = unreachable_message
        # Act
        refuses = "falling back" in message.lower()
        # Assert
        assert refuses


class TestWhatItWritesCanBeReadBack:
    """An enqueue whose row the reader refuses has not delivered anything.

    ``notifications.record_json`` holds each record VERBATIM and the read path
    reconstructs from it, refusing a row that has none. This backend wrote the
    typed columns only, so every notification it enqueued was unreadable —
    and because the read assembles the WHOLE document, ONE such row failed
    every card write fleet-wide: add_task, update_task, comment_task.
    Measured 2026-08-11, it took the board down three times in one night.
    """

    def _stored_payload(self, recipient="agent-x"):
        """The record_json the last enqueue actually wrote, straight from SQL."""
        import psycopg

        with psycopg.connect(_DSN) as conn:
            row = conn.execute(
                "SELECT record_json FROM notifications WHERE recipient_id = %s",
                (recipient,),
            ).fetchone()
        return row[0]

    def test_an_enqueued_row_has_a_payload(self, inbox):
        # Arrange
        _enqueue(inbox)
        # Act
        stored = self._stored_payload()
        # Assert — NULL here is the fleet-wide outage.
        assert stored is not None

    def test_the_payload_reproduces_the_enqueued_body(self, inbox):
        # Arrange
        _enqueue(inbox, body="the body that must survive")
        # Act
        stored = json.loads(self._stored_payload())
        # Assert
        assert stored["body"] == "the body that must survive"

    def test_the_payload_carries_seen_as_a_json_bool(self, inbox):
        # Arrange
        _enqueue(inbox)
        # Act
        stored = json.loads(self._stored_payload())
        # Assert — the column is an INTEGER; the record contract is a bool.
        assert stored["seen"] is False

    def test_the_payload_holds_exactly_the_record_keys(self, inbox):
        # Arrange
        _enqueue(inbox)
        # Act
        stored = json.loads(self._stored_payload())
        # Assert — recipient_id and seq are columns, NOT part of the record.
        assert tuple(stored) == NOTIFICATION_RECORD_KEYS

    def test_the_whole_database_stays_exportable_after_an_enqueue(self, inbox):
        """The end-to-end proof: the read path accepts what the writer wrote."""
        import psycopg

        from scitex_cards._db_export import export_doc

        # Arrange
        _enqueue(inbox)
        # Act
        with psycopg.connect(_DSN) as conn:
            conn.row_factory = psycopg.rows.dict_row
            doc, _ = export_doc(conn=conn)
        # Assert — before the fix this raised ExportRefused for every caller.
        assert doc["inboxes"]["agent-x"][0]["body"] == "hello"


# EOF
