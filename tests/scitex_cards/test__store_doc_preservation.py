#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guard the non-``tasks`` section-preservation property of the write path.

Perf fix (2026-07): the CRUD read-modify-write verbs used to parse the store
TWICE per write — once via ``load_tasks`` for the payload they mutate, then a
second ``safe_load`` re-read inside ``_save_tasks_unlocked`` whose ONLY job was
to recover the non-``tasks`` top-level sections (notably the ``users:``
registry) so they survived the rewrite. The fix loads the FULL doc ONCE under
the lock (``load_doc``) and threads it through to ``_save_doc_unlocked`` — so
the second parse is gone but the preservation property MUST be identical.

These tests pin that property: a ``users:`` (and any other) top-level section
present on disk at CRUD-call time survives every mutation verb, and the doc
still round-trips (the mutated task is present after reload).

Real fixtures, no mocks (STX-NM / PA-306).
"""

from __future__ import annotations

import pytest

from scitex_cards import _model, _store
from scitex_cards._yaml import safe_dump


@pytest.fixture(autouse=True)
def _no_store_git_autocommit(env):
    """Disable the per-store git autocommit for every test here.

    These tests exercise the CRUD write path, which normally fires a git
    ``init``/``add``/``commit`` per save. That subprocess is (a) irrelevant to
    the section-preservation property under test and (b) a source of
    non-determinism under heavy parallel/IO load (a git index-lock conflict can
    surface as a transient partial read). Turning it off via the real opt-out
    knob (not a mock — PA-306) makes these write-path tests deterministic and
    fast. The knob defaults ON, so production behaviour is unchanged.
    """
    env.set("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "0")


def _seed_store_with_users(path, tasks, users):
    """Write a store file that has BOTH a ``tasks:`` list and a ``users:``
    registry, the way the real shared store looks. Fixture-only direct write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    norm = [{"status": "pending", **t} for t in tasks]  # status is required
    with path.open("w", encoding="utf-8") as handle:
        safe_dump({"tasks": norm, "users": users, "meta": {"seed": "v1"}}, handle)


#: Each CRUD verb below is exercised once and then asserted on three ways: the
#: ``users:`` registry survived, the unrelated ``meta:`` section survived, and
#: the mutation itself actually landed. They are split into sibling tests
#: because the third is what stops the other two from passing vacuously — a
#: write path that preserved every section by never writing at all would sail
#: through a users-only check.
_ADD_USERS = {"alice": {"role": "dev"}, "bob": {"role": "qa"}}
_UPDATE_USERS = {"alice": {"role": "dev"}}
_DELETE_USERS = {"carol": {"role": "lead"}}
_COMMENT_USERS = {"dave": {"role": "dev"}}
_SEED_META = {"seed": "v1"}


def _doc_after_add(tmp_path):
    store = tmp_path / "tasks.yaml"
    _seed_store_with_users(store, tasks=[{"id": "a", "title": "A"}], users=_ADD_USERS)
    _store.add_task(store, id="b", title="B", assignee="agent:test")
    return _model.load_doc(store)


def _doc_after_update(tmp_path):
    store = tmp_path / "tasks.yaml"
    _seed_store_with_users(
        store,
        tasks=[{"id": "a", "title": "A", "status": "pending"}],
        users=_UPDATE_USERS,
    )
    _store.update_task(store, task_id="a", status="in_progress")
    return _model.load_doc(store)


def _doc_after_delete(tmp_path):
    store = tmp_path / "tasks.yaml"
    _seed_store_with_users(
        store,
        tasks=[{"id": "a", "title": "A"}, {"id": "b", "title": "B"}],
        users=_DELETE_USERS,
    )
    _store.delete_task(store, task_id="a")
    return _model.load_doc(store)


def _doc_after_comment(tmp_path):
    store = tmp_path / "tasks.yaml"
    _seed_store_with_users(
        store, tasks=[{"id": "a", "title": "A"}], users=_COMMENT_USERS
    )
    _store.comment_task(store, task_id="a", text="hello", by="dave")
    return _model.load_doc(store)


class TestSectionPreservationAcrossCRUD:
    """The ``users:`` / ``meta:`` sections survive each write verb."""

    def test_users_survives_add_task(self, tmp_path):
        # Arrange
        expected = _ADD_USERS
        # Act
        doc = _doc_after_add(tmp_path)
        # Assert
        assert doc["users"] == expected

    def test_meta_survives_add_task(self, tmp_path):
        # Arrange
        expected = _SEED_META
        # Act
        doc = _doc_after_add(tmp_path)
        # Assert — it is not just `users:` that is recovered, but every section.
        assert doc["meta"] == expected

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
        store = tmp_path / "tasks.yaml"
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
    store = tmp_path / "tasks.yaml"
    _seed_store_with_users(store, tasks=[{"id": "a", "title": "A"}], users=users)
    return store


def _doc_after_save_doc_unlocked(tmp_path):
    store = _seed_primitive_store(tmp_path, {"u": 1})
    with _model._store_lock(store):
        doc = _model.load_doc(store)
        new_tasks = list(doc["tasks"]) + [
            {"id": "b", "title": "B", "status": "pending"}
        ]
        _model._save_doc_unlocked(doc, store, tasks=new_tasks)
    return _model.load_doc(store)


def _doc_after_save_tasks_unlocked(tmp_path):
    store = _seed_primitive_store(tmp_path, {"u": 2})
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
    """Direct unit coverage of the new ``load_doc`` / ``_save_doc_unlocked``."""

    def test_load_doc_returns_the_users_section(self, tmp_path):
        # Arrange
        store = _seed_primitive_store(tmp_path, {"u": 1})
        # Act
        doc = _model.load_doc(store)
        # Assert
        assert doc.get("users") == {"u": 1}

    def test_load_doc_returns_the_meta_section(self, tmp_path):
        # Arrange
        store = _seed_primitive_store(tmp_path, {"u": 1})
        # Act
        doc = _model.load_doc(store)
        # Assert — the FULL doc, not a tasks-plus-users special case.
        assert doc.get("meta") == _SEED_META

    def test_load_doc_returns_the_tasks_list(self, tmp_path):
        # Arrange
        store = _seed_primitive_store(tmp_path, {"u": 1})
        # Act
        doc = _model.load_doc(store)
        # Assert
        assert [t["id"] for t in doc["tasks"]] == ["a"]

    def test_save_doc_unlocked_preserves_the_users_section(self, tmp_path):
        # Arrange
        expected = {"u": 1}
        # Act
        reloaded = _doc_after_save_doc_unlocked(tmp_path)
        # Assert
        assert reloaded["users"] == expected

    def test_save_doc_unlocked_preserves_the_meta_section(self, tmp_path):
        # Arrange
        expected = _SEED_META
        # Act
        reloaded = _doc_after_save_doc_unlocked(tmp_path)
        # Assert
        assert reloaded["meta"] == expected

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
        expected = {"u": 2}
        # Act
        reloaded = _doc_after_save_tasks_unlocked(tmp_path)
        # Assert
        assert reloaded["users"] == expected

    def test_save_tasks_unlocked_wrapper_preserves_meta(self, tmp_path):
        # Arrange
        expected = _SEED_META
        # Act
        reloaded = _doc_after_save_tasks_unlocked(tmp_path)
        # Assert
        assert reloaded["meta"] == expected

    def test_save_tasks_unlocked_wrapper_writes_the_new_tasks(self, tmp_path):
        # Arrange
        expected_ids = {"a", "c"}
        # Act
        reloaded = _doc_after_save_tasks_unlocked(tmp_path)
        # Assert
        assert {t["id"] for t in reloaded["tasks"]} == expected_ids


class TestGitAutocommitOptOut:
    """The ``SCITEX_TODO_STORE_GIT_AUTOCOMMIT`` knob gates the per-save commit."""

    def test_autocommit_skipped_when_disabled(self, tmp_path, env):
        # Arrange
        env.set("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "0")
        store = tmp_path / "tasks.yaml"
        # Act
        _store.add_task(store, id="a", title="A", assignee="agent:test")
        # Assert — a write that would normally lazy-init a per-store .git did not.
        assert not (tmp_path / ".git").exists()

    def test_write_still_lands_when_autocommit_is_disabled(self, tmp_path, env):
        # Arrange
        env.set("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "0")
        store = tmp_path / "tasks.yaml"
        # Act
        _store.add_task(store, id="a", title="A", assignee="agent:test")
        # Assert — the knob turns off the recovery layer, not the store.
        assert any(t["id"] == "a" for t in _model.load_tasks(store))

    def test_autocommit_runs_when_enabled(self, tmp_path, env):
        # Arrange
        env.set("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "1")
        store = tmp_path / "tasks.yaml"
        # Act
        _store.add_task(store, id="a", title="A", assignee="agent:test")
        # Assert — the recovery-layer .git was lazily initialized.
        assert (tmp_path / ".git").exists()


class TestReadUnderLock:
    def test_on_disk_users_at_calltime_survives_locked_crud(self, tmp_path):
        """Read-under-lock semantics: the `users:` present on disk WHEN the
        locked CRUD call begins is the version that survives — the write verb
        reads the full doc under the lock and rewrites it whole, so a concurrent
        writer's users-block that was already committed to disk at call-time is
        not clobbered by the tasks-only mutation.
        """
        # Arrange
        store = tmp_path / "tasks.yaml"
        _seed_store_with_users(
            store, tasks=[{"id": "a", "title": "A"}], users={"e": {"x": 1}}
        )
        # Act
        _store.update_task(store, task_id="a", priority=1)
        # Assert — the users block on disk at call-time is intact post-write.
        assert _model.load_doc(store)["users"] == {"e": {"x": 1}}
