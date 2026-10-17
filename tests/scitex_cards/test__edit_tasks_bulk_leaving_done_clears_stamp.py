#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the bulk-edit completion-stamp obligation.

``edit_tasks`` (``_store_write.py``) is the sanctioned bulk-edit primitive:
it yields the raw ``tasks`` list with the store lock held. Assigning
``status`` on those dicts bypasses ``update_task`` and its
``_clear_completion_stamp_on_leaving_done`` clock, so a bulk move of a
``done``+stamped card (``complete_task`` stamps
``_log_meta.completed_{at,by}``) to a live status keeps ``completed_at``
on a non-``done`` row — the phantom-delivery row ``fleet/timing.py`` +
``timeline.py`` aggregate on ``completed_at`` alone (never ``status``).
Same honesty class as the reopen / restore / archive / upsert stamp
clears; the undo-family card recorded bulk edit as out-of-bound, and this
file closes it.

The fix is doc-only on purpose: the primitive cannot know intent (a bulk
RESTORE re-inserting snapshots AS ``done`` keeps genuine stamps), so the
docstring names the obligation — capture ``prior_status`` BEFORE the flip
and call the named clock per row — instead of auto-clearing on exit and
lying about restored snapshots.

These pins NEVER touch a store: past the status gate every path takes the
store lock, and a bogus file store path does NOT isolate (a prior probe
mirrored to the live board). Docstring-order pins + the pure clock on
in-memory dicts only, in the established ``*_pending_abolished`` style.
The store-backed round-trip twin belongs in the store-fixture suite for
CI-with-PG.
"""

from __future__ import annotations

from scitex_cards import _store_write as write_mod
from scitex_cards._store_clocks import _clear_completion_stamp_on_leaving_done
from scitex_cards._store_lifecycle import COMPLETED_STATUS


def _stamped(status: str) -> dict:
    # Arrange helper, not a test: a done+stamped row shaped like the
    # `complete_task` output a bulk loop replays.
    return {
        "id": "x",
        "status": status,
        "title": "t",
        "_log_meta": {"completed_at": "2026-01-01T00:00:00Z", "completed_by": "a"},
    }


def test_edit_tasks_doc_names_the_stamp_clock():
    # Arrange
    # Arrange
    # Act
    doc = write_mod.edit_tasks.__doc__ or ""
    # Assert
    assert "_clear_completion_stamp_on_leaving_done" in doc


def test_edit_tasks_doc_names_prior_status_capture_case1():
    # Arrange
    # Arrange
    # Act
    doc = write_mod.edit_tasks.__doc__ or ""
    # Assert
    assert "prior_status" in doc


def test_edit_tasks_doc_names_prior_status_capture_case2():
    # Arrange
    # Arrange
    # Act
    doc = write_mod.edit_tasks.__doc__ or ""
    # Assert
    assert "BEFORE the flip" in doc


def test_edit_tasks_doc_names_the_phantom_readers_case1():
    # Arrange
    # Arrange
    # Act
    doc = write_mod.edit_tasks.__doc__ or ""
    # Assert
    assert "timing.py" in doc


def test_edit_tasks_doc_names_the_phantom_readers_case2():
    # Arrange
    # Arrange
    # Act
    doc = write_mod.edit_tasks.__doc__ or ""
    # Assert
    assert "timeline.py" in doc


def test_edit_tasks_doc_names_the_phantom_readers_case3():
    # Arrange
    # Arrange
    # Act
    doc = write_mod.edit_tasks.__doc__ or ""
    # Assert
    assert "completed_at" in doc


def test_edit_tasks_doc_distinguishes_restore_as_done_case1():
    # Arrange
    # Arrange
    # Act
    doc = write_mod.edit_tasks.__doc__ or ""
    # Assert
    assert "AS ``done`` keeps its" in doc


def test_edit_tasks_doc_distinguishes_restore_as_done_case2():
    # Arrange
    # Arrange
    # Act
    doc = write_mod.edit_tasks.__doc__ or ""
    # Assert
    assert "only LEAVING ``done`` clears" in doc


def test_bulk_flip_without_clock_keeps_stamp():
    # Arrange: the exact shape a bulk loop took before the obligation —
    # raw dict assignment with no clock call.
    task = _stamped("done")
    # Act: flip status the way `edit_tasks` rows invite.
    task["status"] = "deferred"
    # Assert: the stamp survives — the dishonest row the doc closes.
    # (This pin documents the hole, not the fix: if raw assignment ever
    # started clearing, the primitive would be lying about restores.)
    assert task["_log_meta"]["completed_at"] == "2026-01-01T00:00:00Z"


def test_clock_clears_bulk_row_leaving_done_case1():
    # Arrange
    # Arrange
    task = _stamped("done")
    prior_status = task["status"]
    task["status"] = "deferred"
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, prior_status)
    # Assert
    assert cleared is True


def test_clock_clears_bulk_row_leaving_done_case2():
    # Arrange
    task = _stamped("done")
    prior_status = task["status"]
    # Act
    task["status"] = "deferred"
    # Assert
    cleared = _clear_completion_stamp_on_leaving_done(task, prior_status)
    assert "_log_meta" not in task


def test_clock_keeps_stamp_when_row_stays_done_case1():
    # Arrange: a passing edit on a done card, not an exit.
    # Arrange
    task = _stamped("done")
    prior_status = task["status"]
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, prior_status)
    # Assert
    assert cleared is False


def test_clock_keeps_stamp_when_row_stays_done_case2():
    # Arrange: a passing edit on a done card, not an exit.
    # Arrange
    task = _stamped("done")
    prior_status = task["status"]
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, prior_status)
    # Assert
    assert task["_log_meta"]["completed_at"] == "2026-01-01T00:00:00Z"


def test_clock_noop_when_row_was_never_done_case1():
    # Arrange
    # Arrange
    task = {"id": "y", "status": "in_progress", "title": "t"}
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, "in_progress")
    # Assert
    assert cleared is False


def test_clock_noop_when_row_was_never_done_case2():
    # Arrange
    # Arrange
    # Act
    task = {"id": "y", "status": "in_progress", "title": "t"}
    # Assert
    cleared = _clear_completion_stamp_on_leaving_done(task, "in_progress")
    assert "_log_meta" not in task


def test_completed_status_anchor_is_done():
    # Arrange + Act + Assert: non-vacuity anchor — the clock keys on this
    # constant, so the leaving-done pins above are testing the live
    # transition and not a renamed status.
    # Arrange
    # Act
    # Assert
    assert COMPLETED_STATUS == "done"


def test_store_write_has_no_model_harness_coupling_case1():
    # Arrange
    # Arrange
    import inspect
    # Act
    src = inspect.getsource(write_mod)
    # Assert
    assert "from sac" not in src


def test_store_write_has_no_model_harness_coupling_case2():
    # Arrange
    # Arrange
    import inspect
    # Act
    src = inspect.getsource(write_mod)
    # Assert
    assert "import sac" not in src


def test_store_write_has_no_model_harness_coupling_case3():
    # Arrange
    # Arrange
    import inspect
    # Act
    src = inspect.getsource(write_mod)
    # Assert
    assert "harness" not in src
