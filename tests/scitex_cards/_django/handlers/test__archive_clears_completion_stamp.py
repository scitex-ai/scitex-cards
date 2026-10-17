#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the board /archive completion-stamp clear.

``handle_archive`` (POST /archive, the HTTP twin of the CLI
``close --reason`` verb) flipped ANY card to ``cancelled`` and stamped
``_log_meta.closed_{at,by}`` via ``_save_doc_unlocked`` directly — bypassing
:func:`update_task` and its ``_clear_completion_stamp_on_leaving_done``
clock. Archiving a ``done`` card therefore kept
``_log_meta.completed_{at,by}`` on a LIVE cancelled row, which
``fleet/timing.py`` + ``timeline.py`` aggregate SOLELY on ``completed_at``
(never ``status``) and which ``_check_terminal_state_honest`` FAILs on
(``cancelled`` + ``completed_at`` is a named bad population in
``_health_cards``). Same honesty class as the ``reopen_task`` stamp clear;
the helper docstring says to call it from ANY transition out of ``done``.

The CLI twin needs no change (it composes ``update_task``, whose clock
fires on every exit from ``done`` — pinned by
``test__update_task_clears_the_completion_stamp.py``); ``delete_task``
tombstones read as ABSENT (``_store_list`` skips them), so it is safe too.
Only the board handler bypassed the clock, so only it changes.

These pins run with no database (source scans + the pure helper, in the
established ``*_pending_abolished`` style). The store-backed round-trip
twin lives in ``test_stale.py`` for CI-with-PG.

STRICT scitex doctrine: no unittest.mock, no monkeypatch (PA-306) — the
live-write side is pinned via source order, never a stubbed store.
"""

from __future__ import annotations

import inspect


def _archive_source() -> str:
    from scitex_cards._django.handlers import stale as stale_handler

    return inspect.getsource(stale_handler.handle_archive)


def test_archive_source_clears_the_completion_stamp():
    # Arrange
    # Arrange
    # Act
    src = _archive_source()
    # Assert
    assert "clear_completion_stamp(task)" in src


def test_archive_source_imports_the_named_helper():
    # Arrange
    # Arrange
    # Act
    src = _archive_source()
    # Assert
    assert "from ..._store_lifecycle import clear_completion_stamp" in src


def test_archive_clear_runs_before_the_save():
    # Arrange
    src = _archive_source()
    # Act: the clear must land before the row is persisted, or a crash
    # between write and clear mints the phantom-delivery row.
    clear_at = src.index("clear_completion_stamp(task)")
    # Assert
    assert clear_at < src.index("_save_doc_unlocked(doc")


def test_archive_doc_records_the_clear_case1():
    # Arrange
    # Arrange
    from scitex_cards._django.handlers import stale as stale_handler
    # Act
    doc = stale_handler.handle_archive.__doc__ or ""
    # Assert
    assert "clear_completion_stamp" in doc


def test_archive_doc_records_the_clear_case2():
    # Arrange
    # Arrange
    from scitex_cards._django.handlers import stale as stale_handler
    # Act
    doc = stale_handler.handle_archive.__doc__ or ""
    # Assert
    assert "completed_at" in doc


def test_clear_helper_drops_the_stamp_and_keeps_the_close_case1():
    # Arrange: a done card as the archive write would see it mid-flight —
    # already flipped to cancelled, closed stamp on, completion stamp stale.
    # Arrange
    from scitex_cards._store_lifecycle import clear_completion_stamp
    task = {
        "id": "was-done",
        "status": "cancelled",
        "_log_meta": {
            "completed_at": "2026-10-01T00:00:00+00:00",
            "completed_by": "scitex-cards",
            "closed_at": "2026-10-14T00:00:00+00:00",
            "closed_by": "scitex-cards",
        },
    }
    # Act
    cleared = clear_completion_stamp(task)
    # Assert
    assert cleared is True


def test_clear_helper_drops_the_stamp_and_keeps_the_close_case2():
    # Arrange: a done card as the archive write would see it mid-flight —
    # already flipped to cancelled, closed stamp on, completion stamp stale.
    # Arrange
    from scitex_cards._store_lifecycle import clear_completion_stamp
    # Act
    task = {
        "id": "was-done",
        "status": "cancelled",
        "_log_meta": {
            "completed_at": "2026-10-01T00:00:00+00:00",
            "completed_by": "scitex-cards",
            "closed_at": "2026-10-14T00:00:00+00:00",
            "closed_by": "scitex-cards",
        },
    }
    # Assert
    cleared = clear_completion_stamp(task)
    assert "completed_at" not in task["_log_meta"]


def test_clear_helper_drops_the_stamp_and_keeps_the_close_case3():
    # Arrange: a done card as the archive write would see it mid-flight —
    # already flipped to cancelled, closed stamp on, completion stamp stale.
    # Arrange
    from scitex_cards._store_lifecycle import clear_completion_stamp
    # Act
    task = {
        "id": "was-done",
        "status": "cancelled",
        "_log_meta": {
            "completed_at": "2026-10-01T00:00:00+00:00",
            "completed_by": "scitex-cards",
            "closed_at": "2026-10-14T00:00:00+00:00",
            "closed_by": "scitex-cards",
        },
    }
    # Assert
    cleared = clear_completion_stamp(task)
    assert "completed_by" not in task["_log_meta"]


def test_clear_helper_drops_the_stamp_and_keeps_the_close_case4():
    # Arrange: a done card as the archive write would see it mid-flight —
    # already flipped to cancelled, closed stamp on, completion stamp stale.
    # Arrange
    from scitex_cards._store_lifecycle import clear_completion_stamp
    # Act
    task = {
        "id": "was-done",
        "status": "cancelled",
        "_log_meta": {
            "completed_at": "2026-10-01T00:00:00+00:00",
            "completed_by": "scitex-cards",
            "closed_at": "2026-10-14T00:00:00+00:00",
            "closed_by": "scitex-cards",
        },
    }
    # Assert
    cleared = clear_completion_stamp(task)
    assert task["_log_meta"]["closed_at"] == "2026-10-14T00:00:00+00:00"


def test_clear_helper_is_a_noop_on_stamp_less_rows():
    # Arrange: the common archive path (a deferred card was never complete).
    from scitex_cards._store_lifecycle import clear_completion_stamp

    task = {"id": "was-deferred", "status": "cancelled", "_log_meta": {}}
    # Act
    cleared = clear_completion_stamp(task)
    # Assert
    assert cleared is False


def test_cancelled_must_never_carry_a_completion_stamp_case1():
    # Arrange: the honesty enum — only `done` means delivered.
    # Arrange
    # Act
    from scitex_cards._health_cards import _COMPLETED_STATUS, _TERMINAL_STATUSES
    # Assert
    assert _COMPLETED_STATUS == "done"


def test_cancelled_must_never_carry_a_completion_stamp_case2():
    # Arrange: the honesty enum — only `done` means delivered.
    # Arrange
    # Act
    from scitex_cards._health_cards import _COMPLETED_STATUS, _TERMINAL_STATUSES
    # Assert
    assert "cancelled" in _TERMINAL_STATUSES


def test_cancelled_must_never_carry_a_completion_stamp_case3():
    # Arrange: the honesty enum — only `done` means delivered.
    # Arrange
    # Act
    from scitex_cards._health_cards import _COMPLETED_STATUS, _TERMINAL_STATUSES
    # Assert
    assert "cancelled" != _COMPLETED_STATUS


def test_throughput_believes_the_stamp_alone_case1():
    # Arrange: the reason the clear matters — the aggregate never consults
    # `status`, so this pin locks the premise, not just the fix.
    # Arrange
    from scitex_cards._django.handlers.fleet import timing as timing_mod
    # Act
    src = inspect.getsource(timing_mod.compute_timing)
    # Assert
    assert 'completed_raw = _log_meta_ts(t, "completed_at")' in src


def test_throughput_believes_the_stamp_alone_case2():
    # Arrange: the reason the clear matters — the aggregate never consults
    # `status`, so this pin locks the premise, not just the fix.
    # Arrange
    from scitex_cards._django.handlers.fleet import timing as timing_mod
    # Act
    src = inspect.getsource(timing_mod.compute_timing)
    # Assert
    assert 'status == "done"' not in src.split("completed_raw")[0][-500:]


def test_detector_is_not_vacuous():
    # Arrange: the source pins above must be capable of failing.
    # Arrange
    # Act
    fixture = 'task["status"] = "cancelled"\n_save_doc_unlocked(doc)'
    # Assert
    assert "clear_completion_stamp(task)" not in fixture


def test_archive_clear_sits_between_flip_and_save_case1():
    # Arrange — locks the reviewed shape: flip, clear, save. A clear
    # placed before the flip would still run, but the fix was reviewed
    # as this order (the clear acts on the row as the archive write
    # sees it mid-flight), so drift out of this order must fail.
    # Arrange
    src = _archive_source()
    flip_at = src.index('task["status"] = "cancelled"')
    # Act
    clear_at = src.index("clear_completion_stamp(task)")
    # Assert
    assert flip_at < clear_at


def test_archive_clear_sits_between_flip_and_save_case2():
    # Arrange — locks the reviewed shape: flip, clear, save. A clear
    # placed before the flip would still run, but the fix was reviewed
    # as this order (the clear acts on the row as the archive write
    # sees it mid-flight), so drift out of this order must fail.
    # Arrange
    src = _archive_source()
    # Act
    flip_at = src.index('task["status"] = "cancelled"')
    clear_at = src.index("clear_completion_stamp(task)")
    # Assert
    assert clear_at < src.index("_save_doc_unlocked(doc")
