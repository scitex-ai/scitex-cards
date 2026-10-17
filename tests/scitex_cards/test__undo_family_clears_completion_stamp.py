#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the Undo-family completion-stamp holes.

Board ``/reopen`` (``handlers/reopen.py::handle_reopen``) flipped a card's
status to the caller-supplied ``prior_status`` with NO stamp clear; board
``/restore`` (``handlers/undo.py::handle_restore``) and the ``restore_task``
verb (``_store_lifecycle.py``) re-inserted the caller snapshot wholesale,
stamp included. Any ``done``+stamped card (``complete_task`` stamps
``_log_meta.completed_{at,by}``) undone or restored to a live status kept
``completed_at`` on a non-``done`` row — the phantom-delivery row
``fleet/timing.py`` + ``timeline.py`` aggregate on ``completed_at`` alone
(never ``status``), so it counts as delivered work forever WHILE nagging
its owner as backlog. Same honesty class as the ``reopen_task``,
``update_task`` (named clock), upsert, board ``/archive`` and stale-sweep
stamp clears.

The fix calls the named clock / helper — never two inline pops — at each
transition: ``handle_reopen`` runs
``_clear_completion_stamp_on_leaving_done(task, pre_status)`` after the
status flip; ``handle_restore`` + ``restore_task`` clear the snapshot stamp
whenever the card re-enters in any status but ``done`` (restored AS ``done``
keeps its genuine stamp — it really was completed).

These pins NEVER drive the handlers end-to-end: past the status gate the
paths take the store lock, and a bogus file store path does NOT isolate
(a prior probe mirrored to the live board). Source-order pins + the pure
helpers only, in the established ``*_pending_abolished`` style. The
store-backed round-trip twins belong in the store-fixture suite for
CI-with-PG.
"""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip("django")

from scitex_cards._django.handlers import reopen as reopen_mod  # noqa: E402
from scitex_cards._django.handlers import undo as undo_mod  # noqa: E402


def _stamped(status: str) -> dict:
    # Arrange helper, not a test: a done+stamped snapshot shaped like the
    # `complete_task` output the Undo payloads replay.
    return {
        "id": "x",
        "status": status,
        "title": "t",
        "_log_meta": {"completed_at": "2026-01-01T00:00:00Z", "completed_by": "a"},
    }


def test_reopen_source_calls_the_named_clock():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(reopen_mod.handle_reopen)
    # Assert
    assert "_clear_completion_stamp_on_leaving_done(task, pre_status)" in src


def test_reopen_source_imports_the_clock_from_store_clocks():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(reopen_mod.handle_reopen)
    # Assert
    assert "from ..._store_clocks import _clear_completion_stamp_on_leaving_done" in src


def test_reopen_clear_runs_after_the_status_assignment_case1():
    # Arrange
    # Arrange
    src = inspect.getsource(reopen_mod.handle_reopen)
    # Act
    clear_at = src.index("_clear_completion_stamp_on_leaving_done(task, pre_status)")
    # Assert
    assert src.index('pre_status = task.get("status")') < clear_at


def test_reopen_clear_runs_after_the_status_assignment_case2():
    # Arrange
    # Arrange
    src = inspect.getsource(reopen_mod.handle_reopen)
    # Act
    clear_at = src.index("_clear_completion_stamp_on_leaving_done(task, pre_status)")
    # Assert
    assert clear_at > src.index('task["status"] = new_status')


def test_reopen_clear_runs_before_the_save():
    # Arrange
    src = inspect.getsource(reopen_mod.handle_reopen)
    # Act: the clear must land before the row is persisted, or a crash
    # between write and clear mints the phantom-delivery row.
    clear_at = src.index("_clear_completion_stamp_on_leaving_done(task, pre_status)")
    # Assert
    assert clear_at < src.index("_save_doc_unlocked(")


def test_reopen_doc_records_the_clear_case1():
    # Arrange
    # Arrange
    # Act
    doc = reopen_mod.handle_reopen.__doc__ or ""
    # Assert
    assert "_clear_completion_stamp_on_leaving_done" in doc


def test_reopen_doc_records_the_clear_case2():
    # Arrange
    # Arrange
    # Act
    doc = reopen_mod.handle_reopen.__doc__ or ""
    # Assert
    assert "completed_at" in doc


def test_restore_handler_source_clears_the_snapshot_stamp():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(undo_mod.handle_restore)
    # Assert
    assert "clear_completion_stamp(task)" in src


def test_restore_handler_clear_is_gated_on_not_done_case1():
    # Arrange
    # Arrange
    src = inspect.getsource(undo_mod.handle_restore)
    # Act
    gate_at = src.index('if task.get("status") != "done":')
    # Assert
    assert gate_at < src.index("clear_completion_stamp(task)")


def test_restore_handler_clear_is_gated_on_not_done_case2():
    # Arrange
    # Arrange
    src = inspect.getsource(undo_mod.handle_restore)
    # Act
    gate_at = src.index('if task.get("status") != "done":')
    # Assert
    assert gate_at < src.index("_save_doc_unlocked(")


def test_restore_handler_doc_records_the_clear():
    # Arrange
    # Arrange
    # Act
    doc = undo_mod.handle_restore.__doc__ or ""
    # Assert
    assert "completed_at" in doc


def test_restore_verb_source_clears_the_restored_copy_case1():
    # Arrange
    # Arrange
    from scitex_cards import _store_lifecycle
    # Act
    src = inspect.getsource(_store_lifecycle.restore_task)
    # Assert
    gate_at = src.index("if restored.get")
    assert "clear_completion_stamp(restored)" in src


def test_restore_verb_source_clears_the_restored_copy_case2():
    # Arrange
    # Arrange
    from scitex_cards import _store_lifecycle
    src = inspect.getsource(_store_lifecycle.restore_task)
    # Act
    gate_at = src.index("if restored.get")
    # Assert
    assert gate_at < src.index("clear_completion_stamp(restored)")


def test_restore_verb_doc_records_the_clear():
    # Arrange
    # Arrange
    from scitex_cards import _store_lifecycle
    # Act
    doc = _store_lifecycle.restore_task.__doc__ or ""
    # Assert
    assert "completed_at" in doc


def test_restore_verb_unshares_meta_before_clearing():
    # Arrange: `restored` is a SHALLOW copy, so its nested meta dict IS the
    # caller's — the in-place clear must first unshare it, or the caller's
    # Undo payload loses its stamp (the verb promises the caller's dict is
    # never mutated and stays replayable).
    # Arrange
    from scitex_cards import _store_lifecycle
    src = inspect.getsource(_store_lifecycle.restore_task)
    # Act
    unshare_at = src.index('restored["_log_meta"] = dict(_meta)')
    # Assert
    assert unshare_at < src.index("clear_completion_stamp(restored)")


def test_clock_drops_the_stamp_on_done_to_blocked_case1():
    # Arrange: the exact reopen shape — done+stamped card, prior done.
    # Arrange
    from scitex_cards._store_clocks import _clear_completion_stamp_on_leaving_done
    task = _stamped("blocked")
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, "done")
    # Assert
    assert cleared is True


def test_clock_drops_the_stamp_on_done_to_blocked_case2():
    # Arrange: the exact reopen shape — done+stamped card, prior done.
    # Arrange
    from scitex_cards._store_clocks import _clear_completion_stamp_on_leaving_done
    # Act
    task = _stamped("blocked")
    # Assert
    cleared = _clear_completion_stamp_on_leaving_done(task, "done")
    assert (task.get("_log_meta") or {}).get("completed_at") is None


def test_helper_drops_the_stamp_on_a_restored_live_copy_case1():
    # Arrange: the exact restore shape — done+stamped snapshot re-entering
    # as deferred on a copy (caller's Undo payload untouched). The verb
    # unshares `_log_meta` before the in-place clear (shallow copy shares
    # the nested dict); this test mirrors that order because the helper
    # itself is deliberately in-place.
    # Arrange
    from scitex_cards._store_lifecycle import clear_completion_stamp
    snapshot = _stamped("done")
    restored = dict(snapshot)
    restored["status"] = "deferred"
    _meta = restored.get("_log_meta")
    if isinstance(_meta, dict):
        restored["_log_meta"] = dict(_meta)
    # Act
    cleared = clear_completion_stamp(restored)
    # Assert
    assert cleared is True


def test_helper_drops_the_stamp_on_a_restored_live_copy_case2():
    # Arrange: the exact restore shape — done+stamped snapshot re-entering
    # as deferred on a copy (caller's Undo payload untouched). The verb
    # unshares `_log_meta` before the in-place clear (shallow copy shares
    # the nested dict); this test mirrors that order because the helper
    # itself is deliberately in-place.
    # Arrange
    from scitex_cards._store_lifecycle import clear_completion_stamp
    snapshot = _stamped("done")
    restored = dict(snapshot)
    restored["status"] = "deferred"
    _meta = restored.get("_log_meta")
    # Act
    if isinstance(_meta, dict):
        restored["_log_meta"] = dict(_meta)
    # Assert
    cleared = clear_completion_stamp(restored)
    assert (restored.get("_log_meta") or {}).get("completed_at") is None


def test_helper_drops_the_stamp_on_a_restored_live_copy_case3():
    # Arrange: the exact restore shape — done+stamped snapshot re-entering
    # as deferred on a copy (caller's Undo payload untouched). The verb
    # unshares `_log_meta` before the in-place clear (shallow copy shares
    # the nested dict); this test mirrors that order because the helper
    # itself is deliberately in-place.
    # Arrange
    from scitex_cards._store_lifecycle import clear_completion_stamp
    snapshot = _stamped("done")
    restored = dict(snapshot)
    restored["status"] = "deferred"
    _meta = restored.get("_log_meta")
    # Act
    if isinstance(_meta, dict):
        restored["_log_meta"] = dict(_meta)
    # Assert
    cleared = clear_completion_stamp(restored)
    assert snapshot["_log_meta"]["completed_at"] == "2026-01-01T00:00:00Z"


def test_project_board_update_inherits_the_stamp_clock_case1():
    # Arrange: `project_board.update_card` delegates to `update_task` via
    # `_default_update`, so a board edit leaving `done` inherits the named
    # clock without calling it itself. A future refactor swapping the
    # default writer for inline writes would silently lose the clear with
    # every pin above still green — this test locks the delegation chain.
    # Arrange
    from scitex_cards import _store_mutate as mutate_mod
    from scitex_cards._django.handlers import project_board as pb_mod
    # Act
    card_src = inspect.getsource(pb_mod.update_card)
    # Assert
    default_src = inspect.getsource(pb_mod._default_update)
    mutate_src = inspect.getsource(mutate_mod.update_task)
    assert "writer = update or _default_update" in card_src


def test_project_board_update_inherits_the_stamp_clock_case2():
    # Arrange: `project_board.update_card` delegates to `update_task` via
    # `_default_update`, so a board edit leaving `done` inherits the named
    # clock without calling it itself. A future refactor swapping the
    # default writer for inline writes would silently lose the clear with
    # every pin above still green — this test locks the delegation chain.
    # Arrange
    from scitex_cards import _store_mutate as mutate_mod
    from scitex_cards._django.handlers import project_board as pb_mod
    # Act
    card_src = inspect.getsource(pb_mod.update_card)
    default_src = inspect.getsource(pb_mod._default_update)
    # Assert
    mutate_src = inspect.getsource(mutate_mod.update_task)
    assert "return update_task(**fields)" in default_src


def test_project_board_update_inherits_the_stamp_clock_case3():
    # Arrange: `project_board.update_card` delegates to `update_task` via
    # `_default_update`, so a board edit leaving `done` inherits the named
    # clock without calling it itself. A future refactor swapping the
    # default writer for inline writes would silently lose the clear with
    # every pin above still green — this test locks the delegation chain.
    # Arrange
    from scitex_cards._django.handlers import project_board as pb_mod
    from scitex_cards import _store_mutate as mutate_mod
    # Act
    card_src = inspect.getsource(pb_mod.update_card)
    default_src = inspect.getsource(pb_mod._default_update)
    mutate_src = inspect.getsource(mutate_mod.update_task)
    # Assert
    assert "_clear_completion_stamp_on_leaving_done(task, prior_status)" in mutate_src
