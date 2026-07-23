#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guard the non-``tasks`` section-preservation property of the write path.

Perf fix (2026-07): the CRUD read-modify-write verbs used to parse the store
TWICE per write — once via ``load_tasks`` for the payload they mutate, then a
second re-read whose ONLY job was to recover the non-``tasks`` top-level
sections (notably the ``users:`` registry) so they survived the rewrite. The
fix loads the FULL doc ONCE under the lock (``load_doc``) and threads it through
to ``_save_doc_unlocked`` — so the redundant parse is gone but the preservation
property MUST be identical.

Post-cutover the store is SQLite, not a YAML file, so the doc is reconstructed
from the DB tables rather than a second parse; the property under test is the
same one. The FIXED schema owns exactly the sections it stores — ``tasks``,
``users`` (a list of records, each keyed by ``id``), ``inboxes`` and
``messages``. There is no longer an arbitrary ``meta:`` (or any other opaque)
top-level section that a write must carry through verbatim: that generality was
a YAML-serialization concern and it is gone. What remains, and what these tests
pin, is that the ``users:`` registry present in the store at CRUD-call time
survives every mutation verb, and the doc still round-trips (the mutated task is
present after reload).

Real fixtures, no mocks (STX-NM / PA-306).
"""

from __future__ import annotations

import os

from scitex_cards import _model, _store


def _seed_store_with_users(tasks, users):
    """Seed the canonical DB with a ``tasks`` list + a ``users`` registry, the
    way the real shared store looks, and return the STORE identity path.

    SQLite is the store now: load/save + the CRUD verbs read and write the
    canonical database, so we seed THAT (not a file) and hand back the pinned
    store path they resolve to — NOT the DB path (a write stamped with any other
    path fails the next read's ownership check). ``users`` is the DB-native
    shape: a list of user records, each with an ``id`` (the old name→attrs dict
    is not a store section the schema knows how to hold). Fixture-only seed."""
    from conftest import seed_db_from_doc

    norm = [{"status": "pending", **t} for t in tasks]  # status is required
    seed_db_from_doc({"tasks": norm, "users": users}, os.environ["SCITEX_CARDS_DB"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


#: Each CRUD verb below is exercised once and then asserted on two ways: the
#: ``users:`` registry survived, and the mutation itself actually landed. They
#: are split into sibling tests because the second is what stops the first from
#: passing vacuously — a write path that preserved every section by never
#: writing at all would sail through a users-only check.
#: ``kind`` is NOT NULL in the ``users`` table, so a valid DB-native user
#: record must carry it; every other key round-trips verbatim via record_json.
_ADD_USERS = [
    {"id": "alice", "kind": "agent", "role": "dev"},
    {"id": "bob", "kind": "agent", "role": "qa"},
]
_UPDATE_USERS = [{"id": "alice", "kind": "agent", "role": "dev"}]
_DELETE_USERS = [{"id": "carol", "kind": "agent", "role": "lead"}]
_COMMENT_USERS = [{"id": "dave", "kind": "agent", "role": "dev"}]


def _doc_after_add(tmp_path):
    store = _seed_store_with_users(tasks=[{"id": "a", "title": "A"}], users=_ADD_USERS)
    _store.add_task(store, id="b", title="B", assignee="agent:test")
    return _model.load_doc(store)


def _doc_after_update(tmp_path):
    store = _seed_store_with_users(
        tasks=[{"id": "a", "title": "A", "status": "pending"}],
        users=_UPDATE_USERS,
    )
    _store.update_task(store, task_id="a", status="in_progress")
    return _model.load_doc(store)


def _doc_after_delete(tmp_path):
    store = _seed_store_with_users(
        tasks=[{"id": "a", "title": "A"}, {"id": "b", "title": "B"}],
        users=_DELETE_USERS,
    )
    _store.delete_task(store, task_id="a")
    return _model.load_doc(store)


def _doc_after_comment(tmp_path):
    store = _seed_store_with_users(
        tasks=[{"id": "a", "title": "A"}], users=_COMMENT_USERS
    )
    _store.comment_task(store, task_id="a", text="hello", by="dave")
    return _model.load_doc(store)


class TestSectionPreservationAcrossCRUD:
    """The ``users:`` section survives each write verb."""

    def test_users_survives_add_task(self, tmp_path):
        # Arrange
        expected = _ADD_USERS
        # Act
        doc = _doc_after_add(tmp_path)
        # Assert
        assert doc["users"] == expected

    def test_add_task_still_appends_the_new_row(self, tmp_path):
        # Arrange
        expected_ids = {"a", "b"}
        # Act
        doc = _doc_after_add(tmp_path)
        # Assert — preservation must not come at the cost of the write.
        assert {t["id"] for t in doc["tasks"]} == expected_ids

    def test_users_survives_update_task(self, tmp_path):
        # Arrange
        expected = _UPDATE_USERS
        # Act
        doc = _doc_after_update(tmp_path)
        # Assert
        assert doc["users"] == expected

    def test_update_task_still_applies_the_status_flip(self, tmp_path):
        # Arrange
        expected_status = "in_progress"
        # Act
        doc = _doc_after_update(tmp_path)
        # Assert
        task = next(t for t in doc["tasks"] if t["id"] == "a")
        assert task["status"] == expected_status

    def test_users_survives_delete_task(self, tmp_path):
        # Arrange
        expected = _DELETE_USERS
        # Act
        doc = _doc_after_delete(tmp_path)
        # Assert
        assert doc["users"] == expected

    def test_delete_task_still_removes_the_row(self, tmp_path):
        # Arrange
        expected_ids = {"b"}
        # Act
        doc = _doc_after_delete(tmp_path)
        # Assert
        assert {t["id"] for t in doc["tasks"]} == expected_ids

    def test_users_survives_comment_task(self, tmp_path):
        # Arrange
        expected = _COMMENT_USERS
        # Act
        doc = _doc_after_comment(tmp_path)
        # Assert
        assert doc["users"] == expected

    def test_comment_task_still_appends_the_comment(self, tmp_path):
        # Arrange
        expected_text = "hello"
        # Act
        doc = _doc_after_comment(tmp_path)
        # Assert
        task = next(t for t in doc["tasks"] if t["id"] == "a")
        assert any(c.get("text") == expected_text for c in task["comments"])


class TestRoundTrip:
    """The mutated payload is present after reload (write actually lands)."""

    def test_added_task_present_after_reload(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="x", title="X", assignee="agent:test")
        # Act
        tasks = _model.load_tasks(store)
        # Assert
        assert any(t["id"] == "x" for t in tasks)


#: The doc primitives, each seeded then read back. As above, the section checks
#: are split from the tasks check: a primitive that preserves everything by
#: writing nothing is the exact bug these guard, and only the tasks assertion
#: can catch it.
def _seed_primitive_store(tmp_path, users):
    return _seed_store_with_users(tasks=[{"id": "a", "title": "A"}], users=users)


def _doc_after_save_doc_unlocked(tmp_path):
    store = _seed_primitive_store(tmp_path, [{"id": "u1", "kind": "agent"}])
    with _model._store_lock(store):
        doc = _model.load_doc(store)
        new_tasks = list(doc["tasks"]) + [
            {"id": "b", "title": "B", "status": "pending"}
        ]
        _model._save_doc_unlocked(doc, store, tasks=new_tasks)
    return _model.load_doc(store)


def _doc_after_save_tasks_unlocked(tmp_path):
    store = _seed_primitive_store(tmp_path, [{"id": "u2", "kind": "agent"}])
    with _model._store_lock(store):
        _model._save_tasks_unlocked(
            [
                {"id": "a", "title": "A", "status": "pending"},
                {"id": "c", "title": "C", "status": "pending"},
            ],
            store,
        )
    return _model.load_doc(store)


class TestDocPrimitives:
    """Direct unit coverage of ``load_doc`` / ``_save_doc_unlocked``."""

    def test_load_doc_returns_the_users_section(self, tmp_path):
        # Arrange
        store = _seed_primitive_store(tmp_path, [{"id": "u1", "kind": "agent"}])
        # Act
        doc = _model.load_doc(store)
        # Assert
        assert doc.get("users") == [{"id": "u1", "kind": "agent"}]

    def test_load_doc_returns_the_tasks_list(self, tmp_path):
        # Arrange
        store = _seed_primitive_store(tmp_path, [{"id": "u1", "kind": "agent"}])
        # Act
        doc = _model.load_doc(store)
        # Assert
        assert [t["id"] for t in doc["tasks"]] == ["a"]

    def test_save_doc_unlocked_preserves_the_users_section(self, tmp_path):
        # Arrange
        expected = [{"id": "u1", "kind": "agent"}]
        # Act
        reloaded = _doc_after_save_doc_unlocked(tmp_path)
        # Assert
        assert reloaded["users"] == expected

    def test_save_doc_unlocked_writes_the_mutated_tasks(self, tmp_path):
        # Arrange
        expected_ids = {"a", "b"}
        # Act
        reloaded = _doc_after_save_doc_unlocked(tmp_path)
        # Assert
        assert {t["id"] for t in reloaded["tasks"]} == expected_ids

    def test_save_tasks_unlocked_wrapper_preserves_users(self, tmp_path):
        """The thin back-compat wrapper holds only `tasks`, not the doc — it
        must STILL recover the on-disk `users:` via its single re-read."""
        # Arrange
        expected = [{"id": "u2", "kind": "agent"}]
        # Act
        reloaded = _doc_after_save_tasks_unlocked(tmp_path)
        # Assert
        assert reloaded["users"] == expected

    def test_save_tasks_unlocked_wrapper_writes_the_new_tasks(self, tmp_path):
        # Arrange
        expected_ids = {"a", "c"}
        # Act
        reloaded = _doc_after_save_tasks_unlocked(tmp_path)
        # Assert
        assert {t["id"] for t in reloaded["tasks"]} == expected_ids


class TestReadUnderLock:
    def test_on_disk_users_at_calltime_survives_locked_crud(self, tmp_path):
        """Read-under-lock semantics: the `users:` present in the store WHEN the
        locked CRUD call begins is the version that survives — the write verb
        reads the full doc under the lock and rewrites it whole, so a concurrent
        writer's users-block that was already committed at call-time is not
        clobbered by the tasks-only mutation.
        """
        # Arrange
        store = _seed_store_with_users(
            tasks=[{"id": "a", "title": "A"}],
            users=[{"id": "e", "kind": "agent", "x": 1}],
        )
        # Act
        _store.update_task(store, task_id="a", priority=1)
        # Assert — the users block in the store at call-time is intact post-write.
        assert _model.load_doc(store)["users"] == [{"id": "e", "kind": "agent", "x": 1}]
