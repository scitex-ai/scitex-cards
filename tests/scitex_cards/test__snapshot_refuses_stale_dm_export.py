#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A snapshot must not record DMs it did not actually capture.

MEASURED 2026-07-28 on the live canonical store: the ``messages`` table held
2042 rows whose newest ``ts`` was ``2026-07-19T00:49:05Z`` — NINE DAYS stale.
Zero rows for 07-27 or 07-28; four DMs sent that morning were absent. Every DM
since existed only in the ``threads.json`` sidecar.

The root cause is worse than a lapsed refresh: ``messages`` has NO LIVE WRITER.
``_threads.append_message`` (the only DM write path) never touches the database, and
``_insert_messages`` — the table's only writer — has a single caller guarded by
an explicit ``threads=`` argument that nothing in ``src/`` passes. The table is
a fossil left behind when the YAML tier was deleted.

2042 rows is what makes that dangerous: the table LOOKS populated, so a reader
gets a plausible, complete-shaped, nine-day-old answer and nothing errors. One
agent queried it for a DM they had just received, found nothing, and nearly
concluded "no recent DMs exist" from an instrument that could not have shown
one.

``_assert_export_reflects_live_db`` already refuses a stale snapshot — but it
compares CARDS only, while the same report prints a ``messages`` count that
reads as equally verified. A check that silently narrows its own scope while
its output still looks complete is worse than no check, because the number
reads as coverage.

So this guard compares the exported DM count against the LIVE SIDECAR, the
source of truth for DMs today. Had it existed it would have fired on
2026-07-19 instead of letting every snapshot since bank a stale copy of the
chat as if it were a backup.
"""

from __future__ import annotations

import json

import pytest

from scitex_cards._cli._db import _assert_export_reflects_live_dms


@pytest.fixture
def db(tmp_path):
    """An isolated store dir standing in for a resolved DB path.

    EXPLICIT and tmp-scoped: a test that resolves the default store would read
    the live fleet board, which the suite's isolation guard rightly fails.
    """
    path = tmp_path / "cards.db"
    path.touch()
    return path


def _write_sidecar(db_path, threads):
    (db_path.parent / "threads.json").write_text(
        json.dumps({"threads": threads}, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def drifted(db):
    """A store whose sidecar holds 3 DMs while the export reported only 1.

    The exact production shape: the sidecar moved on and the mirror did not.
    """
    _write_sidecar(db, {"dm:a::b": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]})
    return db


@pytest.fixture
def refusal_message(drifted):
    """The text the guard refuses with, for the message-content assertions."""
    with pytest.raises(Exception) as excinfo:
        _assert_export_reflects_live_dms(str(drifted), {"messages": 1})
    return str(excinfo.value)


def test_it_refuses_when_the_sidecar_holds_more_messages_than_the_export(drifted):
    # Arrange
    report = {"messages": 1}

    # Act
    def snapshot_guard():
        _assert_export_reflects_live_dms(str(drifted), report)

    # Assert - the snapshot must not be committed as if current.
    with pytest.raises(Exception):
        snapshot_guard()


def test_it_passes_when_the_export_matches_the_live_sidecar(db):
    # Arrange
    _write_sidecar(db, {"dm:a::b": [{"id": "m1"}, {"id": "m2"}]})

    # Act
    result = _assert_export_reflects_live_dms(str(db), {"messages": 2})

    # Assert - a healthy export returns quietly.
    assert result is None


def test_a_board_with_no_dms_is_not_a_failure(db):
    # Arrange - no sidecar written at all; a fresh board legitimately has none.
    report = {"messages": 0}

    # Act
    result = _assert_export_reflects_live_dms(str(db), report)

    # Assert
    assert result is None


def test_the_refusal_names_the_live_count(refusal_message):
    """An error that only says 'stale' makes the reader go find the numbers."""
    # Arrange - the fixture drove the guard to refuse on a drifted store.
    live_count = "3"

    # Act
    message = refusal_message

    # Assert
    assert live_count in message


def test_the_refusal_names_the_exported_count(refusal_message):
    """Both sides of the disagreement, so the direction of drift is visible."""
    # Arrange
    exported_count = "1"

    # Act
    message = refusal_message

    # Assert
    assert exported_count in message


def test_a_malformed_sidecar_does_not_crash_the_snapshot(db):
    """A corrupt sidecar must refuse loudly, not raise an opaque parse error."""
    # Arrange
    (db.parent / "threads.json").write_text("{not json", encoding="utf-8")

    # Act
    def snapshot_guard():
        _assert_export_reflects_live_dms(str(db), {"messages": 0})

    # Assert
    with pytest.raises(Exception):
        snapshot_guard()


# EOF
