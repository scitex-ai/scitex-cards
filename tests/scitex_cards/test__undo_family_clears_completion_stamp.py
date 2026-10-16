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
    src = inspect.getsource(reopen_mod.handle_reopen)
    # Act + Assert: the verb calls the named clock, not two inline pops
    # (the helper's docstring is the contract the next transition reuses).
    assert "_clear_completion_stamp_on_leaving_done(task, pre_status)" in src


def test_reopen_source_imports_the_clock_from_store_clocks():
    # Arrange
    src = inspect.getsource(reopen_mod.handle_reopen)
    # Act + Assert: the same clock `update_task` uses, not a divergent copy.
    assert "from ..._store_clocks import _clear_completion_stamp_on_leaving_done" in src


def test_reopen_clear_runs_after_the_status_assignment():
    # Arrange
    src = inspect.getsource(reopen_mod.handle_reopen)
    # Act: the clock keys on the transition, so the prior status must be
    # captured before the assignment overwrites it, and the clear must see
    # the NEW status, so it lands after the assignment.
    clear_at = src.index("_clear_completion_stamp_on_leaving_done(task, pre_status)")
    # Assert
    assert src.index('pre_status = task.get("status")') < clear_at
    assert clear_at > src.index('task["status"] = new_status')


def test_reopen_clear_runs_before_the_save():
    # Arrange
    src = inspect.getsource(reopen_mod.handle_reopen)
    # Act: the clear must land before the row is persisted, or a crash
    # between write and clear mints the phantom-delivery row.
    clear_at = src.index("_clear_completion_stamp_on_leaving_done(task, pre_status)")
    # Assert
    assert clear_at < src.index("_save_doc_unlocked(")


def test_reopen_doc_records_the_clear():
    # Arrange
    doc = reopen_mod.handle_reopen.__doc__ or ""
    # Act + Assert
    assert "_clear_completion_stamp_on_leaving_done" in doc
    assert "completed_at" in doc


def test_restore_handler_source_clears_the_snapshot_stamp():
    # Arrange
    src = inspect.getsource(undo_mod.handle_restore)
    # Act + Assert: the re-inserted mapping is unstamped (helper, not pops).
    assert "clear_completion_stamp(task)" in src


def test_restore_handler_clear_is_gated_on_not_done():
    # Arrange
    src = inspect.getsource(undo_mod.handle_restore)
    # Act: restored AS `done` keeps its genuine stamp; every other final
    # status clears. The gate must precede the clear.
    gate_at = src.index('if task.get("status") != "done":')
    # Assert
    assert gate_at < src.index("clear_completion_stamp(task)")
    assert gate_at < src.index("_save_doc_unlocked(")


def test_restore_handler_doc_records_the_clear():
    # Arrange
    doc = undo_mod.handle_restore.__doc__ or ""
    # Act + Assert
    assert "completed_at" in doc


def test_restore_verb_source_clears_the_restored_copy():
    # Arrange
    from scitex_cards import _store_lifecycle

    src = inspect.getsource(_store_lifecycle.restore_task)
    # Act + Assert: the COPY is unstamped (caller's dict stays replayable),
    # gated on re-entering in a live status.
    assert "clear_completion_stamp(restored)" in src
    gate_at = src.index("if restored.get")
    assert gate_at < src.index("clear_completion_stamp(restored)")


def test_restore_verb_doc_records_the_clear():
    # Arrange
    from scitex_cards import _store_lifecycle

    doc = _store_lifecycle.restore_task.__doc__ or ""
    # Act + Assert
    assert "completed_at" in doc


def test_restore_verb_unshares_meta_before_clearing():
    # Arrange: `restored` is a SHALLOW copy, so its nested meta dict IS the
    # caller's — the in-place clear must first unshare it, or the caller's
    # Undo payload loses its stamp (the verb promises the caller's dict is
    # never mutated and stays replayable).
    from scitex_cards import _store_lifecycle

    src = inspect.getsource(_store_lifecycle.restore_task)
    # Act + Assert
    unshare_at = src.index('restored["_log_meta"] = dict(_meta)')
    assert unshare_at < src.index("clear_completion_stamp(restored)")


def test_clock_drops_the_stamp_on_done_to_blocked():
    # Arrange: the exact reopen shape — done+stamped card, prior done.
    from scitex_cards._store_clocks import _clear_completion_stamp_on_leaving_done

    task = _stamped("blocked")
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, "done")
    # Assert
    assert cleared is True
    assert (task.get("_log_meta") or {}).get("completed_at") is None


def test_helper_drops_the_stamp_on_a_restored_live_copy():
    # Arrange: the exact restore shape — done+stamped snapshot re-entering
    # as deferred on a copy (caller's Undo payload untouched). The verb
    # unshares `_log_meta` before the in-place clear (shallow copy shares
    # the nested dict); this test mirrors that order because the helper
    # itself is deliberately in-place.
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
    assert (restored.get("_log_meta") or {}).get("completed_at") is None
    # And the caller's snapshot keeps whatever it had (replayable payload).
    assert snapshot["_log_meta"]["completed_at"] == "2026-01-01T00:00:00Z"


def test_project_board_update_inherits_the_stamp_clock():
    # Arrange: `project_board.update_card` delegates to `update_task` via
    # `_default_update`, so a board edit leaving `done` inherits the named
    # clock without calling it itself. A future refactor swapping the
    # default writer for inline writes would silently lose the clear with
    # every pin above still green — this test locks the delegation chain.
    from scitex_cards._django.handlers import project_board as pb_mod
    from scitex_cards import _store_mutate as mutate_mod

    card_src = inspect.getsource(pb_mod.update_card)
    default_src = inspect.getsource(pb_mod._default_update)
    mutate_src = inspect.getsource(mutate_mod.update_task)
    # Act + Assert: the board defaults to the store API, the store API
    # runs the named clock at the single status-change point.
    assert "writer = update or _default_update" in card_src
    assert "return update_task(**fields)" in default_src
    assert "_clear_completion_stamp_on_leaving_done(task, prior_status)" in mutate_src
