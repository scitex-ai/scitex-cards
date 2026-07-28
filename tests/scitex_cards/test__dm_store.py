#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The DM write path, and the invariants the ENGINE enforces on it.

DESIGN: ``docs/design/dm-into-cards-db.md`` §3 / §4.

WHAT THIS FILE IS FOR that the design-intent files are not: those encode the
SHAPE of the schema, this one exercises the path a live DM actually takes —
``append_message`` writing the database first, the sidecar mirror staying
complete as the rollback state, and each failure mode landing on the right
side of "is the message lost".

THE DUAL-WRITE POLARITY IS THE THING MOST WORTH GUARDING. The database write
is authoritative and RAISES; the sidecar write is best-effort and is only
logged. Get that backwards and a database failure silently degrades to
sidecar-only — which is exactly the state this whole migration exists to
leave, reached by accident instead of by design. Two tests here pin each half.

Every database is an EXPLICIT ``tmp_path`` file, and every call names its
store. Nothing here resolves the ambient store or touches the live fleet.
"""

from __future__ import annotations

import sqlite3
from functools import partial
from pathlib import Path

import pytest

from scitex_cards import _dm_store
from scitex_cards._db import SCHEMA_VERSION, open_db
from scitex_cards._dm_read import current_members
from scitex_cards._threads import append_message, get_thread, mark_read


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    """The task-store container. Its directory is where ``cards.db`` lands."""
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return path


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """The database ``store`` resolves to — named, never discovered."""
    return tmp_path / "cards.db"


@pytest.fixture()
def conn(db_path: Path):
    """A schema-complete connection to the throwaway database."""
    connection = open_db(db_path)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def sent(store: Path) -> dict:
    """One DM, sent the way every caller in the package sends one."""
    return append_message("operator", "agent-x", "hello there", store=store)


# --------------------------------------------------------------------------- #
# The dual write (design part 2, M3)                                           #
# --------------------------------------------------------------------------- #
def test_the_database_keeps_the_message_id_the_caller_was_given(sent, db_path):
    """The id in the DB is the id the caller holds — no second identity.

    Callers, MCP responses and board URLs all carry the returned id. If the
    database minted its own, every existing reference would point at nothing
    and the migration would have rewritten ids, which append-only forbids.
    """
    # Arrange
    expected = sent["id"]

    # Act
    stored = _dm_store.messages_in(sent["thread"], db=db_path)

    # Assert
    assert [m["id"] for m in stored] == [expected]


def test_the_sidecar_still_receives_the_message(sent, store):
    """The rollback state stays COMPLETE, which is what makes it a rollback.

    Undoing this migration has to be "redeploy the previous version" rather
    than "restore a backup". That is only true while the sidecar remains a
    faithful copy, so the mirror is not optional decoration.
    """
    # Arrange
    expected = sent["id"]

    # Act
    records = get_thread("operator", "agent-x", store=store)

    # Assert
    assert [r["id"] for r in records] == [expected]


def test_a_database_failure_is_not_swallowed(store, monkeypatch):
    """The DB write is AUTHORITATIVE: if it fails, the send fails.

    Degrading to a sidecar-only write would look like success while putting
    the message back exactly where this migration is taking it from — and
    nobody would find out until the next time the sidecar was the only copy
    of something that mattered.
    """

    # Arrange
    def _boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("scitex_cards._dm_write.append_pair", _boom)

    # Act
    refusal = pytest.raises(sqlite3.OperationalError)

    # Assert
    with refusal:
        append_message("operator", "agent-x", "hello", store=store)


def test_a_sidecar_failure_does_not_lose_the_message(store, db_path, monkeypatch):
    """The mirror is best-effort: its failure must not fail the send.

    By the time the mirror runs the message is already durable in the store of
    record, so raising here would report as lost a message that was not lost —
    and would hand the caller a reason to retry, duplicating it.
    """

    # Arrange
    def _boom(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr("scitex_cards._threads_io._save_threads_unlocked", _boom)
    append_message("operator", "agent-x", "hello", store=store)

    # Act
    count = _dm_store.message_count(db=db_path)

    # Assert
    assert count == 1


def test_reading_a_dm_records_a_receipt_in_the_database(store, db_path, sent):
    """Read state travels with the messages, or every DM pops unread at cutover.

    ``mark_read`` flips the sidecar's boolean AND inserts a per-reader receipt.
    Without the second half, the day reads move to the database is the day the
    whole fleet's chat history reappears as unread.
    """
    # Arrange
    mark_read(sent["thread"], "agent-x", store=store)

    # Act
    unread = _dm_store.unread_for("agent-x", db=db_path)

    # Assert
    assert unread == []


# --------------------------------------------------------------------------- #
# Append-only, enforced by the engine (design section 4.2)                     #
# --------------------------------------------------------------------------- #
def _seed(connection: sqlite3.Connection) -> None:
    """One thread, one message, one receipt — raw SQL on purpose.

    These tests are about what the ENGINE permits, so routing through a Python
    helper would test the helper's discipline instead of the database's.
    """
    connection.execute(
        "INSERT INTO dm_threads(id, kind, created_at, origin_host, record_json)"
        " VALUES('dm:agent-x::operator', 'pair', '2026-07-28T00:00:00Z',"
        " 'host-a', '{}')"
    )
    connection.execute(
        "INSERT INTO dm_thread_member_events(id, thread_id, member, action, ts,"
        " origin_host, record_json) VALUES('dme_1', 'dm:agent-x::operator',"
        " 'operator', 'join', '2026-07-28T00:00:00Z', 'host-a', '{}')"
    )
    connection.execute(
        "INSERT INTO dm_messages(id, thread_id, sender, body, ts, seq,"
        " origin_host, record_json) VALUES('m_seed', 'dm:agent-x::operator',"
        " 'operator', 'hello', '2026-07-28T00:00:01Z', 1, 'host-a', '{}')"
    )
    connection.execute(
        "INSERT INTO dm_receipts(message_id, reader, read_at, origin_host,"
        " source) VALUES('m_seed', 'agent-x', '2026-07-28T00:00:02Z',"
        " 'host-a', 'live')"
    )


def test_dm_threads_refuses_physical_delete(conn):
    """A thread row is never removed — its messages would be orphaned."""
    # Arrange
    _seed(conn)
    delete = partial(conn.execute, "DELETE FROM dm_threads")

    # Act
    refusal = pytest.raises(sqlite3.DatabaseError, match="append-only")

    # Assert
    with refusal:
        delete()


def test_dm_thread_member_events_refuses_physical_delete(conn):
    """Leaving a thread is a LEAVE ROW, never a removed one.

    Delete the join and "who could see this message" becomes unanswerable
    after the fact — the audit question the event log exists to answer.
    """
    # Arrange
    _seed(conn)
    delete = partial(conn.execute, "DELETE FROM dm_thread_member_events")

    # Act
    refusal = pytest.raises(sqlite3.DatabaseError, match="append-only")

    # Assert
    with refusal:
        delete()


def test_dm_receipts_refuses_physical_delete(conn):
    """A receipt is monotone: "unread again" is deliberately not expressible.

    That is what lets the receipts table merge across hosts by union with no
    arbitration — a withdrawable receipt would need last-write-wins.
    """
    # Arrange
    _seed(conn)
    delete = partial(conn.execute, "DELETE FROM dm_receipts")

    # Act
    refusal = pytest.raises(sqlite3.DatabaseError, match="append-only")

    # Assert
    with refusal:
        delete()


def test_dm_messages_sender_is_immutable(conn):
    """Re-attributing a message is a rewritten record, which append-only forbids."""
    # Arrange
    _seed(conn)
    edit = partial(conn.execute, "UPDATE dm_messages SET sender = 'someone-else'")

    # Act
    refusal = pytest.raises(sqlite3.DatabaseError, match="immutable")

    # Assert
    with refusal:
        edit()


def test_tombstoning_is_the_one_permitted_update(conn):
    """``deleted_at`` is writable while everything around it is frozen.

    That narrow exception is the whole deletion story: the row, its body and
    its receipts all survive, so a count can never fall.
    """
    # Arrange
    _seed(conn)

    # Act
    conn.execute("UPDATE dm_messages SET deleted_at = '2026-07-28T00:00:03Z'")

    # Assert
    assert conn.execute("SELECT COUNT(*) FROM dm_messages").fetchone()[0] == 1


def test_a_tombstoned_message_is_hidden_from_reads(store, db_path, sent):
    """Hidden, not gone — the same shape ``_task._is_tombstoned`` gives a card."""
    # Arrange
    _dm_store.tombstone(sent["id"], by="operator", db=db_path)

    # Act
    visible = _dm_store.messages_in(sent["thread"], db=db_path)

    # Assert
    assert visible == []


def test_a_tombstoned_message_still_counts(store, db_path, sent):
    """The no-shrink guard compares a count that INCLUDES tombstones.

    A count that excluded them would fall on every deletion and would
    therefore report the very bug it exists to detect.
    """
    # Arrange
    _dm_store.tombstone(sent["id"], by="operator", db=db_path)

    # Act
    count = _dm_store.message_count(db=db_path)

    # Assert
    assert count == 1


# --------------------------------------------------------------------------- #
# Membership (design section 3.1 / 3.3)                                        #
# --------------------------------------------------------------------------- #
def test_leaving_a_thread_stops_new_messages_reaching_the_leaver(db_path):
    """Membership is a FOLD, so a leave event changes what the reader sees.

    The record of having been a member survives; the visibility does not.
    """
    # Arrange
    thread = _dm_store.create_group_thread(
        "war room", ["operator", "agent-a", "agent-b"], db=db_path
    )
    _dm_store.remove_member(thread["id"], "agent-b", db=db_path)
    _dm_store.append(thread["id"], "operator", "after the leave", db=db_path)

    # Act
    unread = _dm_store.unread_for("agent-b", db=db_path)

    # Assert
    assert unread == []


def test_a_leaver_is_still_recorded_as_having_been_a_member(db_path):
    """The event log keeps the history a mutable member list would have erased."""
    # Arrange
    thread = _dm_store.create_group_thread(
        "war room", ["operator", "agent-b"], db=db_path
    )
    _dm_store.remove_member(thread["id"], "agent-b", db=db_path)
    connection = open_db(db_path)

    # Act
    try:
        rows = connection.execute(
            "SELECT COUNT(*) FROM dm_thread_member_events WHERE member = 'agent-b'"
        ).fetchone()[0]
    finally:
        connection.close()

    # Assert
    assert rows == 2


def test_a_same_second_leave_beats_the_join_it_followed(conn):
    """REGRESSION, and it was a disclosure bug, not a cosmetic one.

    Timestamps are second-resolution, so creating a thread and removing a
    member in the same second gives the join and the leave an IDENTICAL ``ts``.
    The fold's tie-break then fell through to ``id`` — a content hash, i.e. a
    coin flip — and a departed member kept receiving new messages in 17 of 60
    measured runs. It passed locally (the hash sorted the lucky way) and only
    failed in CI, which is the worst possible way to learn it.

    Written with both events stamped to the same second ON PURPOSE: the
    behavioural test above only reproduces this probabilistically, so it can
    pass while the rule is broken. This one cannot.
    """
    # Arrange — join and leave, same ts, ordered only by seq.
    conn.execute(
        "INSERT INTO dm_threads(id, kind, created_at, origin_host, record_json)"
        " VALUES('dmg:x', 'group', '2026-07-28T00:00:00Z', 'host-a', '{}')"
    )
    for index, (event, action) in enumerate((("dme_j", "join"), ("dme_l", "leave"))):
        conn.execute(
            "INSERT INTO dm_thread_member_events(id, thread_id, member, action,"
            " ts, seq, origin_host, record_json) VALUES(?, 'dmg:x', 'agent-b',"
            " ?, '2026-07-28T00:00:00Z', ?, 'host-a', '{}')",
            (event, action, index + 1),
        )

    # Act
    members = current_members(conn, "dmg:x")

    # Assert
    assert members == []


def test_a_sender_never_sees_their_own_message_as_unread(db_path):
    """Authorship is part of the unread definition, not a caller's job."""
    # Arrange
    thread = _dm_store.create_group_thread(
        "standup", ["operator", "agent-a"], db=db_path
    )
    _dm_store.append(thread["id"], "operator", "morning", db=db_path)

    # Act
    unread = _dm_store.unread_for("operator", db=db_path)

    # Assert
    assert unread == []


# --------------------------------------------------------------------------- #
# Schema stamping                                                              #
# --------------------------------------------------------------------------- #
def test_the_schema_version_advanced_to_five(db_path):
    """The DM tables are a schema change, and the stamp has to say so.

    Checked against the FILE's ``user_version``, not against the constant: a
    stamp that agrees with itself proves nothing.
    """
    # Arrange
    connection = open_db(db_path)

    # Act
    try:
        stamped = int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()

    # Assert
    assert stamped == SCHEMA_VERSION == 5


def test_a_v4_database_gains_the_dm_tables_on_open(tmp_path):
    """``CREATE TABLE IF NOT EXISTS`` never alters an EXISTING database.

    So a store created before v5 would keep the old shape forever while its
    stamp claimed otherwise. The additive migration runs on every open for
    exactly that reason; this builds a v4-shaped file to prove it fires.
    """
    # Arrange — a database with the v4 tables and none of the v5 ones.
    legacy = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(legacy))
    raw.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    raw.execute("PRAGMA user_version=4")
    raw.commit()
    raw.close()

    # Act
    connection = open_db(legacy)
    try:
        present = {
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        connection.close()

    # Assert
    assert {"dm_threads", "dm_messages", "dm_receipts"} <= present


# EOF
