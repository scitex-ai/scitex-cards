#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A stale completion stamp on a non-`done` card is healable, and delete does not mint one.

WHY THIS FILE EXISTS (2026-11-26): the doctor's ``terminal_state_honest`` check
reported 11 live cards carrying ``_log_meta.completed_at`` while ``cancelled``.
Two defects made that population both GROW and STAY:

1. ``delete_task`` tombstones a card to ``cancelled`` but kept its completion
   stamp, so deleting a ``done`` card left it counted as delivered work.
2. ``update_task``'s stamp clock acted only on a transition OUT OF ``done``.
   A row that was already non-``done`` and stamped (written before the clock
   existed, or by a path that bypassed it) could never be healed through the
   API: no later write was an "exit", and the doctor's hint had no verb to name.

Store-touching tests request the ``postgres_dsn`` fail-LOUD fixture.
"""

import os

from conftest import seed_db_from_doc

from scitex_cards._health import _check_terminal_state_honest
from scitex_cards._model import load_tasks
from scitex_cards._store_lifecycle import complete_task, delete_task, restore_task
from scitex_cards._store_mutate import update_task

_STAMP = {"completed_at": "2026-08-01T00:00:00Z", "completed_by": "someone"}


def _store(tasks):
    seed_db_from_doc({"tasks": tasks}, os.environ["SCITEX_STORE_DSN"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def _row(store, task_id):
    return next(t for t in load_tasks(store) if t["id"] == task_id)


def _stale_cancelled_store():
    return _store(
        [
            {
                "id": "stale",
                "title": "cancelled but stamped",
                "status": "cancelled",
                "_log_meta": dict(_STAMP),
            }
        ]
    )


def test_the_seeded_stale_row_is_reported_by_the_doctor(postgres_dsn):
    # Arrange — positive control: the fixture really is the defect.
    store = _stale_cancelled_store()
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is False


def test_a_same_status_update_drops_a_stale_stamp(postgres_dsn):
    # Arrange
    store = _stale_cancelled_store()
    # Act
    update_task(store, "stale", status="cancelled")
    # Assert
    assert "completed_at" not in (_row(store, "stale").get("_log_meta") or {})


def test_a_same_status_update_keeps_the_status(postgres_dsn):
    # Arrange
    store = _stale_cancelled_store()
    # Act
    update_task(store, "stale", status="cancelled")
    # Assert
    assert _row(store, "stale")["status"] == "cancelled"


def test_the_doctor_is_clean_after_the_heal(postgres_dsn):
    # Arrange
    store = _stale_cancelled_store()
    # Act
    update_task(store, "stale", status="cancelled")
    # Assert
    assert _check_terminal_state_honest(store)["ok"] is True


def test_an_update_to_a_done_card_keeps_its_genuine_stamp(postgres_dsn):
    # Arrange — no false heal: done + stamp is the honest state.
    store = _store(
        [{"id": "d", "title": "D", "status": "done", "_log_meta": dict(_STAMP)}]
    )
    # Act
    update_task(store, "d", priority=2)
    # Assert
    assert _row(store, "d")["_log_meta"]["completed_at"] == _STAMP["completed_at"]


def test_deleting_a_done_card_drops_its_completion_stamp(postgres_dsn):
    # Arrange
    store = _store([{"id": "x", "title": "X", "status": "blocked"}])
    complete_task(store, "x", by="tester")
    # Act
    delete_task(store, "x")
    # Assert
    assert "completed_at" not in (_row(store, "x").get("_log_meta") or {})


def test_deleting_a_done_card_leaves_the_doctor_clean(postgres_dsn):
    # Arrange
    store = _store([{"id": "x", "title": "X", "status": "blocked"}])
    complete_task(store, "x", by="tester")
    # Act
    delete_task(store, "x")
    # Assert
    assert _check_terminal_state_honest(store)["ok"] is True


def test_the_delete_undo_payload_still_carries_the_stamp(postgres_dsn):
    # Arrange — the Undo must be lossless: the snapshot predates the clear.
    store = _store([{"id": "x", "title": "X", "status": "blocked"}])
    complete_task(store, "x", by="tester")
    # Act
    removed = delete_task(store, "x")["removed"]
    # Assert
    assert removed["_log_meta"].get("completed_at")


def test_undo_of_a_deleted_done_card_restores_its_stamp(postgres_dsn):
    # Arrange
    store = _store([{"id": "x", "title": "X", "status": "blocked"}])
    complete_task(store, "x", by="tester")
    removed = delete_task(store, "x")["removed"]
    # Act
    restore_task(store, removed)
    # Assert
    row = _row(store, "x")
    assert row["status"] == "done" and row["_log_meta"].get("completed_at")
