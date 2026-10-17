#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the upsert completion-stamp clear on leaving ``done``.

``upsert_task`` (``src/scitex_cards/_store_upsert.py``) reopens a card in ANY
state — including ``done`` — to the caller-requested status, writing via
``_save_doc_unlocked`` directly and bypassing ``update_task`` and its
``_clear_completion_stamp_on_leaving_done`` clock. Reopening a ``done``
card therefore kept ``_log_meta.completed_{at,by}`` on a LIVE open row,
which ``fleet/timing.py`` + ``timeline.py`` aggregate SOLELY on
``completed_at`` (never ``status``) and which ``_check_terminal_state_honest``
FAILs on as a phantom-delivery row. Same honesty class as the ``reopen_task``
stamp clear and the board ``/archive`` stamp clear; the helper docstring says
to call it from ANY transition out of ``done``.

The fix calls the named clock — not two inline pops — after the status
assignment and before the save, with the pre-assignment ``prior_status``.

These pins run with no database (source scans + the pure helper, in the
established ``*_pending_abolished`` style). The store-backed round-trip twin
lives in ``test__upsert_task_reopens_a_closed_card.py`` for CI-with-PG.
"""

from __future__ import annotations

import inspect


def _upsert_source() -> str:
    from scitex_cards._store_upsert import upsert_task

    return inspect.getsource(upsert_task)


def test_upsert_source_clears_the_completion_stamp():
    # Arrange
    # Arrange
    # Act
    src = _upsert_source()
    # Assert
    assert "_clear_completion_stamp_on_leaving_done(task, prior_status)" in src


def test_upsert_source_imports_the_named_clock():
    # Arrange
    # Arrange
    # Act
    src = _upsert_source()
    # Assert
    assert "from ._store_clocks import _clear_completion_stamp_on_leaving_done" in src


def test_upsert_clear_runs_after_the_status_assignment():
    # Arrange
    src = _upsert_source()
    # Act: the clear must see the NEW status, so it lands after the assignment.
    clear_at = src.index("_clear_completion_stamp_on_leaving_done(task, prior_status)")
    # Assert
    assert clear_at > src.index('task["status"] = status')


def test_upsert_clear_runs_before_the_save():
    # Arrange
    src = _upsert_source()
    # Act: the clear must land before the row is persisted, or a crash
    # between write and clear mints the phantom-delivery row.
    clear_at = src.index("_clear_completion_stamp_on_leaving_done(task, prior_status)")
    # Assert
    assert clear_at < src.index("_save_doc_unlocked(doc")


def test_upsert_captures_the_prior_status_first():
    # Arrange
    src = _upsert_source()
    # Act: the clock keys on the transition, so the prior status must be
    # captured before the assignment overwrites it.
    prior_at = src.index("prior_status = task.get(")
    # Assert
    assert prior_at < src.index('task["status"] = status')


def test_upsert_doc_records_the_clear():
    # Arrange
    # Arrange
    from scitex_cards._store_upsert import upsert_task
    # Act
    doc = upsert_task.__doc__ or ""
    # Assert
    assert "_clear_completion_stamp_on_leaving_done" in doc


def test_upsert_doc_names_the_stamp():
    # Arrange
    # Arrange
    from scitex_cards._store_upsert import upsert_task
    # Act
    doc = upsert_task.__doc__ or ""
    # Assert
    assert "completed_at" in doc


def test_clock_drops_the_stamp_on_done_to_in_progress():
    # Arrange
    from scitex_cards._store_clocks import _clear_completion_stamp_on_leaving_done

    task = {"id": "x", "status": "in_progress", "_log_meta": {"completed_at": "t", "completed_by": "a"}}
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, "done")
    # Assert
    assert cleared is True


def test_clock_removes_both_stamp_keys():
    # Arrange
    from scitex_cards._store_clocks import _clear_completion_stamp_on_leaving_done

    task = {"id": "x", "status": "in_progress", "_log_meta": {"completed_at": "t", "completed_by": "a"}}
    # Act
    _clear_completion_stamp_on_leaving_done(task, "done")
    # Assert
    assert task.get("_log_meta") is None


def test_clock_keeps_the_stamp_when_still_done():
    # Arrange
    from scitex_cards._store_clocks import _clear_completion_stamp_on_leaving_done

    task = {"id": "x", "status": "done", "_log_meta": {"completed_at": "t"}}
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, "done")
    # Assert
    assert cleared is False


def test_clock_is_a_noop_when_never_done():
    # Arrange
    from scitex_cards._store_clocks import _clear_completion_stamp_on_leaving_done

    task = {"id": "x", "status": "in_progress", "_log_meta": {"completed_at": "t"}}
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, "deferred")
    # Assert
    assert cleared is False
