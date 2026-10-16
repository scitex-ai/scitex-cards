#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins: restoring a card clears the blocker gate on non-``blocked``.

Both restore paths take ANY mapping (no status precondition), so either can
re-insert a gated snapshot at a non-``blocked`` status — and the schema
validator REJECTS a real blocker variant on a non-``blocked`` row
(``_validate.py`` rule (b)). Without a clear, the restore of a gated card
fails on its own gate (board: 400 via the ``TaskValidationError`` catch;
store verb: ``TaskValidationError`` out of ``_save_doc_unlocked``).

The concrete trigger is the status-less default: a mapping carrying
``blocker`` but NO status defaults to ``deferred`` (the create-twin
default) — gate retained, status ``deferred`` — which the validator
refuses. Same invariant the ``/archive`` twin keeps
(``task.pop("blocker", None)``), ``close --reason`` keeps
(``blocker=""`` clear-sentinel), and ``handle_reopen`` keeps on every
non-``blocked`` restore — this pins the two remaining restore paths that
bypassed it:

- board ``handle_restore`` pops the gate in place before the save;
- store ``restore_task`` pops the gate on the written COPY (the caller's
  Undo payload stays replayable) before the save.

A verbatim ``blocked`` snapshot keeps its gate on both paths.

These pins run with no database (function-scoped source scans + the pure
validator, in the established ``*_pending_abolished`` style). The
store-backed round-trip twins live in the store-fixture suite for CI-with-PG.

STRICT scitex doctrine: no unittest.mock, no monkeypatch (PA-306) — the
live-write side is pinned via source order, never a stubbed store.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django.handlers import undo as undo_mod  # noqa: E402


def _restore_source() -> str:
    return inspect.getsource(undo_mod.handle_restore)


def _verb_source() -> str:
    from scitex_cards import _store_lifecycle

    return inspect.getsource(_store_lifecycle.restore_task)


def _post_restore(task: dict):
    # Arrange helper, not a test: a RequestFactory POST against a store
    # path that does not exist, so any response short of the store layer
    # proves the decision was made without touching the store.
    request = RequestFactory().post(
        "/restore?store=/nonexistent/store-identity",
        data=json.dumps({"task": task, "refs": []}),
        content_type="application/json",
    )
    board = SimpleNamespace(store_path="/nonexistent/store-identity")
    return undo_mod.handle_restore(request, board)


def test_handler_pops_the_blocker_gate():
    # Arrange
    src = _restore_source()
    # Act + Assert: leaving `blocked` clears the gate in place — the same
    # pop `handle_reopen` and the `/archive` twin use, not a divergent copy.
    assert 'task.pop("blocker", None)' in src


def test_handler_gate_clear_runs_before_the_save():
    # Arrange
    src = _restore_source()
    # Act: the pop must land before the row is persisted — the WRITE ITSELF
    # is refused by the validator (rule (b)), so order is the fix.
    pop_at = src.index('task.pop("blocker", None)')
    # Assert
    assert pop_at < src.index("_save_doc_unlocked(")


def test_handler_gate_clear_is_gated_on_not_blocked():
    # Arrange
    src = _restore_source()
    # Act: a verbatim `blocked` snapshot keeps its gate; every other final
    # status drops it. The gate must precede the pop.
    gate_at = src.index('if task.get("status") != "blocked":')
    # Assert
    assert gate_at < src.index('task.pop("blocker", None)')


def test_handler_doc_records_the_gate_clear():
    # Arrange
    doc = undo_mod.handle_restore.__doc__ or ""
    # Act + Assert
    assert "blocker" in doc
    assert "blocked" in doc


def test_verb_pops_the_gate_on_the_written_copy():
    # Arrange
    src = _verb_source()
    # Act + Assert: the COPY is ungated (caller's Undo payload stays
    # replayable), gated on re-entering in a non-`blocked` status.
    assert 'restored.pop("blocker", None)' in src
    gate_at = src.index('if restored.get("status") != "blocked":')
    assert gate_at < src.index('restored.pop("blocker", None)')
    assert gate_at < src.index("_save_doc_unlocked(")


def test_verb_doc_records_the_gate_clear():
    # Arrange
    from scitex_cards import _store_lifecycle

    doc = _store_lifecycle.restore_task.__doc__ or ""
    # Act + Assert
    assert "blocker" in doc


def test_verb_keeps_a_verbatim_blocked_gate():
    # Arrange: the normal Undo shape — a blocked snapshot replayed verbatim.
    # The pop is gated on `!= "blocked"`, so this mapping is untouched by
    # construction; pin the gate text so a future unconditional pop fails
    # loudly instead of silently stripping real gates.
    src = _verb_source()
    # Act + Assert
    assert 'if restored.get("status") != "blocked":' in src


def test_validator_refuses_a_real_blocker_on_a_deferred_row():
    # Arrange: exactly what the restore write would persist without the
    # pop — a status-less+gated mapping defaulted to `deferred` with its
    # gate retained.
    from scitex_cards._task import TaskValidationError
    from scitex_cards._validate import _validate_tasks

    rows = [
        {
            "id": "was-blocked",
            "title": "gated card",
            "status": "deferred",
            "blocker": "operator-decision",
        }
    ]
    # Act + Assert: rule (b) raises — this refusal is WHY the pop exists.
    try:
        _validate_tasks(rows, source="probe")
    except TaskValidationError as exc:
        assert "operator-decision" in str(exc)
    else:  # pragma: no cover — the pin is vacuous if this ever passes
        raise AssertionError("validator accepted blocker-on-deferred")


def test_validator_accepts_a_deferred_row_with_no_gate():
    # Arrange: the post-fix shape — gate popped, backlog status kept.
    from scitex_cards._validate import _validate_tasks

    rows = [{"id": "was-blocked", "title": "gated card", "status": "deferred"}]
    # Act + Assert: no raise (returns None on success).
    assert _validate_tasks(rows, source="probe") is None


def test_handler_gated_statusless_mapping_reaches_the_store_layer():
    # Arrange: a mapping carrying a gate but NO status. The handler
    # defaults it to `deferred` (create-twin default) and now pops the
    # gate, so the request must clear validation and die loudly at the
    # store layer on the bogus path — never a 400 on its own gate, never
    # a silent 200. Without the pop this 400s via the save-path catch.
    task = {"id": "gated-no-status", "title": "Gated without status", "blocker": "operator-decision"}
    # Act
    # Assert — loud store-layer failure, never a 400 or a silent 200.
    with pytest.raises(Exception):
        _post_restore(task)
