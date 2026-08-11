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
happened.

So the PRIMARY check here is STRUCTURAL and always runs: the INSERT is read out
of the function's own source. The defect was a missing column in a statement,
and that is statically visible. The end-to-end check below it is the stronger
assertion and runs when a DSN exists — both, not either.
"""

import inspect
import os
import re

import pytest

from scitex_cards import _inbox_postgres as pg

#: The INSERT's column list and its VALUES list, from the live source.
_INSERT_RE = re.compile(
    r"INSERT INTO \{_TABLE\}\"?\s*(?P<cols>.*?)VALUES\((?P<vals>[^)]*)\)",
    re.DOTALL,
)


def _insert_clause() -> str:
    """The INSERT statement as it appears in `enqueue`'s own source."""
    return inspect.getsource(pg.enqueue)


def test_the_insert_statement_is_findable_at_all():
    """POSITIVE CONTROL. Every assertion below reads the statement out of the
    source; if the regex stops matching (someone reformats the SQL), the checks
    would pass vacuously on an empty string and this file would guard nothing."""
    # Arrange
    source = _insert_clause()
    # Act
    match = _INSERT_RE.search(source)
    # Assert
    assert match is not None


def test_the_insert_names_record_json():
    """THE FIX. `record_json` must be among the columns written."""
    # Arrange
    match = _INSERT_RE.search(_insert_clause())
    # Act
    columns = match.group("cols")
    # Assert
    assert "record_json" in columns


def test_every_column_has_a_placeholder():
    """Guards a DIFFERENT bug: a column list and a VALUES list of unequal
    length, which raises at execution.

    WHAT THIS TEST DOES NOT CATCH, stated because I first wrote that it did.
    Measured against the unfixed source: it PASSED. The outage was a column
    omitted from BOTH lists — nine columns, eight placeholders plus a literal,
    perfectly self-consistent and perfectly wrong. Internal consistency cannot
    detect an omission, because what is missing is missing from both sides of
    the comparison.

    Completeness is guarded by `test_the_insert_names_record_json`, which names
    the column. This one stays because the mismatch bug is real and cheap to
    exclude — but a test whose docstring claims more than it checks is the
    thing this whole file exists to prevent.

    `seen` is written as a literal 0 rather than a placeholder, so it is
    expected to have no `%s` of its own."""
    # Arrange
    match = _INSERT_RE.search(_insert_clause())
    columns = [c.strip() for c in match.group("cols").strip(' "()').split(",")]
    # Act
    literal_columns = 1  # `seen` is the literal 0
    counts = (len(columns) - literal_columns, match.group("vals").count("%s"))
    # Assert
    assert counts[0] == counts[1]


def test_the_payload_is_built_from_the_record_the_function_already_returns():
    """The record dict is built, returned to the caller, and must be the SAME
    object handed to the database — not a second construction that could drift
    from it. `card_payload_json(record)` is the call that guarantees that."""
    # Arrange
    source = _insert_clause()
    # Act
    serialises_the_record = "card_payload_json(record)" in source
    # Assert
    assert serialises_the_record is True


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
