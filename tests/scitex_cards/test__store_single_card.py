#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A one-card verb reads one card and writes one card — through the same guards.

THE MEASUREMENT THIS PINS (2026-09-02, live primary, 6,542 cards): one
``comment_task`` exported the whole board three times, 2.7 s, against a 3.1 ms
one-card query. Every test below is a real tmp store; nothing is mocked. The
instrument on the last two tests is the card's own: cProfile, asserting that no
frame named ``export_doc`` (or the canonical read that wraps it) ran at all.

THE GUARD TESTS ARE THE POSITIVE CONTROL. A single-card path that skipped the
canonical read's exists / ownership / retired checks would be faster AND wrong
in exactly the way the 2026-07-19 outage was wrong (one door lenient, one
strict). So three tests here hand the one-card read a store the canonical read
refuses, and require the same refusal.
"""

from __future__ import annotations

import cProfile
import sqlite3

import pytest

from scitex_cards import _store
from scitex_cards._db import ENV_DB, connect
from scitex_cards._db_export import export_doc
from scitex_cards._store_errors import RevisionConflictError, StoreNotProvisionedError
from scitex_cards._store_retirement import StoreRetired, retire_store
from scitex_cards._store_single_card import read_card_or_raise, write_card_or_raise
from scitex_cards._store_target import resolve_store_target
from scitex_cards._store_uuid import ENV_EXPECTED_STORE_UUID, stamp_store_uuid

IDENTITY_A = "3f2b8c1e-9d4a-4f77-b0c5-1a2e3d4f5a6b"
IDENTITY_B = "7c9e0d21-5b3f-4a08-9e6d-2f4a6b8c0d1e"

_DOC = {
    "tasks": [
        {
            "id": "alpha",
            "title": "alpha",
            "status": "deferred",
            "assignee": "agent:test-suite",
            "comments": [
                {"author": "agent:test-suite", "ts": "2026-09-01T00:00:00Z", "text": "first"},
            ],
        },
        {
            "id": "beta",
            "title": "beta",
            "status": "deferred",
            "assignee": "agent:test-suite",
        },
    ]
}


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #
@pytest.fixture
def store_db(tmp_path, env):
    """A real two-card tmp store, pinned as ``$SCITEX_CARDS_DB`` for the test."""
    from conftest import seed_db_from_doc

    db = tmp_path / "cards.db"
    seed_db_from_doc(_DOC, str(db))
    env.set(ENV_DB, str(db))
    return db


def _revisions(db_path) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {str(r[0]): int(r[1]) for r in conn.execute("SELECT id, revision FROM tasks")}
    finally:
        conn.close()


def _comment_texts_in_table(db_path, task_id: str) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT text FROM task_comments WHERE task_id = ? ORDER BY seq", (task_id,)
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _frames_named(profile: cProfile.Profile) -> set[str]:
    return {
        entry.code.co_name
        for entry in profile.getstats()
        if hasattr(entry.code, "co_name")
    }


# --------------------------------------------------------------------------- #
# The read                                                                    #
# --------------------------------------------------------------------------- #
def test_the_one_card_read_is_the_exporters_card(store_db):
    """Equivalence: the row read alone is byte-for-byte the row the export rebuilds."""
    # Arrange
    exported = next(c for c in export_doc(store_db)[0]["tasks"] if c["id"] == "alpha")
    # Act
    card, _revision = read_card_or_raise(resolve_store_target(None), "alpha")
    # Assert
    assert card == exported


def test_an_unknown_id_reads_as_absent_not_as_an_error(store_db):
    # Arrange
    target = resolve_store_target(None)
    # Act
    card, revision = read_card_or_raise(target, "no-such-card")
    # Assert
    assert (card, revision) == (None, None)


# --------------------------------------------------------------------------- #
# The write                                                                   #
# --------------------------------------------------------------------------- #
def test_a_comment_moves_exactly_one_rows_revision(store_db):
    """Blast radius = one row: no other card is re-asserted by a comment."""
    # Arrange
    before = _revisions(store_db)
    # Act
    _store.comment_task(str(store_db), "alpha", "a second comment", by="agent:test-suite")
    # Assert
    after = _revisions(store_db)
    assert {tid for tid in before if before[tid] != after[tid]} == {"alpha"}


def test_a_comment_row_the_payload_lacks_survives_a_comment(store_db):
    """The 2026-08-23 loss: a row only the TABLE knows must not be dropped."""
    # Arrange -- a comment that arrived in the table without reaching card_json
    conn = sqlite3.connect(str(store_db))
    try:
        conn.execute(
            "INSERT INTO task_comments(task_id, seq, author, ts, kind, text) "
            "VALUES ('alpha', 1, 'peer', '2026-09-01T01:00:00Z', NULL, 'arrived by sync')"
        )
        conn.commit()
    finally:
        conn.close()
    # Act
    _store.comment_task(str(store_db), "alpha", "third", by="agent:test-suite")
    # Assert
    assert _comment_texts_in_table(store_db, "alpha") == ["first", "arrived by sync", "third"]


def _stale_write(store_db) -> dict:
    """A write handed a revision one ahead of the row's; returns the card."""
    target = resolve_store_target(None)
    card, revision = read_card_or_raise(target, "alpha")
    card["comments"].append({"author": "x", "ts": "2026-09-02T00:00:00Z", "text": "late"})
    try:
        write_card_or_raise(target, card, expected_revision=revision + 1)
    except RevisionConflictError:
        pass
    return card


def test_a_stale_revision_is_refused(store_db):
    """Compare-and-set positive control: a revision the store does not hold is refused."""
    # Arrange
    target = resolve_store_target(None)
    card, revision = read_card_or_raise(target, "alpha")
    card["comments"].append({"author": "x", "ts": "2026-09-02T00:00:00Z", "text": "late"})
    # Act
    stale = revision + 1
    # Assert
    with pytest.raises(RevisionConflictError):
        write_card_or_raise(target, card, expected_revision=stale)


def test_a_refused_write_leaves_the_rows_comments_alone(store_db):
    """The guard refuses BEFORE the drop: nothing of the row is touched."""
    # Arrange
    before = _comment_texts_in_table(store_db, "alpha")
    # Act
    _stale_write(store_db)
    # Assert
    assert _comment_texts_in_table(store_db, "alpha") == before == ["first"]


def test_a_deleted_card_cannot_be_commented(store_db):
    """Tombstone parity with the whole-document path: deleted reads as absent."""
    # Arrange
    _store.delete_task(str(store_db), "beta")
    # Act
    store = str(store_db)
    # Assert
    with pytest.raises(_store.TaskNotFoundError):
        _store.comment_task(store, "beta", "too late", by="agent:test-suite")


# --------------------------------------------------------------------------- #
# The guards still refuse at the seam                                         #
# --------------------------------------------------------------------------- #
def test_a_store_stamped_for_another_identity_is_refused(store_db, env):
    # Arrange
    conn = connect(str(store_db))
    try:
        conn.execute("BEGIN IMMEDIATE")
        stamp_store_uuid(conn, IDENTITY_A)
        conn.commit()
    finally:
        conn.close()
    env.set(ENV_EXPECTED_STORE_UUID, IDENTITY_B)
    # Act
    target = resolve_store_target(None)
    # Assert
    with pytest.raises(RuntimeError, match="stamped"):
        read_card_or_raise(target, "alpha")


def test_a_schemaless_store_is_not_provisioned_rather_than_empty(tmp_path, env):
    # Arrange -- a file that exists and holds no schema at all
    db = tmp_path / "cards.db"
    sqlite3.connect(str(db)).close()
    env.set(ENV_DB, str(db))
    # Act
    target = resolve_store_target(None)
    # Assert
    with pytest.raises(StoreNotProvisionedError):
        read_card_or_raise(target, "alpha")


def test_a_retired_store_is_refused(store_db):
    # Arrange
    conn = connect(str(store_db))
    try:
        conn.execute("BEGIN IMMEDIATE")
        retire_store(
            conn,
            successor_uuid=IDENTITY_B,
            by="agent:test-suite",
            at="2026-09-02T00:00:00Z",
        )
        conn.commit()
    finally:
        conn.close()
    # Act
    target = resolve_store_target(None)
    # Assert
    with pytest.raises(StoreRetired):
        read_card_or_raise(target, "alpha")


# --------------------------------------------------------------------------- #
# The card's own instrument                                                   #
# --------------------------------------------------------------------------- #
def test_a_comment_write_never_exports_the_board(store_db):
    # Arrange
    profile = cProfile.Profile()
    # Act
    profile.enable()
    try:
        _store.comment_task(str(store_db), "alpha", "profiled", by="agent:test-suite")
    finally:
        profile.disable()
    # Assert
    assert _frames_named(profile) & {"export_doc", "_read_canonical_db_or_raise"} == set()


def test_a_single_card_read_never_exports_the_board(store_db):
    # Arrange
    profile = cProfile.Profile()
    # Act
    profile.enable()
    try:
        _store.get_task(str(store_db), "alpha")
    finally:
        profile.disable()
    # Assert
    assert _frames_named(profile) & {"export_doc", "_read_canonical_db_or_raise"} == set()


# EOF
