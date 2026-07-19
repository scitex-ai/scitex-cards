#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A card that is not `done` must not carry a completion stamp.

WHY THIS FILE EXISTS (2026-07-14): `reopen_task` flipped `status` from `done`
back to `blocked` but left `_log_meta.completed_{at,by}` in place. That is not a
cosmetic leak — `_django/handlers/fleet/timing.py` and `timeline.py` compute
throughput from `completed_at` ALONE and never consult `status`. So a reopened
card was counted as DELIVERED WORK by the throughput surfaces while
simultaneously nagging its owner as OPEN BACKLOG in the digest. One card, two
contradictory facts, depending on who read it.

Five such cards were found on the live board. The health check that exists to
catch exactly this (`_check_terminal_state_honest`) reported ok on all five,
because it knew about `closed_at` and had never been told about `completed_at`.
"""

import yaml

from scitex_cards._health import _CLOSURE_MARKERS, _check_terminal_state_honest
from scitex_cards._store_lifecycle import (
    COMPLETION_STAMP_KEYS,
    clear_completion_stamp,
    complete_task,
    reopen_task,
)


def _store(tmp_path, tasks):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "tasks.yaml"
    p.write_text(yaml.safe_dump({"tasks": tasks}, sort_keys=False))
    return p


def _meta(store, task_id):
    doc = yaml.safe_load(store.read_text())
    task = next(t for t in doc["tasks"] if t["id"] == task_id)
    return task, (task.get("_log_meta") or {})


def _one_blocked_card(tmp_path):
    return _store(tmp_path, [{"id": "a", "title": "A", "status": "blocked"}])


def test_complete_task_flips_the_card_to_done(tmp_path):
    # Arrange
    store = _one_blocked_card(tmp_path)
    # Act
    complete_task(store, "a", by="tester")
    # Assert
    task, _meta_map = _meta(store, "a")
    assert task["status"] == "done"


def test_complete_task_stamps_completed_at(tmp_path):
    # Arrange
    store = _one_blocked_card(tmp_path)
    # Act
    complete_task(store, "a", by="tester")
    # Assert
    _task, meta = _meta(store, "a")
    assert meta.get("completed_at"), "complete_task must stamp completed_at"


def test_reopen_task_restores_the_previous_status(tmp_path):
    # Arrange
    store = _one_blocked_card(tmp_path)
    complete_task(store, "a", by="tester")
    # Act
    reopen_task(store, "a", by="tester")
    # Assert
    task, _meta_map = _meta(store, "a")
    assert task["status"] == "blocked"


def test_complete_then_reopen_leaves_no_completion_stamp(tmp_path):
    """The regression itself: reopen must un-complete, not just un-status."""
    # Arrange
    store = _one_blocked_card(tmp_path)
    complete_task(store, "a", by="tester")
    # Act
    reopen_task(store, "a", by="tester")
    # Assert
    _task, meta = _meta(store, "a")
    for key in COMPLETION_STAMP_KEYS:
        assert key not in meta, (
            f"reopened card still carries _log_meta.{key} — the throughput "
            "surfaces aggregate on completed_at alone and will count this "
            "open card as delivered work"
        )


def test_reopened_card_is_not_a_zombie_to_the_health_check(tmp_path):
    """End-to-end: the guard must agree the reopened card is honest."""
    # Arrange
    store = _one_blocked_card(tmp_path)
    complete_task(store, "a", by="tester")
    reopen_task(store, "a", by="tester")
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is True


def _completed_at_zombie_store(tmp_path):
    return _store(
        tmp_path,
        [
            {
                "id": "zombie",
                "title": "counted as shipped, still nagging",
                "status": "deferred",
                "_log_meta": {"completed_at": "2026-07-01T00:00:00Z"},
            }
        ],
    )


def test_health_check_catches_a_completed_at_zombie(tmp_path):
    """The blind spot, pinned: open status + completed_at MUST be caught.

    Before 2026-07-14 this returned ok — which is how five of them survived.
    """
    # Arrange
    store = _completed_at_zombie_store(tmp_path)
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is False


def test_completed_at_zombie_detail_names_the_card(tmp_path):
    """A verdict nobody can act on is not a verdict — name the offender."""
    # Arrange
    store = _completed_at_zombie_store(tmp_path)
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert "zombie" in result["detail"]


def test_health_check_still_catches_a_closed_at_zombie(tmp_path):
    """Widening the check must not have dropped the marker it already had."""
    # Arrange
    store = _store(
        tmp_path,
        [
            {
                "id": "zombie",
                "title": "closed but open",
                "status": "in_progress",
                "_log_meta": {"closed_at": "2026-07-01T00:00:00Z"},
            }
        ],
    )
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is False


def test_every_closure_marker_is_actually_checked(tmp_path):
    """Add a closure marker without teaching the guard, and this FAILS.

    The original bug was an ENUMERATION that silently claimed to be complete.
    This test makes the enumeration load-bearing: each marker in
    _CLOSURE_MARKERS must, on its own, be enough to convict an open card.
    """
    # Arrange
    markers = list(_CLOSURE_MARKERS)
    # Act
    results = {
        marker: _check_terminal_state_honest(
            _store(
                tmp_path / marker,
                [
                    {
                        "id": "z",
                        "title": "z",
                        "status": "deferred",
                        "_log_meta": {marker: "2026-07-01T00:00:00Z"},
                    }
                ],
            )
        )
        for marker in markers
    }
    # Assert
    for marker, result in results.items():
        assert result["ok"] is False, (
            f"_log_meta.{marker} is listed in _CLOSURE_MARKERS but does not "
            "trip the zombie check — the guard's enumeration is a lie"
        )


def _cancelled_but_stamped_store(tmp_path):
    """A TERMINAL card that still lies about throughput.

    `cancelled` is not an open status, so the zombie rule does not see it — it
    does not nag anyone. But fleet/timing.py and timeline.py aggregate on
    `completed_at` ALONE, so a cancelled card carrying the stamp is reported as
    DELIVERED WORK. That is exactly the corruption that marking a killed card
    `done` was supposed to avoid, arrived at by a different road.

    (Real card, 2026-07-14: `sac-keystone` was briefly mislabelled `done`, then
    correctly moved to `cancelled` — and kept its completion stamp. The status
    was fixed; the stamp was not.)
    """
    return _store(
        tmp_path,
        [
            {
                "id": "killed-but-counted",
                "title": "never built, still counted as shipped",
                "status": "cancelled",
                "_log_meta": {
                    "completed_at": "2026-07-14T10:23:38Z",
                    "completed_by": "somebody",
                },
            }
        ],
    )


def test_cancelled_card_carrying_completed_at_is_caught(tmp_path):
    # Arrange
    store = _cancelled_but_stamped_store(tmp_path)
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is False


def test_cancelled_zombie_detail_names_the_card(tmp_path):
    # Arrange
    store = _cancelled_but_stamped_store(tmp_path)
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert "killed-but-counted" in result["detail"]


def test_cancelled_zombie_detail_explains_the_throughput_lie(tmp_path):
    # Arrange
    store = _cancelled_but_stamped_store(tmp_path)
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert "DELIVERED WORK" in result["detail"]


def test_a_genuinely_done_card_is_not_flagged(tmp_path):
    """The stamp is CORRECT on a done card — no false positives."""
    # Arrange
    store = _store(
        tmp_path,
        [
            {
                "id": "shipped",
                "title": "actually shipped",
                "status": "done",
                "_log_meta": {
                    "completed_at": "2026-07-14T10:00:00Z",
                    "completed_by": "me",
                },
            }
        ],
    )
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is True


def test_clear_completion_stamp_never_invents_a_meta_block():
    """Never reports a removal on a task that has no _log_meta at all."""
    # Arrange
    task = {"id": "x"}
    # Act
    removed = clear_completion_stamp(task)
    # Assert
    assert removed is False


def test_clear_completion_stamp_reports_nothing_on_an_empty_meta():
    """An empty _log_meta holds no stamp, so nothing was cleared."""
    # Arrange
    task = {"id": "x", "_log_meta": {}}
    # Act
    removed = clear_completion_stamp(task)
    # Assert
    assert removed is False


def test_clear_completion_stamp_reports_removing_a_real_stamp():
    # Arrange
    task = {"id": "x", "_log_meta": {"completed_at": "t", "completed_by": "me"}}
    # Act
    removed = clear_completion_stamp(task)
    # Assert
    assert removed is True


def test_clear_completion_stamp_drops_an_emptied_meta_block():
    # Arrange
    task = {"id": "x", "_log_meta": {"completed_at": "t", "completed_by": "me"}}
    # Act
    clear_completion_stamp(task)
    # Assert
    assert "_log_meta" not in task, "an emptied _log_meta should not linger"


def test_clear_completion_stamp_is_idempotent():
    # Arrange
    task = {"id": "x", "_log_meta": {"completed_at": "t", "completed_by": "me"}}
    clear_completion_stamp(task)
    # Act
    second = clear_completion_stamp(task)
    # Assert
    assert second is False


def test_clear_completion_stamp_reports_a_removal_on_mixed_meta():
    """Only the completion keys go — started_at et al. are someone else's."""
    # Arrange
    task = {"id": "x", "_log_meta": {"completed_at": "t", "started_at": "s"}}
    # Act
    removed = clear_completion_stamp(task)
    # Assert
    assert removed is True


def test_clear_completion_stamp_preserves_unrelated_meta():
    # Arrange
    task = {"id": "x", "_log_meta": {"completed_at": "t", "started_at": "s"}}
    # Act
    clear_completion_stamp(task)
    # Assert
    assert task["_log_meta"] == {"started_at": "s"}
