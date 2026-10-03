#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A one-card verb reads one card and writes one card — through the same guards.

THE MEASUREMENT THIS PINS (2026-09-02, live primary, 6,542 cards): one
``comment_task`` exported the whole board three times, 2.7 s, against a 3.1 ms
one-card query. Every test below runs against a real throwaway PostgreSQL
schema handed out by the harness (``new_store``); nothing is mocked. The
instrument on the last two tests is the card's own: cProfile, asserting that no
frame named ``export_doc`` (or the canonical read that wraps it) ran at all.

THE GUARD TESTS ARE THE POSITIVE CONTROL. A single-card path that skipped the
canonical read's exists / ownership / retired checks would be faster AND wrong
in exactly the way the 2026-07-19 outage was wrong (one door lenient, one
strict). So three tests here hand the one-card read a store the canonical read
refuses, and require the same refusal.

CONNECTIONS ARE CLOSED IN ``finally`` — see ``new_store``'s docstring: the
schema is dropped CASCADE at teardown and a leaked connection wedges the run.
"""

from __future__ import annotations

import cProfile
from copy import deepcopy

import pytest
from conftest import seed_db_from_doc

from scitex_cards import _store
from scitex_cards._db import ENV_STORE_DSN, connect
from scitex_cards._db_export import export_doc
from scitex_cards._store_errors import RevisionConflictError, StoreNotProvisionedError
from scitex_cards._store_retirement import StoreRetired, retire_store
from scitex_cards._store_single_card import read_card_or_raise, write_card_or_raise
from scitex_cards._store_target import resolve_store_target
from scitex_cards._store_uuid import ENV_EXPECTED_STORE_UUID, stamp_store_uuid
from scitex_cards._task import TaskValidationError

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
def store_db(new_store, env):
    """A real two-card throwaway store, pinned as ``$SCITEX_STORE_DSN``."""
    db = new_store("cards_one_card", bootstrap=False)
    seed_db_from_doc(_DOC, db)
    env.set(ENV_STORE_DSN, db)
    return db


def _revisions(dsn: str) -> dict[str, int]:
    conn = connect(dsn)
    try:
        rows = conn.execute("SELECT id, revision FROM tasks").fetchall()
        return {str(r["id"]): int(r["revision"]) for r in rows}
    finally:
        conn.close()


def _comment_texts_in_table(dsn: str, task_id: str) -> list[str]:
    conn = connect(dsn)
    try:
        rows = conn.execute(
            "SELECT text FROM task_comments WHERE task_id = ? ORDER BY seq", (task_id,)
        ).fetchall()
        return [r["text"] for r in rows]
    finally:
        conn.close()


def _insert_table_only_comment(dsn: str, task_id: str, seq: int, text: str) -> None:
    conn = connect(dsn)
    try:
        conn.execute(
            "INSERT INTO task_comments(task_id, seq, author, ts, kind, text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, seq, "peer", "2026-09-01T01:00:00Z", None, text),
        )
        conn.commit()
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
    _store.comment_task(None, "alpha", "a second comment", by="agent:test-suite")
    # Assert
    after = _revisions(store_db)
    assert {tid for tid in before if before[tid] != after[tid]} == {"alpha"}


def test_a_comment_row_the_payload_lacks_survives_a_comment(store_db):
    """The 2026-08-23 loss: a row only the TABLE knows must not be dropped."""
    # Arrange -- a comment that arrived in the table without reaching card_json
    _insert_table_only_comment(store_db, "alpha", 1, "arrived by sync")
    # Act
    _store.comment_task(None, "alpha", "third", by="agent:test-suite")
    # Assert
    assert _comment_texts_in_table(store_db, "alpha") == ["first", "arrived by sync", "third"]


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
    target = resolve_store_target(None)
    card, revision = read_card_or_raise(target, "alpha")
    card["comments"].append({"author": "x", "ts": "2026-09-02T00:00:00Z", "text": "late"})
    # Act
    try:
        write_card_or_raise(target, card, expected_revision=revision + 1)
    except RevisionConflictError:
        pass
    # Assert
    assert _comment_texts_in_table(store_db, "alpha") == ["first"]


def test_a_deleted_card_cannot_be_commented(store_db):
    """Tombstone parity with the whole-document path: deleted reads as absent."""
    # Arrange
    _store.delete_task(None, "beta")
    # Act
    text = "too late"
    # Assert
    with pytest.raises(_store.TaskNotFoundError):
        _store.comment_task(None, "beta", text, by="agent:test-suite")


# --------------------------------------------------------------------------- #
# The guards still refuse at the seam                                         #
# --------------------------------------------------------------------------- #
def test_a_store_stamped_for_another_identity_is_refused(store_db, env):
    # Arrange
    conn = connect(store_db)
    try:
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


def test_a_schemaless_store_is_not_provisioned_rather_than_empty(new_store, env):
    # Arrange -- a schema that exists and holds no tables at all
    bare = new_store("cards_bare", bootstrap=False)
    env.set(ENV_STORE_DSN, bare)
    # Act
    target = resolve_store_target(None)
    # Assert
    with pytest.raises(StoreNotProvisionedError):
        read_card_or_raise(target, "alpha")


def test_a_retired_store_is_refused(store_db):
    # Arrange
    conn = connect(store_db)
    try:
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
        _store.comment_task(None, "alpha", "profiled", by="agent:test-suite")
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
        _store.get_task(None, "alpha")
    finally:
        profile.disable()
    # Assert
    assert _frames_named(profile) & {"export_doc", "_read_canonical_db_or_raise"} == set()


_ENROLLED = {
    "version": 1, "required": True, "generation": 1,
    "started_at": "2026-10-03T00:00:00Z", "checkpoints": {},
}
_NAMESPACES = [None, {"version": 1, "required": False}, _ENROLLED]
_NO_DELIVERY = object()


@pytest.fixture
def delivery_store(new_store, env):
    """Seed trusted test data; every act uses the ordinary public write door."""
    def seed(namespace=_NO_DELIVERY, *, status="deferred"):
        card = {
            "id": "protected", "title": "Protected card", "status": status,
            "agent": "agent:test-suite", "assignee": "agent:test-suite",
            "last_activity": "2026-10-03T00:00:00Z",
            "_log_meta": {"other": "preserve"}, "comments": [],
        }
        if namespace is not _NO_DELIVERY:
            card["_log_meta"]["delivery"] = deepcopy(namespace)
        if status == "done":
            card["_log_meta"].update(
                completed_at="2026-10-03T00:10:00Z", completed_by="agent:deployer",
                closed_at="2026-10-03T00:11:00Z", closed_by="agent:deployer",
            )
        db = new_store("cards_delivery_one", bootstrap=False)
        seed_db_from_doc({"tasks": [card]}, db)
        env.set(ENV_STORE_DSN, db)
        return db
    return seed


@pytest.mark.parametrize("namespace", _NAMESPACES, ids=["null", "optional", "enrolled"])
@pytest.mark.parametrize("change", ["remove", "replace"])
def test_single_card_cannot_replace_or_remove_reserved_namespace(delivery_store, namespace, change):
    """Optional and explicit null metadata are as reserved as enrollment."""
    # Arrange
    target = delivery_store(namespace)
    proposed, revision = read_card_or_raise(target, "protected")
    if change == "remove":
        proposed["_log_meta"].pop("delivery")
    else:
        proposed["_log_meta"]["delivery"] = {"injected": True}
    # Act
    attempt = lambda: write_card_or_raise(target, proposed, expected_revision=revision)
    # Assert
    with pytest.raises(TaskValidationError):
        attempt()


@pytest.mark.parametrize("namespace", _NAMESPACES, ids=["null", "optional", "enrolled"])
@pytest.mark.parametrize("new_row", [False, True], ids=["existing-legacy", "new-row"])
def test_single_card_cannot_inject_reserved_namespace(delivery_store, namespace, new_row):
    # Arrange
    target = delivery_store()
    proposed, revision = read_card_or_raise(target, "protected")
    if new_row:
        proposed["id"], revision = "injected", None
    proposed["_log_meta"]["delivery"] = deepcopy(namespace)
    # Act
    attempt = lambda: write_card_or_raise(target, proposed, expected_revision=revision)
    # Assert
    with pytest.raises(TaskValidationError):
        attempt()


def test_single_card_cannot_complete_enrolled_card_without_observer(delivery_store):
    # Arrange
    target = delivery_store(_ENROLLED)
    proposed, revision = read_card_or_raise(target, "protected")
    proposed["status"] = "done"
    # Act
    attempt = lambda: write_card_or_raise(target, proposed, expected_revision=revision)
    # Assert
    with pytest.raises(TaskValidationError):
        attempt()


@pytest.mark.parametrize("clock", ["completed_at", "completed_by", "closed_at", "closed_by"])
def test_single_card_cannot_rewrite_enrolled_completion_or_archive_clock(delivery_store, clock):
    # Arrange
    target = delivery_store(_ENROLLED, status="done")
    proposed, revision = read_card_or_raise(target, "protected")
    proposed["_log_meta"][clock] = "2026-10-03T00:12:00Z" if clock.endswith("_at") else "agent:caller"
    # Act
    attempt = lambda: write_card_or_raise(target, proposed, expected_revision=revision)
    # Assert
    with pytest.raises(TaskValidationError):
        attempt()


def test_refused_single_card_completion_leaves_payload_and_revision_unchanged(delivery_store):
    # Arrange
    target = delivery_store(_ENROLLED)
    before = read_card_or_raise(target, "protected")
    proposed = deepcopy(before[0])
    proposed["status"] = "done"
    # Act
    try:
        write_card_or_raise(target, proposed, expected_revision=before[1])
    except TaskValidationError:
        pass
    # Assert
    assert read_card_or_raise(target, "protected") == before


def test_legacy_single_card_edit_still_persists(delivery_store):
    # Arrange
    target = delivery_store()
    proposed, revision = read_card_or_raise(target, "protected")
    proposed["title"] = "Edited legacy card"
    # Act
    write_card_or_raise(target, proposed, expected_revision=revision)
    # Assert
    assert read_card_or_raise(target, "protected") == (proposed, revision + 1)


def test_done_enrolled_comment_changes_only_comments_activity_and_revision(delivery_store):
    """Discussion remains append-only without refreshing completion evidence."""
    # Arrange
    target = delivery_store(_ENROLLED, status="done")
    before, revision = read_card_or_raise(target, "protected")
    expected = deepcopy(before)
    # Act
    result = _store.comment_task(None, "protected", "delivery discussion", by="agent:test-suite", entry_points=())
    expected["comments"].append(result["comment"])
    expected["last_activity"] = result["comment"]["ts"]
    # Assert
    assert read_card_or_raise(target, "protected") == (expected, revision + 1)


# EOF
