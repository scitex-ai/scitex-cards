"""Ordinary bulk callers against the repository's real PostgreSQL fixture.

No observer, write capability, row port or event function is replaced. These
entrypoints are identical for V1 and V2; running them requires the supported
isolated writable PostgreSQL fixture, and static compilation is not PG proof.
"""

from copy import deepcopy

import pytest
from conftest import seed_db_from_doc

from scitex_cards._db import ENV_STORE_DSN
from scitex_cards._db_export import export_doc
from scitex_cards._db_mirror import mirror_doc_incremental
from scitex_cards._store_single_card import read_card_or_raise, write_card_or_raise
from scitex_cards._store_write import edit_tasks, save_tasks
from scitex_cards._task import TaskValidationError

_ENROLLED = {
    "version": 1, "required": True, "generation": 1,
    "started_at": "2026-10-03T00:00:00Z", "checkpoints": {},
}
_NAMESPACES = [None, {"version": 1, "required": False}, _ENROLLED]
_ABSENT = object()
_BULK_DOORS = ["mirror", "save", "edit"]


@pytest.fixture
def bulk_store(new_store, env, tmp_path):
    """Hash setup uses unchanged ordinary CAS writes, never SQL hash deletion."""
    def seed(namespace=_ABSENT):
        cards = [
            {"id": "legacy", "title": "Legacy card", "status": "deferred",
             "assignee": "agent:test-suite"},
            {"id": "protected", "title": "Protected card", "status": "deferred",
             "assignee": "agent:test-suite", "repo": "fixture/application",
             "pr_url": "https://github.com/fixture/application/pull/1",
             "_log_meta": {"other": "preserve"}},
        ]
        if namespace is not _ABSENT:
            cards[1]["_log_meta"]["delivery"] = deepcopy(namespace)
        target = new_store("cards_delivery_bulk", bootstrap=False)
        seed_db_from_doc({"tasks": cards}, target)
        env.set(ENV_STORE_DSN, target)
        for task in cards:
            canonical, revision = read_card_or_raise(target, task["id"])
            write_card_or_raise(target, canonical, expected_revision=revision)
        return target, tmp_path / "tasks.yaml"
    return seed


def _protected(tasks):
    return next(task for task in tasks if task["id"] == "protected")


def _rows(target):
    return {tid: read_card_or_raise(target, tid) for tid in ("legacy", "protected")}


def _apply(door, store, mutate):
    """Call the actual bulk API without wrapping or replacing its persistence."""
    target, path = store
    if door == "edit":
        with edit_tasks(path) as tasks:
            mutate(tasks)
        return
    doc = export_doc(target)[0]
    mutate(doc["tasks"])
    if door == "save":
        save_tasks(doc["tasks"], path)
    else:
        mirror_doc_incremental(doc, target, store_path=target)


@pytest.mark.parametrize("door", ["save", "edit"])
@pytest.mark.parametrize("namespace", _NAMESPACES, ids=["null", "optional", "enrolled"])
@pytest.mark.parametrize("change", ["remove", "replace"])
def test_bulk_cannot_remove_or_replace_reserved_namespace(bulk_store, door, namespace, change):
    # Arrange
    store = bulk_store(namespace)
    def mutate(tasks):
        meta = _protected(tasks)["_log_meta"]
        if change == "remove":
            meta.pop("delivery")
        else:
            meta["delivery"] = {"injected": True}
    # Act
    attempt = lambda: _apply(door, store, mutate)
    # Assert
    with pytest.raises(TaskValidationError):
        attempt()


@pytest.mark.parametrize("door", _BULK_DOORS)
@pytest.mark.parametrize("namespace", _NAMESPACES, ids=["null", "optional", "enrolled"])
@pytest.mark.parametrize("new_row", [False, True], ids=["existing-legacy", "new-row"])
def test_bulk_cannot_inject_reserved_namespace(bulk_store, door, namespace, new_row):
    # Arrange
    store = bulk_store()
    def mutate(tasks):
        card = _protected(tasks)
        if new_row:
            card = deepcopy(card)
            card["id"] = "injected"
            tasks.append(card)
        card["_log_meta"]["delivery"] = deepcopy(namespace)
    # Act
    attempt = lambda: _apply(door, store, mutate)
    # Assert
    with pytest.raises(TaskValidationError):
        attempt()


@pytest.mark.parametrize("door", _BULK_DOORS)
def test_bulk_cannot_complete_enrolled_card_without_observer(bulk_store, door):
    # Arrange
    store = bulk_store(_ENROLLED)
    def mutate(tasks):
        _protected(tasks)["status"] = "done"
    # Act
    attempt = lambda: _apply(door, store, mutate)
    # Assert
    with pytest.raises(TaskValidationError):
        attempt()


@pytest.mark.parametrize("door", _BULK_DOORS)
@pytest.mark.parametrize("clock", ["completed_at", "completed_by", "closed_at", "closed_by"])
def test_bulk_cannot_inject_enrolled_completion_or_archive_clock(bulk_store, door, clock):
    # Arrange
    store = bulk_store(_ENROLLED)
    def mutate(tasks):
        _protected(tasks)["_log_meta"][clock] = "2026-10-03T00:12:00Z" if clock.endswith("_at") else "agent:caller"
    # Act
    attempt = lambda: _apply(door, store, mutate)
    # Assert
    with pytest.raises(TaskValidationError):
        attempt()


@pytest.mark.parametrize("door", _BULK_DOORS)
@pytest.mark.parametrize("field", ["repo", "pr_url"])
def test_bulk_retarget_cannot_keep_old_delivery_generation(bulk_store, door, field):
    # Arrange
    store = bulk_store(_ENROLLED)
    def mutate(tasks):
        _protected(tasks)[field] = "fixture/changed" if field == "repo" else "https://github.com/fixture/changed/pull/2"
    # Act
    attempt = lambda: _apply(door, store, mutate)
    # Assert
    with pytest.raises(TaskValidationError):
        attempt()


@pytest.mark.parametrize("door", _BULK_DOORS)
def test_invalid_enrolled_batch_leaves_earlier_legacy_edit_and_all_revisions_unchanged(bulk_store, door):
    """An invalid later row must roll back the complete ordinary caller batch."""
    # Arrange
    store = bulk_store(_ENROLLED)
    before = _rows(store[0])
    def mutate(tasks):
        tasks.sort(key=lambda task: task["id"] != "legacy")
        tasks[0]["title"] = "Must not land"
        _protected(tasks)["status"] = "done"
    # Act
    try:
        _apply(door, store, mutate)
    except TaskValidationError:
        pass
    # Assert
    assert _rows(store[0]) == before


@pytest.mark.parametrize("door", _BULK_DOORS)
@pytest.mark.parametrize("namespace", _NAMESPACES, ids=["null", "optional", "enrolled"])
def test_legacy_bulk_edit_preserves_other_protected_card(bulk_store, door, namespace):
    """The presence of protected metadata does not block unrelated legacy edits."""
    # Arrange
    store = bulk_store(namespace)
    expected = _rows(store[0])
    legacy, revision = expected["legacy"]
    legacy["title"] = "Edited legacy card"
    expected["legacy"] = (legacy, revision + 1)
    def mutate(tasks):
        next(task for task in tasks if task["id"] == "legacy")["title"] = "Edited legacy card"
    # Act
    _apply(door, store, mutate)
    # Assert
    assert _rows(store[0]) == expected


def test_touched_mirror_ignores_invalid_untouched_snapshot(bulk_store):
    """An unrelated stale payload must neither be written nor veto the named row."""
    # Arrange
    target, _path = bulk_store(_ENROLLED)
    before = read_card_or_raise(target, "protected")
    doc = export_doc(target)[0]
    _protected(doc["tasks"])["_log_meta"].pop("delivery")
    next(task for task in doc["tasks"] if task["id"] == "legacy")["title"] = "Edited legacy card"
    # Act
    mirror_doc_incremental(doc, target, store_path=target, touched_ids=["legacy"])
    # Assert
    assert read_card_or_raise(target, "protected") == before
