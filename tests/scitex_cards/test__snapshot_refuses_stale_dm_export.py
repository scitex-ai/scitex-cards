#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A snapshot must not record DMs it did not actually capture.

MEASURED 2026-07-28 on the live canonical store: the ``messages`` table held
2042 rows whose newest ``ts`` was ``2026-07-19T00:49:05Z`` — NINE DAYS stale.
Zero rows for 07-27 or 07-28; four DMs sent that morning were absent. Every DM
since Jul 19 existed only in the ``threads.json`` sidecar, because ``messages``
is a DERIVED mirror of it (see ``_db_mirror``) and had stopped being refreshed.

2042 rows is what makes this dangerous: the table LOOKS populated, so a reader
gets a plausible, complete-shaped, nine-day-old answer and nothing errors. One
agent queried it for a DM they had just received, found nothing, and nearly
concluded "no recent DMs exist" from an instrument that could not have shown
one.

``_assert_export_reflects_live_db`` already refuses a stale snapshot — but it
compares CARDS only, while the same report prints a ``messages`` count that
reads as equally verified. A check that silently narrows its own scope is the
shape this repo has been bitten by before: the report says "snapshot: N
messages" and means "N rows copied from a mirror I did not check".

So this guard compares the exported DM count against the LIVE SIDECAR, which is
the source of truth for DMs today. Had it existed, it would have fired nine days
ago instead of letting every snapshot since bank a stale copy of the chat.
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


def test_it_refuses_when_the_sidecar_holds_more_messages_than_the_export(db):
    # Arrange - the exact production shape: sidecar moved on, mirror did not.
    _write_sidecar(db, {"dm:a::b": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]})

    # Act / Assert - the snapshot must not be committed as if current.
    with pytest.raises(Exception):
        _assert_export_reflects_live_dms(str(db), {"messages": 1})


def test_it_passes_when_the_export_matches_the_live_sidecar(db):
    # Arrange
    _write_sidecar(db, {"dm:a::b": [{"id": "m1"}, {"id": "m2"}]})

    # Act
    result = _assert_export_reflects_live_dms(str(db), {"messages": 2})

    # Assert - a healthy export returns quietly.
    assert result is None


def test_a_board_with_no_dms_is_not_a_failure(db):
    # Arrange - no sidecar written at all; a fresh board legitimately has none.

    # Act
    result = _assert_export_reflects_live_dms(str(db), {"messages": 0})

    # Assert
    assert result is None


def test_the_refusal_names_the_live_count(db):
    """An error that only says 'stale' makes the reader go find the numbers."""
    # Arrange
    _write_sidecar(db, {"dm:a::b": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]})

    # Act
    with pytest.raises(Exception) as excinfo:
        _assert_export_reflects_live_dms(str(db), {"messages": 1})

    # Assert
    assert "3" in str(excinfo.value)


def test_the_refusal_names_the_exported_count(db):
    """Both sides of the disagreement, so the direction of drift is visible."""
    # Arrange
    _write_sidecar(db, {"dm:a::b": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]})

    # Act
    with pytest.raises(Exception) as excinfo:
        _assert_export_reflects_live_dms(str(db), {"messages": 1})

    # Assert
    assert "1" in str(excinfo.value)


def test_a_malformed_sidecar_does_not_crash_the_snapshot(db):
    """A corrupt sidecar must refuse loudly, not raise an opaque parse error."""
    # Arrange
    (db.parent / "threads.json").write_text("{not json", encoding="utf-8")

    # Act / Assert
    with pytest.raises(Exception):
        _assert_export_reflects_live_dms(str(db), {"messages": 0})


# EOF
