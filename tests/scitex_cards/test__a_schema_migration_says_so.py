#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Advancing the SHARED store's schema is announced, not silent.

THE P0's TITLE SAID "SILENTLY" AND IT WAS LITERALLY TRUE. Until 2026-09-06
neither ``_db_init_schema`` nor ``_db_migrations`` contained a logger, a
warning or a print. One client opening the store could run the whole rung
ladder and move the shape under every other client, leaving no trace anywhere
except the ``schema_migrated_*`` provenance rows — whose own docstring says
"THIS IS A RECORD, NOT A GATE". A record nobody is told to read is not a
notification.

WHAT THIS DOES NOT DO, deliberately: it does not gate the migration. The store
is one PostgreSQL primary the whole fleet shares and containers still run a
spread of versions, so refusing here would trade a silent change for a
fleet-wide open failure — the trade this card's owner declined twice, on the
measured ground that "every client is current" has never been establishable.
What it removes is the SILENCE, which the operator ruled out on 2026-09-05
even where the outcome is harmless: 「結果的に問題がなかったとしてもですね。
フォールバックを静かにするっていうのはなしですよ。」

The two tests that assert SILENCE are the load-bearing ones. A warning that
fired on every ordinary open would be read past within a day, and then the
loud path would be worth exactly as much as the silent one it replaced.
"""

import logging

from scitex_cards._db import SCHEMA_VERSION, open_db


def _wind_back_to_the_previous_rung(conn, value: int) -> None:
    """Put the store in the state an OLDER client leaves behind.

    BOTH HALVES ARE REQUIRED, and finding that out is the reason this helper
    has a docstring. Winding back the recorded stamp alone does nothing:
    ``schema_already_current`` decides on the physical SHAPE, not the stamp
    (deliberately — it went shape-based on 2026-08-02 because a stamp
    comparison re-ran the full DDL for every client older than the store). So
    a stamp-only arrangement early-returns before the branch under test and
    the warning never fires, which is precisely how the first draft of this
    file failed: two red tests that looked like a missing log line and were
    actually a test that never reached the code.

    Dropping a v13 column as well makes the shape genuinely behind, which is
    the real state a v12 client leaves — and the migration is additive and
    idempotent, so re-adding it is exactly what the ladder is for.
    """
    conn.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS reopened_at")
    conn.execute(
        "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
        (str(value),),
    )
    conn.commit()


def test_a_genuine_upgrade_is_announced(new_store, caplog):
    """The defect: this produced no output at all."""
    # Arrange
    store = new_store()
    conn = open_db(store)
    try:
        _wind_back_to_the_previous_rung(conn, SCHEMA_VERSION - 1)
    finally:
        conn.close()
    # Act
    with caplog.at_level(logging.WARNING, logger="scitex_cards._db_init_schema"):
        again = open_db(store)
        again.close()
    # Assert
    assert "SCHEMA MIGRATED" in caplog.text


def test_the_announcement_names_both_rungs(new_store, caplog):
    """A bare "migrated" would not let a reader tell WHICH move happened."""
    # Arrange
    store = new_store()
    conn = open_db(store)
    try:
        _wind_back_to_the_previous_rung(conn, SCHEMA_VERSION - 1)
    finally:
        conn.close()
    # Act
    with caplog.at_level(logging.WARNING, logger="scitex_cards._db_init_schema"):
        again = open_db(store)
        again.close()
    # Assert
    assert f"rung {SCHEMA_VERSION - 1} to rung {SCHEMA_VERSION}" in caplog.text


def test_a_fresh_store_is_silent(new_store, caplog):
    """A CREATE is not a migration, and must not cry wolf.

    This is half of what keeps the warning worth reading: every process that
    provisions a throwaway schema would otherwise emit it, which in this test
    suite alone is hundreds of times per run.
    """
    # Arrange
    store = new_store()
    # Act
    with caplog.at_level(logging.WARNING, logger="scitex_cards._db_init_schema"):
        conn = open_db(store)
        conn.close()
    # Assert
    assert "SCHEMA MIGRATED" not in caplog.text


def test_an_already_current_store_is_silent(new_store, caplog):
    """THE ORDINARY CASE, and the other half of the wolf test.

    Every agent, daemon and CLI call in the fleet opens an already-current
    store. If this were noisy the line would be filtered out within a day and
    the genuine event would go back to being invisible — the same fate as the
    tolerated-read warnings nobody reads.
    """
    # Arrange
    store = new_store()
    first = open_db(store)
    first.close()
    # Act
    with caplog.at_level(logging.WARNING, logger="scitex_cards._db_init_schema"):
        second = open_db(store)
        second.close()
    # Assert
    assert "SCHEMA MIGRATED" not in caplog.text


# EOF
