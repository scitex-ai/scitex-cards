#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the SQLite cards+comments backend (Phase 2 of the store
migration, slice A).

Real sqlite temp DBs, NO mocks (STX-NM / PA-306): every test writes to a
real DB under ``tmp_path``'s runtime dir (resolved from an explicit
``store=``). NEVER touches ``~/.scitex/todo/tasks.yaml`` (a live bulk
repair may be writing to it concurrently). Covers:

* the backend switch default is YAML (unchanged fleet behaviour);
* opt-in sqlite (``SCITEX_TODO_STORE_BACKEND=sqlite``) works end-to-end;
* lazy one-time migration on first SQLite access preserves every card AND
  its comments, without touching the YAML file;
* migration is idempotent (running it twice inserts nothing new);
* closed-enum validation (``kind`` / ``blocker``) still rejects bad values,
  reusing the SAME validator the YAML path runs;
* round-trip of a card with multiple comments.
"""

from __future__ import annotations

from scitex_todo import _store as yaml_store
from scitex_todo import _store_backend
from scitex_todo import _store_sqlite as sq
from scitex_todo._model import TaskValidationError


def _store(tmp_path):
    return tmp_path / "tasks.yaml"


# --------------------------------------------------------------------------- #
# backend switch                                                              #
# --------------------------------------------------------------------------- #
def test_default_backend_is_yaml(tmp_path, env):
    env.delete("SCITEX_TODO_STORE_BACKEND")
    store = _store(tmp_path)
    _store_backend.add_task(
        store, id="t1", title="hello", assignee="bob", created_by="bob"
    )
    # Written to the YAML store; no sqlite DB created.
    assert store.exists()
    assert not sq.store_db_path(store).exists()
    tasks = yaml_store.list_tasks(store, scope="")
    assert [t["id"] for t in tasks] == ["t1"]


def test_opt_in_sqlite_works(tmp_path, env):
    env.set("SCITEX_TODO_STORE_BACKEND", "sqlite")
    store = _store(tmp_path)
    _store_backend.add_task(
        store, id="t1", title="hello", assignee="bob", created_by="bob"
    )
    assert sq.store_db_path(store).exists()
    # The YAML file was never written by the sqlite path.
    assert not store.exists()
    got = _store_backend.get_task(store, "t1")
    assert got["title"] == "hello"
    assert got["assignee"] == "bob"


# --------------------------------------------------------------------------- #
# lazy migration                                                              #
# --------------------------------------------------------------------------- #
def test_lazy_auto_migration_preserves_cards_and_comments(tmp_path):
    store = _store(tmp_path)
    # Seed the YAML store directly (the pre-existing production data shape).
    yaml_store.add_task(
        store, id="c1", title="card one", assignee="alice", created_by="alice"
    )
    yaml_store.add_task(
        store, id="c2", title="card two", assignee="bob", created_by="bob"
    )
    yaml_store.comment_task(store, "c1", text="first comment", by="alice")
    yaml_store.comment_task(store, "c1", text="second comment", by="bob")
    yaml_store.comment_task(store, "c2", text="only comment", by="bob")

    # No sqlite DB yet.
    assert not sq.store_db_path(store).exists()

    # First SQLite access lazily migrates everything.
    tasks = sq.list_tasks(store)
    assert {t["id"] for t in tasks} == {"c1", "c2"}
    assert sq.store_db_path(store).exists()

    c1 = sq.get_task(store, "c1")
    assert [c["text"] for c in c1["comments"]] == ["first comment", "second comment"]
    assert [c["author"] for c in c1["comments"]] == ["alice", "bob"]

    c2 = sq.get_task(store, "c2")
    assert [c["text"] for c in c2["comments"]] == ["only comment"]

    # The YAML file is untouched (still has both cards + all 3 comments).
    yaml_tasks = yaml_store.list_tasks(store, scope="")
    assert {t["id"] for t in yaml_tasks} == {"c1", "c2"}
    yaml_c1 = yaml_store.get_task(store, "c1")
    assert len(yaml_c1["comments"]) == 2

    # A second access does NOT re-migrate / duplicate (flag guard).
    again = sq.list_tasks(store)
    assert len(again) == 2
    c1_again = sq.get_task(store, "c1")
    assert len(c1_again["comments"]) == 2


def test_migration_is_idempotent(tmp_path):
    store = _store(tmp_path)
    yaml_store.add_task(
        store, id="c1", title="card one", assignee="alice", created_by="alice"
    )
    yaml_store.comment_task(store, "c1", text="a comment", by="alice")

    first = sq.migrate_to_sqlite(store=store)
    assert first["inserted"] == 1
    assert first["comments"] == 1

    second = sq.migrate_to_sqlite(store=store)
    assert second["inserted"] == 0
    assert second["skipped"] == 1
    assert second["comments"] == 0  # skipped tasks' comments are not re-copied

    got = sq.get_task(store, "c1")
    assert len(got["comments"]) == 1  # no duplication


# --------------------------------------------------------------------------- #
# closed-enum validation parity                                               #
# --------------------------------------------------------------------------- #
def test_add_task_rejects_bad_kind(tmp_path):
    store = _store(tmp_path)
    try:
        sq.add_task(
            store,
            id="c1",
            title="x",
            assignee="bob",
            created_by="bob",
            kind="not-a-real-kind",
        )
        raise AssertionError("expected TaskValidationError")
    except TaskValidationError as exc:
        assert "kind" in str(exc)
    # Nothing was persisted.
    assert sq.list_tasks(store) == []


def test_add_task_rejects_bad_blocker(tmp_path):
    store = _store(tmp_path)
    try:
        sq.add_task(
            store,
            id="c1",
            title="x",
            assignee="bob",
            created_by="bob",
            status="blocked",
            blocker="not-a-real-blocker",
        )
        raise AssertionError("expected TaskValidationError")
    except TaskValidationError as exc:
        assert "blocker" in str(exc)
    assert sq.list_tasks(store) == []


def test_add_task_accepts_valid_blocker(tmp_path):
    store = _store(tmp_path)
    got = sq.add_task(
        store,
        id="c1",
        title="x",
        assignee="bob",
        created_by="bob",
        status="blocked",
        blocker="operator-decision",
    )
    assert got["blocker"] == "operator-decision"


def test_update_task_rejects_bad_kind(tmp_path):
    store = _store(tmp_path)
    sq.add_task(store, id="c1", title="x", assignee="bob", created_by="bob")
    try:
        sq.update_task(store, "c1", kind="bogus")
        raise AssertionError("expected TaskValidationError")
    except TaskValidationError:
        pass
    # Unchanged on disk.
    assert sq.get_task(store, "c1").get("kind") is None


# --------------------------------------------------------------------------- #
# round-trip: card with comments                                              #
# --------------------------------------------------------------------------- #
def test_round_trip_card_with_comments(tmp_path):
    store = _store(tmp_path)
    sq.add_task(
        store, id="c1", title="my card", assignee="alice", created_by="alice"
    )
    sq.comment_task(store, "c1", text="first", by="alice")
    sq.comment_task(store, "c1", text="second", by="bob", kind="push")

    got = sq.get_task(store, "c1")
    assert got["title"] == "my card"
    assert len(got["comments"]) == 2
    assert got["comments"][0] == {"author": "alice", "ts": got["comments"][0]["ts"], "text": "first"}
    assert got["comments"][1]["text"] == "second"
    assert got["comments"][1]["kind"] == "push"

    listed = sq.list_tasks(store)
    assert len(listed) == 1
    assert len(listed[0]["comments"]) == 2


def test_comment_task_unknown_id_raises(tmp_path):
    store = _store(tmp_path)
    try:
        sq.comment_task(store, "nope", text="x", by="alice")
        raise AssertionError("expected TaskNotFoundError")
    except sq.TaskNotFoundError:
        pass


# --------------------------------------------------------------------------- #
# other CRUD verbs — sanity coverage                                          #
# --------------------------------------------------------------------------- #
def test_update_task_persists_and_stamps_last_activity(tmp_path):
    store = _store(tmp_path)
    sq.add_task(store, id="c1", title="x", assignee="alice", created_by="alice")
    updated = sq.update_task(store, "c1", title="y", priority=1)
    assert updated["title"] == "y"
    assert updated["priority"] == 1
    assert "last_activity" in updated


def test_complete_task_is_idempotent(tmp_path):
    store = _store(tmp_path)
    sq.add_task(store, id="c1", title="x", assignee="alice", created_by="alice")
    first = sq.complete_task(store, "c1", by="alice")
    assert first["status"] == "done"
    first_stamp = first["_log_meta"]["completed_at"]
    second = sq.complete_task(store, "c1", by="alice")
    assert second["_log_meta"]["completed_at"] == first_stamp


def test_reassign_task_changes_owner_and_comments(tmp_path):
    store = _store(tmp_path)
    sq.add_task(store, id="c1", title="x", assignee="alice", created_by="alice")
    out = sq.reassign_task(store, "c1", "bob", by="alice")
    assert out["changed"] is True
    assert out["from_owner"] == "alice"
    assert out["to_owner"] == "bob"
    got = sq.get_task(store, "c1")
    assert got["assignee"] == "bob"
    assert got["scope"] == "agent:bob"
    assert any("reassigned" in c["text"] for c in got["comments"])
    # Same-owner reassign is a no-op.
    noop = sq.reassign_task(store, "c1", "bob", by="alice")
    assert noop["changed"] is False


def test_delete_task_removes_card_and_scrubs_refs(tmp_path):
    store = _store(tmp_path)
    sq.add_task(store, id="c1", title="parent", assignee="alice", created_by="alice")
    sq.add_task(
        store,
        id="c2",
        title="child",
        assignee="alice",
        created_by="alice",
        parent="c1",
        depends_on=["c1"],
    )
    result = sq.delete_task(store, "c1")
    assert result["removed"]["id"] == "c1"
    assert result["refs"] == ["c2"]
    remaining = sq.get_task(store, "c2")
    assert "parent" not in remaining
    assert "depends_on" not in remaining
    assert sq.list_tasks(store) == [sq.get_task(store, "c2")]


# EOF
