#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The durable-rail undelivered check — positive control, signals, shape.

NO MOCKS. Every test builds a REAL temporary sqlite database using the REAL
``channel_events`` schema, copied verbatim from the live rail. A mocked rail
would have happily returned whatever the test asked for, and the bug this
module exists to prevent is precisely a query that succeeds against the wrong
database and returns a meaningless zero — which only a real database can
reproduce.

The most important test here is the EMPTY-rail one. A per-agent
``runtime/<agent>/state.db`` shard carries the ``channel_events`` table with
zero rows in it; querying one succeeds and reads exactly like an all-clear.
That is the failure this card was filed about, and it must come back as
CANNOT_TELL rather than a pass.
"""

from __future__ import annotations

import sqlite3

import pytest

from scitex_cards._channel_rail import (
    Control,
    Signal,
    UndeliveredMessage,
    UndeliveredReport,
    format_epoch,
    read_undelivered,
)

#: Verbatim from the live rail (``sqlite_master.sql`` on
#: ~/.scitex/agent-container/runtime/state.db). Kept exact so a schema drift
#: upstream shows up here as a failure rather than as a passing fiction.
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
#: 2026-08-14 16:35:21Z — the float epoch of the real undelivered row the card
#: recorded, so the rendering assertions are anchored to a known value.
TS = 1786725321.4503303


def _insert(
    path,
    *,
    target: str,
    source: str,
    content: str = "hello",
    ts: float = TS,
    delivered_at: "float | None" = None,
) -> None:
    """Append one row to a real rail. Opens and closes its own connection."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO channel_events "
            "(target, source, kind, content, meta_json, ts, delivered_at) "
            "VALUES (?, ?, 'message', ?, '{}', ?, ?)",
            (target, source, content, ts, delivered_at),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def rail(tmp_path):
    """A real, EMPTY rail file carrying the real schema.

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


@pytest.fixture
def tableless_rail(tmp_path):
    """A real sqlite database with NO ``channel_events`` table."""
    path = tmp_path / "wrong.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    yield path
    path.unlink(missing_ok=True)


# -- the positive control: the failure this card was filed about -----------
def test_an_empty_rail_cannot_tell_rather_than_reporting_an_all_clear(rail):
    # Arrange — the per-agent shard shape: table present, zero rows
    expected = Control.EMPTY
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.control_passed is expected


def test_an_empty_rail_leaves_the_inbound_signal_unable_to_tell(rail):
    # Arrange — a filtered zero here would prove nothing
    expected = Signal.CANNOT_TELL
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.inbound_undelivered is expected


def test_an_empty_rail_leaves_the_outbound_signal_unable_to_tell(rail):
    # Arrange
    expected = Signal.CANNOT_TELL
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.outbound_undelivered is expected


def test_an_empty_rail_is_never_mistaken_for_a_clear_signal(rail):
    # Arrange — CANNOT_TELL and CLEAR must be distinguishable values
    forbidden = Signal.CLEAR
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert forbidden not in (report.inbound_undelivered, report.outbound_undelivered)


def test_an_empty_rail_says_why_it_cannot_tell(rail):
    # Arrange
    report = read_undelivered(ME, rail)
    # Act
    detail = report.detail
    # Assert
    assert "0 rows" in detail


def test_a_missing_rail_cannot_tell(tmp_path):
    # Arrange — the file simply is not there
    absent = tmp_path / "nope.db"
    # Act
    report = read_undelivered(ME, absent)
    # Assert
    assert report.control_passed is Control.UNREADABLE


def test_a_missing_rail_is_flagged_as_cannot_tell_not_as_actionable(tmp_path):
    # Arrange
    absent = tmp_path / "nope.db"
    # Act
    report = read_undelivered(ME, absent)
    # Assert
    assert report.cannot_tell is True


def test_a_rail_without_the_events_table_cannot_tell(tableless_rail):
    # Arrange — a real database, wrong contents
    expected = Control.UNREADABLE
    # Act
    report = read_undelivered(ME, tableless_rail)
    # Assert
    assert report.control_passed is expected


def test_a_populated_rail_passes_the_control(rail):
    # Arrange
    _insert(rail, target="someone", source="other", delivered_at=TS)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.control_passed is Control.PASSED


def test_the_control_reports_the_row_count_as_its_evidence(rail):
    # Arrange
    for _ in range(3):
        _insert(rail, target="someone", source="other", delivered_at=TS)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.total_rows == 3


# -- undelivered inbound ---------------------------------------------------
def test_an_undelivered_inbound_row_is_found(rail):
    # Arrange
    _insert(rail, target=ME, source="scitex-hub", delivered_at=None)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.inbound_undelivered is Signal.FOUND


def test_an_undelivered_inbound_row_names_its_sender(rail):
    # Arrange
    _insert(rail, target=ME, source="scitex-hub", delivered_at=None)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.inbound[0].peer == "scitex-hub"


def test_an_undelivered_inbound_row_carries_a_content_preview(rail):
    # Arrange
    _insert(rail, target=ME, source="scitex-hub", content="resend me", delivered_at=None)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.inbound[0].preview == "resend me"


def test_a_long_body_is_truncated_to_the_preview_length(rail):
    # Arrange — enough to recognise and resend, not the whole message
    _insert(rail, target=ME, source="scitex-hub", content="x" * 500, delivered_at=None)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert len(report.inbound[0].preview) == 80


def test_a_null_body_previews_as_empty_rather_than_none(rail):
    # Arrange — content is nullable on the real schema
    _insert(rail, target=ME, source="scitex-hub", content=None, delivered_at=None)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.inbound[0].preview == ""


def test_inbound_traffic_does_not_leak_into_the_outbound_signal(rail):
    # Arrange
    _insert(rail, target=ME, source="scitex-hub", delivered_at=None)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.outbound_undelivered is Signal.CLEAR


# -- undelivered outbound --------------------------------------------------
def test_an_undelivered_outbound_row_is_found(rail):
    # Arrange
    _insert(rail, target="scitex-hub", source=ME, delivered_at=None)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.outbound_undelivered is Signal.FOUND


def test_an_undelivered_outbound_row_names_its_recipient(rail):
    # Arrange
    _insert(rail, target="scitex-hub", source=ME, delivered_at=None)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.outbound[0].peer == "scitex-hub"


def test_outbound_traffic_does_not_leak_into_the_inbound_signal(rail):
    # Arrange
    _insert(rail, target="scitex-hub", source=ME, delivered_at=None)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.inbound_undelivered is Signal.CLEAR


# -- neither: a real, control-backed zero -----------------------------------
def test_a_busy_but_fully_delivered_rail_reports_a_real_inbound_zero(rail):
    # Arrange
    _insert(rail, target=ME, source="scitex-hub", delivered_at=TS)
    _insert(rail, target="scitex-hub", source=ME, delivered_at=TS)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.inbound_undelivered is Signal.CLEAR


def test_a_busy_but_fully_delivered_rail_reports_a_real_outbound_zero(rail):
    # Arrange
    _insert(rail, target=ME, source="scitex-hub", delivered_at=TS)
    _insert(rail, target="scitex-hub", source=ME, delivered_at=TS)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.outbound_undelivered is Signal.CLEAR


def test_a_fully_delivered_rail_needs_no_action(rail):
    # Arrange
    _insert(rail, target=ME, source="scitex-hub", delivered_at=TS)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.actionable is False


# -- delivered rows must never be reported ---------------------------------
def test_a_delivered_inbound_row_is_not_reported(rail):
    # Arrange — a delivery stamp means it arrived
    _insert(rail, target=ME, source="scitex-hub", delivered_at=TS + 1)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.inbound == ()


def test_a_delivered_outbound_row_is_not_reported(rail):
    # Arrange
    _insert(rail, target="scitex-hub", source=ME, delivered_at=TS + 1)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.outbound == ()


def test_a_delivered_stamp_of_zero_still_counts_as_delivered(rail):
    # Arrange — 0.0 is a stamp; only NULL means undelivered
    _insert(rail, target=ME, source="scitex-hub", delivered_at=0.0)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.inbound == ()


# -- scoping ----------------------------------------------------------------
def test_another_agents_undelivered_row_is_not_reported_as_mine(rail):
    # Arrange
    _insert(rail, target="scitex-db", source="scitex-hub", delivered_at=None)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.inbound == ()


# -- NO WATERMARK: a low-id row must still be reported ----------------------
def test_a_low_id_undelivered_row_is_still_reported(rail):
    # Arrange — an `id > N` watermark was added here once and became a blind
    # spot when the row it excluded was later delivered. Guard against its
    # return: the very first row on the rail must still reach the report.
    _insert(rail, target=ME, source="scitex-hub", delivered_at=None)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert [row.id for row in report.inbound] == [1]


# -- timestamp rendering from a FLOAT EPOCH --------------------------------
def test_a_float_epoch_renders_as_a_readable_utc_timestamp():
    # Arrange — ts is a float epoch, NOT an ISO string
    epoch = TS
    # Act
    rendered = format_epoch(epoch)
    # Assert
    assert rendered == "2026-08-14 16:35:21Z"


def test_a_rail_row_renders_its_own_timestamp(rail):
    # Arrange
    _insert(rail, target=ME, source="scitex-hub", ts=TS, delivered_at=None)
    # Act
    report = read_undelivered(ME, rail)
    # Assert
    assert report.inbound[0].when == "2026-08-14 16:35:21Z"


def test_an_unrenderable_timestamp_says_so_rather_than_inventing_a_date():
    # Arrange — no silent fallback to a plausible-looking wrong time
    nonsense = float("nan")
    # Act
    rendered = format_epoch(nonsense)
    # Assert
    assert "unrenderable" in rendered


# -- the declared shape is enforced, not merely documented -----------------
def _report(**overrides) -> UndeliveredReport:
    base = dict(
        agent=ME,
        rail="/tmp/state.db",
        control_passed=Control.PASSED,
        total_rows=1,
        inbound_undelivered=Signal.CLEAR,
        outbound_undelivered=Signal.CLEAR,
    )
    base.update(overrides)
    return UndeliveredReport(**base)


def _shape_error(**overrides) -> str:
    """The validator's complaint, as a value, so a test keeps one assertion."""
    try:
        _report(**overrides)
    except ValueError as exc:
        return str(exc)
    return ""


def test_a_failed_control_may_not_claim_a_clear_inbound_signal():
    # Arrange — the invariant the whole module exists for
    kwargs = dict(
        control_passed=Control.EMPTY,
        total_rows=0,
        inbound_undelivered=Signal.CLEAR,
        outbound_undelivered=Signal.CANNOT_TELL,
        detail="empty",
    )
    # Act
    message = _shape_error(**kwargs)
    # Assert
    assert "never a pass" in message


def test_a_failed_control_may_not_carry_rows():
    # Arrange
    row = UndeliveredMessage(id=1, peer="scitex-hub", ts=TS, preview="x")
    kwargs = dict(
        control_passed=Control.UNREADABLE,
        total_rows=0,
        inbound_undelivered=Signal.CANNOT_TELL,
        outbound_undelivered=Signal.CANNOT_TELL,
        inbound=(row,),
        detail="unreadable",
    )
    # Act
    message = _shape_error(**kwargs)
    # Assert
    assert "failed positive control" in message


def test_a_failed_control_must_state_a_reason():
    # Arrange
    kwargs = dict(
        control_passed=Control.EMPTY,
        total_rows=0,
        inbound_undelivered=Signal.CANNOT_TELL,
        outbound_undelivered=Signal.CANNOT_TELL,
        detail="",
    )
    # Act
    message = _shape_error(**kwargs)
    # Assert
    assert "must say why" in message


def test_a_passing_control_may_not_leave_a_signal_unable_to_tell():
    # Arrange
    kwargs = dict(inbound_undelivered=Signal.CANNOT_TELL)
    # Act
    message = _shape_error(**kwargs)
    # Assert
    assert "neither signal may be CANNOT_TELL" in message


def test_the_control_cannot_pass_on_an_empty_rail():
    # Arrange — PASSED must be backed by rows
    kwargs = dict(total_rows=0)
    # Act
    message = _shape_error(**kwargs)
    # Assert
    assert "cannot pass on an empty rail" in message


def test_a_found_signal_must_name_its_rows():
    # Arrange
    kwargs = dict(inbound_undelivered=Signal.FOUND, inbound=())
    # Act
    message = _shape_error(**kwargs)
    # Assert
    assert "no rows named" in message


def test_a_clear_signal_may_not_carry_rows():
    # Arrange
    row = UndeliveredMessage(id=1, peer="scitex-hub", ts=TS, preview="x")
    kwargs = dict(inbound_undelivered=Signal.CLEAR, inbound=(row,))
    # Act
    message = _shape_error(**kwargs)
    # Assert
    assert "CLEAR but 1 rows" in message


# EOF
