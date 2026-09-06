#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the write-side shrink guard + `delete_task` tombstone conversion.

P0 (third board wipe, 2026-07-21): the `tasks` table went 2170 -> 18 because
a full-suite pytest run's ambient environment happened to name the LIVE
store, and a stale/replacement document missing almost every row was
persisted straight over it. Operator ruling — 一度書いたものは消えない, "a
written card never disappears" — replaced the original ratio-based brief
with two coupled changes, both covered here:

  1. the doc-level persist chokepoint (`_store_backend.write_doc_to_db`)
     refuses a write that is missing even ONE id the store already has,
     unless that id is named in `deleted_ids` or the caller passes
     `allow_shrink=True` (`StoreShrinkRefusedError`, naming the missing ids);
  2. `delete_task` no longer physically removes a row — it TOMBSTONES it in
     place (`status="cancelled"` + `_log_meta.deleted_at`), so the row
     survives in SQL forever, and every ordinary read (`list_tasks` /
     `get_task` / ...) treats it as absent by default.

Real fixtures (no mocks per STX-NM / PA-306); the autouse conftest fixture
pins `$SCITEX_CARDS_DB` to a fresh, schema-complete scratch database per
test, so every test here is safely isolated from the live store.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards import _store
from scitex_cards._db import connect
from scitex_cards._model import _save_doc_unlocked, load_doc
from scitex_cards._store_target import resolve_store_target
from scitex_cards._task import StoreShrinkRefusedError, _is_tombstoned


def _open_ambient():
    """A connection to the store THIS TEST is pinned to.

    ``resolve_store_target``, not ``resolve_db_path``: the latter is typed
    ``-> Path`` and REFUSES a DSN outright, so routing through it made every
    helper here structurally unable to reach the store it was written to read.
    """
    return connect(resolve_store_target(None))


def _live_row(task_id: str):
    """Raw SQL row for ``task_id``, or ``None``. Bypasses every app-level
    filter (tombstone exclusion, scope, ...) — the ground truth for "is the
    row still physically there."""
    conn = _open_ambient()
    try:
        return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    finally:
        conn.close()


def _live_task_count() -> int:
    conn = _open_ambient()
    try:
        # AN EXPLICIT ALIAS: a COUNT has no column name of its own, and this
        # driver's rows are addressed by name.
        return int(conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"])
    finally:
        conn.close()


def _two_card_store() -> str:
    """The shared arrange for the shrink-refusal tests: a store holding a and b."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    _store.add_task(store, id="b", title="B", assignee="agent:test-suite")
    return store


def _refuses(doc, store, **kwargs) -> bool:
    """Did the shrink guard refuse this write? Returns the VERDICT, never raises.

    Lets a test assert the refusal's OUTCOME as one assertion (STX-TQ007), and
    lets the tests that only need the refusal as a PRECONDITION have it without
    spending their assertion on it.
    """
    try:
        _save_doc_unlocked(doc, store, **kwargs)
    except StoreShrinkRefusedError:
        return True
    return False


def _refusal_message(doc, store, **kwargs) -> str:
    with pytest.raises(StoreShrinkRefusedError) as excinfo:
        _save_doc_unlocked(doc, store, **kwargs)
    return str(excinfo.value)


# === delete_task tombstones (never a physical delete) ======================


class TestDeleteTaskTombstones:
    """delete_task marks the row in place; it never physically disappears."""

    def test_deleted_row_survives_in_sql(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        # Act
        _store.delete_task(store, task_id="a")
        # Assert — the row is still THERE; whether it is correctly marked is
        # the sibling test's claim.
        assert _live_row("a") is not None

    def test_deleted_row_is_marked_cancelled_in_sql(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        # Act
        _store.delete_task(store, task_id="a")
        # Assert — split from "it survives" (STX-TQ007): a row that survives
        # UNMARKED is a different and worse bug than one that vanishes, and
        # merged it could never be the reported failure.
        row = _live_row("a")
        assert row["status"] == "cancelled"

    def test_deleted_row_stamps_deleted_at(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        # Act
        _store.delete_task(store, task_id="a")
        # Assert
        doc = load_doc(store, validate=False)
        deleted = next(t for t in doc["tasks"] if t["id"] == "a")
        assert _is_tombstoned(deleted)

    def test_sql_row_count_does_not_drop_after_delete(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        _store.add_task(store, id="b", title="B", assignee="agent:test-suite")
        before = _live_task_count()
        # Act
        _store.delete_task(store, task_id="a")
        # Assert
        assert _live_task_count() == before

    def test_single_delete_does_not_trip_the_shrink_guard(self, tmp_path):
        # Arrange — a card write that touches ONE row must never be refused
        # as a "shrink"; that refusal exists for a doc-level collapse only.
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        _store.add_task(store, id="b", title="B", assignee="agent:test-suite")
        # Act
        _store.delete_task(store, task_id="a")
        # Assert — the untouched row survived, so the guard did not refuse the
        # write. Asserting the OUTCOME rather than "no exception": a test whose
        # only claim is that nothing was raised also passes when the call does
        # nothing at all.
        assert _live_row("b") is not None


# === reads exclude tombstones by default ====================================


class TestReadsExcludeTombstones:
    """list_tasks / get_task / set_edge treat a tombstoned row as absent."""

    def test_list_tasks_excludes_the_deleted_row(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        _store.add_task(store, id="b", title="B", assignee="agent:test-suite")
        # Act
        _store.delete_task(store, task_id="a")
        # Assert
        assert {t["id"] for t in _store.list_tasks(store, scope="")} == {"b"}

    def test_get_task_raises_not_found_for_a_tombstoned_id(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        _store.delete_task(store, task_id="a")
        # Act
        # Assert
        with pytest.raises(_store.TaskNotFoundError):
            _store.get_task(store, task_id="a")

    def test_set_edge_onto_a_tombstoned_target_raises_not_found(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        _store.add_task(store, id="b", title="B", assignee="agent:test-suite")
        _store.delete_task(store, task_id="a")
        # Act
        # Assert
        with pytest.raises(_store.TaskNotFoundError):
            _store.set_edge(
                store, action="add", kind="depends_on", source="b", target="a"
            )


# === restore_task un-tombstones =============================================


class TestRestoreUnTombstones:
    """restore_task is the delete Undo; it reverses a tombstone in place."""

    def test_restore_makes_the_row_visible_again(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        removed = _store.delete_task(store, task_id="a")["removed"]
        # Act
        _store.restore_task(store, task=removed, refs=[])
        # Assert
        assert _store.get_task(store, task_id="a")["status"] != "cancelled"

    def test_restore_clears_the_tombstone_marker(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        removed = _store.delete_task(store, task_id="a")["removed"]
        # Act
        _store.restore_task(store, task=removed, refs=[])
        # Assert
        assert not _is_tombstoned(_store.get_task(store, task_id="a"))

    def test_restore_onto_a_live_duplicate_id_still_raises(self, tmp_path):
        # Arrange — "a" exists and was NEVER deleted; restoring onto it is a
        # genuine conflict, not an undo.
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        # Act
        # Assert
        with pytest.raises(ValueError):
            _store.restore_task(
                store, task={"id": "a", "title": "A", "status": "deferred"}
            )

    def test_restore_does_not_grow_the_sql_row_count(self, tmp_path):
        # Arrange — undo a delete must not create a SECOND row for the id.
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        removed = _store.delete_task(store, task_id="a")["removed"]
        before = _live_task_count()
        # Act
        _store.restore_task(store, task=removed, refs=[])
        # Assert
        assert _live_task_count() == before


# === the doc-level persist chokepoint (StoreShrinkRefusedError) ============


class TestDocRewriteShrinkGuard:
    """A doc-level write missing rows the store already has is refused."""

    def test_a_doc_rewrite_missing_a_row_raises(self, tmp_path):
        # Arrange — three live cards; a "doc" that only knows about one of
        # them is exactly the 2170->18 collapse's shape.
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        _store.add_task(store, id="b", title="B", assignee="agent:test-suite")
        _store.add_task(store, id="c", title="C", assignee="agent:test-suite")
        doc = load_doc(store, validate=False)
        stale_tasks = [t for t in doc["tasks"] if t["id"] == "a"]
        # Act
        # Assert
        with pytest.raises(StoreShrinkRefusedError):
            _save_doc_unlocked(dict(doc), store, tasks=stale_tasks)

    def test_the_exception_names_the_missing_ids(self, tmp_path):
        # Arrange
        store = _two_card_store()
        doc = load_doc(store, validate=False)
        stale_tasks = [t for t in doc["tasks"] if t["id"] == "a"]
        # Act
        message = _refusal_message(dict(doc), store, tasks=stale_tasks)
        # Assert
        assert "b" in message

    def test_a_refused_write_does_not_touch_the_store(self, tmp_path):
        # Arrange
        store = _two_card_store()
        doc = load_doc(store, validate=False)
        stale_tasks = [t for t in doc["tasks"] if t["id"] == "a"]
        before = _live_task_count()
        # Act — the refusal itself is asserted by its own test; here it is the
        # precondition, so it is caught rather than spent as this test's one
        # assertion. A write that wrongly SUCCEEDED still falls through to the
        # count check below, which is the point of this test.
        _refuses(dict(doc), store, tasks=stale_tasks)
        # Assert
        assert _live_task_count() == before

    def test_allow_shrink_true_bypasses_the_raise(self, tmp_path):
        """`allow_shrink=True` suppresses the refusal.

        THE STX-TQ001 FINDING HERE WAS LEFT OPEN DELIBERATELY BY A PREVIOUS
        AUTHOR, and their reasoning was sound: they tried two outcome
        assertions and both were WRONG about this code —
            `_live_task_count() == 1`  -> actually 2; the store tombstones
            `"b" not in the document`  -> "b" is still there
        so they declined to write a third guess, on the grounds that encoding
        another false belief is worse than an open finding. That was the right
        call given the two options they had.

        The assertion below is a THIRD option that guesses nothing. It does not
        claim anything about what the store contains afterwards — which is the
        part `_save_doc_unlocked`'s contract does not make clear — only that the
        refusal did not fire, which is exactly and only what this test's name
        claims. What `allow_shrink=True` guarantees BEYOND not raising is still
        unknown and still worth someone establishing.
        """
        # Arrange
        store = _two_card_store()
        doc = load_doc(store, validate=False)
        stale_tasks = [t for t in doc["tasks"] if t["id"] == "a"]
        # Act
        refused = _refuses(dict(doc), store, tasks=stale_tasks, allow_shrink=True)
        # Assert
        assert refused is False

    def test_a_doc_naming_the_id_via_deleted_ids_is_not_refused(self, tmp_path):
        # Arrange — the ONE legitimate single-card removal path: the write
        # explicitly names what it intentionally removed.
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        _store.add_task(store, id="b", title="B", assignee="agent:test-suite")
        doc = load_doc(store, validate=False)
        stale_tasks = [t for t in doc["tasks"] if t["id"] == "a"]
        # Act
        _save_doc_unlocked(dict(doc), store, tasks=stale_tasks, deleted_ids=["b"])
        # Assert — naming the removal in `deleted_ids` let the write through,
        # and it genuinely removed the row rather than being quietly ignored.
        assert _live_task_count() == 1

    def test_a_fresh_store_growing_from_zero_is_never_refused(self, tmp_path):
        # Arrange — a brand-new store has nothing stored yet, so there is
        # nothing to be "missing"; growth must never trip the guard.
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        _store.add_task(store, id="b", title="B", assignee="agent:test-suite")
        _store.add_task(store, id="c", title="C", assignee="agent:test-suite")
        # Assert — no raise, for any of the first several writes.
        assert {t["id"] for t in _store.list_tasks(store, scope="")} == {
            "a",
            "b",
            "c",
        }

    def test_an_ordinary_single_card_write_still_passes(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
        # Act
        _store.update_task(store, task_id="a", note="touched")
        # Assert — an ordinary write neither removed the card nor was refused,
        # which is the whole claim: the shrink guard fires on collapse, not on
        # every write that happens to touch one row.
        assert _live_task_count() == 1


# === count never decreases across a public-API sequence ====================


def _counts_across_a_realistic_write_sequence() -> list[int]:
    """Live row count after each public write verb, including deletes/restore."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    counts: list[int] = [_live_task_count()]

    def _step(fn, *args, **kwargs):
        fn(*args, **kwargs)
        counts.append(_live_task_count())

    _step(_store.add_task, store, id="x", title="X", assignee="agent:test-suite")
    _step(_store.add_task, store, id="y", title="Y", assignee="agent:test-suite")
    _step(_store.add_task, store, id="z", title="Z", assignee="agent:test-suite")
    _step(_store.update_task, store, task_id="x", note="hello")
    _step(_store.comment_task, store, task_id="y", text="hi", by="agent:test-suite")
    _step(_store.complete_task, store, task_id="z", by="agent:test-suite")
    removed_x = _store.delete_task(store, task_id="x")["removed"]
    counts.append(_live_task_count())
    _step(_store.restore_task, store, task=removed_x, refs=[])
    _step(_store.delete_task, store, task_id="y")
    return counts


class TestCountNeverDecreasesAcrossASequence:
    """The append-only invariant, exercised over a realistic verb sequence."""

    def test_count_is_monotonically_non_decreasing(self, tmp_path):
        # Arrange
        # Act
        counts = _counts_across_a_realistic_write_sequence()
        # Assert
        assert all(b >= a for a, b in zip(counts, counts[1:]))

    def test_the_final_count_matches_the_cards_actually_created(self, tmp_path):
        # Arrange
        # Act
        counts = _counts_across_a_realistic_write_sequence()
        # Assert — split under STX-TQ007, and it is the more interesting half:
        # a store that LOST every row would still be monotonically
        # non-decreasing from zero, so the invariant alone cannot catch it.
        assert counts[-1] == 3
