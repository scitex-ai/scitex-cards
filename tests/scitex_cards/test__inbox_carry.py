#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The inbox carry must be verifiable under a CONCURRENT DRAIN.

The design these tests pin was a correction to my own plan. The migration was
written as "carry the 1502 unseen rows and check you still have 1502" — a gate on
a quantity the drain repair is designed to change. The count moved 1502 -> 1525
inside one working session, so that gate was already wrong when written.

Two failure modes a count-based gate cannot separate, both exercised below:

  * rows legitimately acked mid-carry (success) look like
  * rows lost by the carry (data loss)

by count. By ID they are trivially distinguishable: an acked row is still
PRESENT, merely seen. So verification is set membership with the target as a
SUPERSET — a floor, never an equality.

The SQLite half runs everywhere. The Postgres half runs when a server is
available and FAILS rather than skips when one is declared, per
SCITEX_CARDS_TEST_PG_DSN.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from scitex_cards._inbox_carry import (
    SOURCE_COLUMNS,
    TARGET_COLUMNS,
    carry_rows,
    read_source_rows,
    source_ids,
    verify_carry,
)

_ENV_PG_DSN = "SCITEX_CARDS_TEST_PG_DSN"
_PG_DSN = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"

_INBOX_DDL = """
CREATE TABLE inbox (
    id TEXT PRIMARY KEY,
    recipient TEXT NOT NULL,
    event_type TEXT,
    card_id TEXT,
    body TEXT,
    actor TEXT,
    ts TEXT,
    seen INTEGER NOT NULL DEFAULT 0,
    msg_id TEXT,
    pushed_at TEXT,
    confirmed_at TEXT
)
"""

#: A SQLite stand-in for the Postgres target, so the carry logic is exercised
#: without a server. Same column names; ON CONFLICT DO NOTHING is understood by
#: both engines, which is why the statement itself needs no dialect branch.
_NOTIFICATIONS_DDL = """
CREATE TABLE notifications (
    id TEXT PRIMARY KEY,
    recipient_id TEXT,
    event_type TEXT,
    card_id TEXT,
    body TEXT,
    actor TEXT,
    ts TEXT,
    seen INTEGER,
    record_json TEXT,
    msg_id TEXT,
    pushed_at TEXT,
    confirmed_at TEXT
)
"""


def _seed(conn, count, *, recipient="agent-a", start=0):
    for i in range(start, start + count):
        conn.execute(
            "INSERT INTO inbox (id, recipient, event_type, card_id, body, "
            "actor, ts, seen, msg_id, pushed_at, confirmed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"n_{i:04d}",
                recipient,
                "dm",
                f"card-{i}",
                f"body {i}",
                None,
                "2026-08-02T00:00:00Z",
                0,
                f"m_{i:04d}",
                None,
                None,
            ),
        )
    conn.commit()


@pytest.fixture
def source():
    conn = sqlite3.connect(":memory:")
    conn.execute(_INBOX_DDL)
    yield conn
    conn.close()


@pytest.fixture
def target():
    conn = sqlite3.connect(":memory:")
    conn.execute(_NOTIFICATIONS_DDL)
    yield conn
    conn.close()


@pytest.fixture
def pg_conn():
    """Live Postgres: skip if UNDECLARED, fail if DECLARED-but-broken."""
    declared = os.environ.get(_ENV_PG_DSN)
    dsn = declared or _PG_DSN
    try:
        import psycopg
    except ImportError:
        if declared:
            pytest.fail(f"{_ENV_PG_DSN} is set but psycopg is not installed")
        pytest.skip("psycopg not installed")
    try:
        conn = psycopg.connect(dsn, connect_timeout=5)
    except Exception as exc:
        if declared:
            pytest.fail(f"{_ENV_PG_DSN} declares {dsn!r} but connecting raised {exc}")
        pytest.skip(f"no live Postgres: {type(exc).__name__}")
    yield conn
    conn.close()


class TestTheColumnMappingIsAligned:
    """A misaligned INSERT would write bodies into timestamps, silently."""

    def test_the_two_column_lists_are_the_same_length(self):
        # Arrange
        pair = (SOURCE_COLUMNS, TARGET_COLUMNS)

        # Act
        lengths = {len(pair[0]), len(pair[1])}

        # Assert
        assert len(lengths) == 1

    def test_recipient_is_the_only_renamed_column(self):
        # Arrange
        pairs = list(zip(SOURCE_COLUMNS, TARGET_COLUMNS))

        # Act
        renamed = [(a, b) for a, b in pairs if a != b]

        # Assert
        assert renamed == [("recipient", "recipient_id")]


class TestTheCarryMovesRows:
    def test_every_source_row_reaches_the_target(self, source, target):
        # Arrange
        _seed(source, 5)

        # Act
        carry_rows(read_source_rows(source), target, placeholder="?")
        result = verify_carry(source, target)

        # Assert
        assert result.missing == set()

    def test_the_carry_is_marked_complete(self, source, target):
        # Arrange
        _seed(source, 5)

        # Act
        carry_rows(read_source_rows(source), target, placeholder="?")
        result = verify_carry(source, target)

        # Assert
        assert result.complete

    def test_a_partial_carry_names_the_missing_ids(self, source, target):
        """Positive control: verification must be able to FAIL.

        A verifier that cannot report loss is not a verifier, and every other
        test here would pass on one that always returns complete.
        """
        # Arrange
        _seed(source, 5)
        rows = read_source_rows(source)

        # Act -- deliberately carry only part of the set
        carry_rows(rows[:3], target, placeholder="?")
        result = verify_carry(source, target)

        # Assert
        assert result.missing == {"n_0003", "n_0004"}


class TestIdempotence:
    def test_re_running_the_carry_does_not_duplicate(self, source, target):
        """An interrupted carry must be resumable.

        A duplicate here is not cosmetic: it is a second delivery of a message
        the recipient already received.
        """
        # Arrange
        _seed(source, 4)
        rows = read_source_rows(source)

        # Act
        carry_rows(rows, target, placeholder="?")
        carry_rows(rows, target, placeholder="?")
        total = target.execute("SELECT count(*) FROM notifications").fetchone()[0]

        # Assert
        assert total == 4


class TestAConcurrentDrainIsNotDataLoss:
    """The scenario a count-based gate gets exactly backwards."""

    def test_rows_acked_during_the_carry_still_verify(self, source, target):
        # Arrange
        _seed(source, 6)
        rows = read_source_rows(source)
        carry_rows(rows, target, placeholder="?")

        # Act -- a drain acks rows AFTER the carry: they are seen, not gone
        source.execute("UPDATE inbox SET seen = 1 WHERE id IN ('n_0000','n_0001')")
        source.commit()
        result = verify_carry(source, target)

        # Assert
        assert result.missing == set()

    def test_the_unseen_count_changes_while_membership_does_not(self, source, target):
        """Demonstrates WHY the gate is by id: the count moves, the ids do not."""
        # Arrange
        _seed(source, 6)
        before = source.execute("SELECT count(*) FROM inbox WHERE seen=0").fetchone()[0]

        # Act
        source.execute("UPDATE inbox SET seen = 1 WHERE id = 'n_0000'")
        source.commit()
        after = source.execute("SELECT count(*) FROM inbox WHERE seen=0").fetchone()[0]

        # Assert -- a gate on this number would have fired on a healthy drain
        assert (before, after) == (6, 5)

    def test_the_id_set_is_unchanged_by_that_same_drain(self, source):
        """The other half of the pair above, and the reason ids are the gate."""
        # Arrange
        _seed(source, 6)
        before = source_ids(source)

        # Act
        source.execute("UPDATE inbox SET seen = 1 WHERE id = 'n_0000'")
        source.commit()
        after = source_ids(source)

        # Assert
        assert before == after


class TestATargetSupersetIsSuccess:
    def test_extra_target_rows_do_not_read_as_a_problem(self, source, target):
        """The target may hold rows the source never had -- a later enqueue, or
        a prior partial carry. Only MISSING ids indicate loss."""
        # Arrange
        _seed(source, 3)
        carry_rows(read_source_rows(source), target, placeholder="?")
        target.execute(
            "INSERT INTO notifications (id, recipient_id) VALUES ('n_9999','agent-z')"
        )

        # Act
        result = verify_carry(source, target)

        # Assert
        assert result.complete


#: Postgres spelling of the target table. The test CREATES IT rather than
#: assuming it, because assuming was measurably wrong: this test passed locally
#: against a store that already had the schema, and failed the moment CI ran it
#: on a fresh postgres:16 with `relation "notifications" does not exist`. A test
#: that depends on ambient state in a shared database is not testing the carry,
#: it is testing whose database it happened to run against.
#:
#: Safe inside the rolled-back transaction below because PostgreSQL DDL is
#: transactional: on a fresh server the table is created and then vanishes with
#: the rollback; on a populated one IF NOT EXISTS makes it a no-op.
_PG_NOTIFICATIONS_DDL = """
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    recipient_id TEXT,
    event_type TEXT,
    card_id TEXT,
    body TEXT,
    actor TEXT,
    ts TEXT,
    seen BIGINT,
    record_json TEXT,
    msg_id TEXT,
    pushed_at TEXT,
    confirmed_at TEXT
)
"""


class TestAgainstRealPostgres:
    """The SQLite stand-in shares a dialect quirk with the real target or it
    does not -- and only the real server can settle that."""

    def test_the_carry_statement_executes_on_postgres(self, pg_conn):
        # Arrange
        src = sqlite3.connect(":memory:")
        src.execute(_INBOX_DDL)
        _seed(src, 2, recipient="carry-test-agent")
        rows = read_source_rows(src)

        # Act
        try:
            with pg_conn.transaction(force_rollback=True):
                pg_conn.execute(_PG_NOTIFICATIONS_DDL)
                written = carry_rows(rows, pg_conn, placeholder="%s")
        finally:
            src.close()

        # Assert
        assert written == 2

    def test_the_carried_rows_are_readable_back_on_postgres(self, pg_conn):
        """Writing without reading back proves the statement PARSED, not that it
        stored anything a later drain could find."""
        # Arrange
        src = sqlite3.connect(":memory:")
        src.execute(_INBOX_DDL)
        _seed(src, 3, recipient="carry-test-agent")
        rows = read_source_rows(src)

        # Act
        try:
            with pg_conn.transaction(force_rollback=True):
                pg_conn.execute(_PG_NOTIFICATIONS_DDL)
                carry_rows(rows, pg_conn, placeholder="%s")
                found = pg_conn.execute(
                    "SELECT count(*) FROM notifications WHERE recipient_id = %s",
                    ("carry-test-agent",),
                ).fetchone()[0]
        finally:
            src.close()

        # Assert
        assert found == 3


# EOF
