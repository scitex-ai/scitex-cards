#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The migration must move every unseen row, and be safe to run twice.

The rows at stake are real: 1981 unseen on the laptop and 87 on compute-04,
measured 2026-08-09. Those are notifications and DMs that were enqueued,
reported delivered, and never reached anyone. Losing one while "fixing"
delivery would be the same failure with a migration script wrapped around it.

The source shape is built from ``SQLITE_SHAPE`` rather than hand-written,
because an earlier draft of the migration read a table named
``notifications`` from the SQLite side. The SQLite table is ``inbox``, so it
would have migrated NOTHING while reporting success — and "read 0, inserted
0" is indistinguishable from "already done". A fixture that hard-coded the
right names would have hidden that; taking them from the shape is what makes
the test able to catch it.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Iterator

import pytest

pytest.importorskip("psycopg")

from scitex_cards._inbox_shape import SQLITE_SHAPE

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
def clean_destination() -> Iterator[None]:
    """An empty Postgres inbox for each test."""
    import psycopg

    with psycopg.connect(_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM notifications")
    yield


@pytest.fixture
def source_inbox(tmp_path):
    """A per-host SQLite inbox in the REAL legacy shape, with unseen rows."""
    path = tmp_path / "todo.db"
    conn = sqlite3.connect(path)
    conn.execute(
        f"CREATE TABLE {SQLITE_SHAPE.table} ("
        f"id TEXT PRIMARY KEY, {SQLITE_SHAPE.recipient} TEXT, event_type TEXT, "
        "card_id TEXT, body TEXT, actor TEXT, ts TEXT, seen INTEGER, msg_id TEXT)"
    )
    rows = [
        ("n_seen01", "agent-a", "dm", "c1", "already read", "op", "T1", 1, None),
        ("n_unseen1", "agent-a", "dm", "c2", "never delivered", "op", "T2", 0, None),
        ("n_unseen2", "agent-b", "card", "c3", "also undelivered", "op", "T3", 0, None),
    ]
    conn.executemany(
        f"INSERT INTO {SQLITE_SHAPE.table} VALUES(?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()
    return path


def _migrate(source):
    from scitex_cards._inbox_migrate_postgres import (
        migrate_sqlite_inbox_to_postgres,
    )

    return migrate_sqlite_inbox_to_postgres(source, dsn=_DSN, source_label="test-host")


def _count(where: str = "") -> int:
    import psycopg

    with psycopg.connect(_DSN, autocommit=True) as conn:
        return conn.execute(f"SELECT count(*) FROM notifications {where}").fetchone()[0]


class TestItActuallyReadsTheSource:
    """Guards the bug that would look exactly like success."""

    def test_it_reads_the_rows_that_are_there(self, clean_destination, source_inbox):
        """Reading zero from a populated inbox is the silent-failure mode."""
        # Arrange
        # Act
        result = _migrate(source_inbox)
        # Assert
        assert result.read == 3

    def test_a_missing_source_file_is_not_an_error(self, clean_destination, tmp_path):
        """A host that never ran the old inbox has nothing to migrate."""
        # Arrange
        absent = tmp_path / "does-not-exist.db"
        # Act
        result = _migrate(absent)
        # Assert
        assert result.read == 0


class TestUnseenStaysUnseen:
    """The single property whose loss would be silent and unrecoverable."""

    def test_every_unseen_row_arrives(self, clean_destination, source_inbox):
        # Arrange
        # Act
        _migrate(source_inbox)
        # Assert
        assert _count("WHERE seen = 0") == 2

    def test_the_seen_row_stays_seen(self, clean_destination, source_inbox):
        """Marking history unread would re-deliver messages already handled."""
        # Arrange
        # Act
        _migrate(source_inbox)
        # Assert
        assert _count("WHERE seen = 1") == 1

    def test_the_unseen_count_is_reported(self, clean_destination, source_inbox):
        """Counts, not a success flag — 'it worked' is not checkable."""
        # Arrange
        # Act
        result = _migrate(source_inbox)
        # Assert
        assert result.unseen_inserted == 2


class TestRunningItTwiceIsSafe:
    """A migration people fear repeating gets run once, half-way."""

    def test_the_second_run_inserts_nothing(self, clean_destination, source_inbox):
        # Arrange
        _migrate(source_inbox)
        # Act
        second = _migrate(source_inbox)
        # Assert
        assert second.inserted == 0

    def test_the_second_run_reports_them_as_already_present(
        self, clean_destination, source_inbox
    ):
        """Distinguishes a no-op re-run from a failed one."""
        # Arrange
        _migrate(source_inbox)
        # Act
        second = _migrate(source_inbox)
        # Assert
        assert second.skipped_existing == 3

    def test_no_rows_are_duplicated(self, clean_destination, source_inbox):
        # Arrange
        _migrate(source_inbox)
        _migrate(source_inbox)
        # Act
        total = _count()
        # Assert
        assert total == 3

    def test_a_row_acked_since_the_first_run_is_not_resurrected(
        self, clean_destination, source_inbox
    ):
        """Re-running must not undo a delivery the recipient already confirmed."""
        # Arrange
        import psycopg

        _migrate(source_inbox)
        with psycopg.connect(_DSN, autocommit=True) as conn:
            conn.execute("UPDATE notifications SET seen = 1 WHERE id = 'n_unseen1'")
        # Act
        _migrate(source_inbox)
        # Assert
        assert _count("WHERE seen = 0") == 1


class TestTheSourceIsLeftAlone:
    """Additive on purpose: the old inbox is the only other copy."""

    def test_the_source_rows_are_still_there(self, clean_destination, source_inbox):
        # Arrange
        _migrate(source_inbox)
        # Act
        conn = sqlite3.connect(f"file:{source_inbox}?mode=ro", uri=True)
        remaining = conn.execute(
            f"SELECT count(*) FROM {SQLITE_SHAPE.table}"
        ).fetchone()[0]
        conn.close()
        # Assert
        assert remaining == 3


# EOF
