#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards dev list-undelivered`` — mounting, exit codes, JSON shape.

NO MOCKS: the rail is a REAL temporary sqlite database carrying the REAL
``channel_events`` schema, and the verb is driven through Click's runner.

Assertions read ``result.stdout`` rather than ``result.output``: CliRunner
folds stderr into ``output`` by default, so asserting on it passes when the
text arrives on the wrong stream.

The EXIT CODES are the contract a cron line depends on, and the one that
matters most is 2 — CANNOT TELL. A check whose "I could not read the rail"
looked like its "nothing is wrong" would be the very bug this verb exists to
retire, so the two must never collapse onto the same status.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from click.testing import CliRunner

from scitex_cards._cli._main import main

RAIL_SCHEMA = """
CREATE TABLE channel_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    target        TEXT NOT NULL,
    source        TEXT,
    kind          TEXT NOT NULL DEFAULT 'message',
    content       TEXT,
    meta_json     TEXT NOT NULL,
    ts            REAL NOT NULL,
    delivered_at  REAL
)
"""

ME = "scitex-cards"
TS = 1786725321.4503303


@pytest.fixture
def runner():
    """Fresh CliRunner per test."""
    return CliRunner()


@pytest.fixture
def rail(tmp_path):
    """A real, empty rail file with the real schema.

    Yields (never returns) because it acquires a filesystem resource, and the
    teardown after the yield is what releases it (STX-TQ005).
    """
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(RAIL_SCHEMA)
        conn.commit()
    finally:
        conn.close()
    yield path
    path.unlink(missing_ok=True)


def _insert(path, *, target: str, source: str, delivered_at: "float | None") -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO channel_events "
            "(target, source, kind, content, meta_json, ts, delivered_at) "
            "VALUES (?, ?, 'message', 'body', '{}', ?, ?)",
            (target, source, TS, delivered_at),
        )
        conn.commit()
    finally:
        conn.close()


def _invoke(runner, rail, *extra):
    return runner.invoke(
        main, ["dev", "list-undelivered", "--agent", ME, "--rail", str(rail), *extra]
    )


# -- the verb is mounted where the doctrine says ---------------------------
def test_the_verb_is_mounted_under_the_dev_group(runner):
    # Arrange — §13: self-maintenance nests under `dev`, never at top level
    args = ["dev", "--help"]
    # Act
    result = runner.invoke(main, args)
    # Assert
    assert "list-undelivered" in result.stdout


def test_the_verb_is_not_mounted_at_the_top_level(runner):
    # Arrange
    args = ["--help"]
    # Act
    result = runner.invoke(main, args)
    # Assert
    assert "list-undelivered" not in result.stdout


def test_the_help_documents_the_positive_control(runner):
    # Arrange
    args = ["dev", "list-undelivered", "--help"]
    # Act
    result = runner.invoke(main, args)
    # Assert
    assert "POSITIVE CONTROL" in result.stdout


# -- exit codes: the cron contract -----------------------------------------
def test_an_empty_rail_exits_cannot_tell_rather_than_success(runner, rail):
    # Arrange — the per-agent-shard trap: table present, zero rows
    expected = 2
    # Act
    result = _invoke(runner, rail)
    # Assert
    assert result.exit_code == expected


def test_a_missing_rail_exits_cannot_tell(runner, tmp_path):
    # Arrange
    absent = tmp_path / "nope.db"
    # Act
    result = _invoke(runner, absent)
    # Assert
    assert result.exit_code == 2


def test_a_clean_rail_exits_success(runner, rail):
    # Arrange
    _insert(rail, target=ME, source="scitex-hub", delivered_at=TS)
    # Act
    result = _invoke(runner, rail)
    # Assert
    assert result.exit_code == 0


def test_an_undelivered_message_exits_non_zero(runner, rail):
    # Arrange
    _insert(rail, target="scitex-hub", source=ME, delivered_at=None)
    # Act
    result = _invoke(runner, rail)
    # Assert
    assert result.exit_code == 1


def test_cannot_tell_and_found_do_not_share_an_exit_code(runner, rail, tmp_path):
    # Arrange — the distinction this verb exists to preserve
    _insert(rail, target="scitex-hub", source=ME, delivered_at=None)
    absent = tmp_path / "nope.db"
    # Act
    found = _invoke(runner, rail).exit_code
    # Assert
    assert found != _invoke(runner, absent).exit_code


# -- the human rendering names the row -------------------------------------
def test_an_undelivered_message_is_named_not_merely_counted(runner, rail):
    # Arrange
    _insert(rail, target="scitex-hub", source=ME, delivered_at=None)
    # Act
    result = _invoke(runner, rail)
    # Assert
    assert "scitex-hub" in result.stdout


def test_the_rendering_shows_a_readable_timestamp_not_a_float(runner, rail):
    # Arrange
    _insert(rail, target="scitex-hub", source=ME, delivered_at=None)
    # Act
    result = _invoke(runner, rail)
    # Assert
    assert "2026-08-14 16:35:21Z" in result.stdout


def test_a_failed_control_says_it_is_not_an_all_clear(runner, rail):
    # Arrange
    expected = "NOT an all-clear"
    # Act
    result = _invoke(runner, rail)
    # Assert
    assert expected in result.stdout


# -- the JSON shape is fixed on every path ---------------------------------
def _keys(result) -> set:
    return set(json.loads(result.stdout))


def test_the_json_shape_is_identical_on_the_success_path(runner, rail):
    # Arrange
    _insert(rail, target=ME, source="scitex-hub", delivered_at=TS)
    expected = {
        "agent",
        "rail",
        "control_passed",
        "total_rows",
        "inbound_undelivered",
        "outbound_undelivered",
        "inbound",
        "outbound",
        "detail",
    }
    # Act
    result = _invoke(runner, rail, "--json")
    # Assert
    assert _keys(result) == expected


def test_the_json_shape_is_identical_on_the_cannot_tell_path(runner, rail):
    # Arrange — a caller must never have to guess which key exists this run
    _insert(rail, target=ME, source="scitex-hub", delivered_at=TS)
    passed = _keys(_invoke(runner, rail, "--json"))
    # Act
    failed = _keys(_invoke(runner, rail.parent / "nope.db", "--json"))
    # Assert
    assert failed == passed


def test_the_json_reports_cannot_tell_rather_than_a_zero_count(runner, rail):
    # Arrange
    expected = "cannot_tell"
    # Act
    result = _invoke(runner, rail, "--json")
    # Assert
    assert json.loads(result.stdout)["inbound_undelivered"] == expected


def test_the_json_names_the_undelivered_row(runner, rail):
    # Arrange
    _insert(rail, target="scitex-hub", source=ME, delivered_at=None)
    # Act
    result = _invoke(runner, rail, "--json")
    # Assert
    assert json.loads(result.stdout)["outbound"][0]["peer"] == "scitex-hub"


# EOF
