#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins: closing a card clears the blocker gate on leaving ``blocked``.

``handle_archive`` (POST /archive) and the CLI ``close --reason`` verb both
take ANY id (no status precondition), so either can close a ``blocked``
card — and the schema validator REJECTS a real blocker variant on a
non-``blocked`` row (``_validate.py`` rule (b)). Without a clear, the close
of a gated card fails on its own gate (board: 400 via the
``TaskValidationError`` catch; CLI: ``ClickException`` out of
``update_task``).

Same invariant the project board already keeps (``changes["blocker"] = ""``
when the new status is not ``blocked``) and ``handle_reopen`` keeps
(``task.pop("blocker", None)`` on every non-``blocked`` restore) — this
pins the two remaining close paths that bypassed it:

- board ``handle_archive`` pops the gate in place before the save;
- CLI ``close_cmd`` passes the documented clear-sentinel (``blocker=""``,
  resolved to a pop by ``_store_enums.resolve_enum_clears``, never written).

These pins run with no database (function-scoped source scans + the pure
validator / sentinel resolver, in the established ``*_pending_abolished``
style). The store-backed round-trip twin lives in
``tests/scitex_cards/_django/handlers/test_stale.py`` for CI-with-PG.

STRICT scitex doctrine: no unittest.mock, no monkeypatch (PA-306) — the
live-write side is pinned via source order, never a stubbed store.
"""

from __future__ import annotations

import inspect


def _archive_source() -> str:
    from scitex_cards._django.handlers import stale as stale_handler

    return inspect.getsource(stale_handler.handle_archive)


def _close_source() -> str:
    from scitex_cards._cli import _close as close_mod

    # `close_cmd` is a click command object (spec-compliant wrapper), so the
    # implementation lives on its callback — the source pin reads that
    # (plain function in the static view, command object at runtime).
    cmd = close_mod.close_cmd
    return inspect.getsource(getattr(cmd, "callback", cmd))


def test_archive_pops_the_blocker_gate():
    # Arrange
    # Arrange
    # Act
    src = _archive_source()
    # Assert
    assert 'task.pop("blocker", None)' in src


def test_archive_gate_clear_runs_before_the_save():
    # Arrange
    src = _archive_source()
    # Act: the pop must land before the row is persisted, or a crash
    # between write and clear is not the failure — the WRITE ITSELF is
    # refused by the validator (rule (b)), so order is the fix.
    pop_at = src.index('task.pop("blocker", None)')
    # Assert
    assert pop_at < src.index("_save_doc_unlocked(doc")


def test_archive_doc_records_the_gate_clear_case1():
    # Arrange
    # Arrange
    from scitex_cards._django.handlers import stale as stale_handler
    # Act
    doc = stale_handler.handle_archive.__doc__ or ""
    # Assert
    assert "blocker" in doc


def test_archive_doc_records_the_gate_clear_case2():
    # Arrange
    # Arrange
    from scitex_cards._django.handlers import stale as stale_handler
    # Act
    doc = stale_handler.handle_archive.__doc__ or ""
    # Assert
    assert "cancelled" in doc


def test_close_passes_the_blocker_clear_sentinel():
    # Arrange
    # Arrange
    # Act
    src = _close_source()
    # Assert
    assert 'blocker=""' in src


def test_close_clear_runs_inside_the_update_call_case1():
    # Arrange
    # Arrange
    src = _close_source()
    # Act
    call_at = src.index("update_task(")
    # Assert
    assert 'status="cancelled"' in src[call_at:]


def test_close_clear_runs_inside_the_update_call_case2():
    # Arrange
    # Arrange
    src = _close_source()
    # Act
    call_at = src.index("update_task(")
    # Assert
    assert 'blocker=""' in src[call_at:]


def test_close_doc_contract_still_names_cancelled_case1():
    # Arrange: the registered CLI contract lives on the spec wrapper
    # (summary + description), not on the callback docstring.
    # Arrange
    from scitex_cards._cli import _close as close_mod
    cmd = close_mod.close_cmd
    # Act
    contract = " ".join(
        s for s in (getattr(cmd, "help", None), getattr(cmd, "short_help", None)) if s
    )
    # Assert
    assert "terminal-state" in contract


def test_close_doc_contract_still_names_cancelled_case2():
    # Arrange: the registered CLI contract lives on the spec wrapper
    # (summary + description), not on the callback docstring.
    # Arrange
    from scitex_cards._cli import _close as close_mod
    cmd = close_mod.close_cmd
    # Act
    contract = " ".join(
        s for s in (getattr(cmd, "help", None), getattr(cmd, "short_help", None)) if s
    )
    # Assert
    assert "close --reason" in contract


def test_validator_refuses_a_real_blocker_on_a_cancelled_row():
    # Arrange: exactly what the archive write would persist without the
    # pop — a blocked card flipped to cancelled with its gate retained.
    from scitex_cards._task import TaskValidationError
    from scitex_cards._validate import _validate_tasks

    rows = [
        {
            "id": "was-blocked",
            "title": "gated card",
            "status": "cancelled",
            "blocker": "operator-decision",
        }
    ]
    # Act
    try:
        _validate_tasks(rows, source="probe")
    except TaskValidationError as exc:
        # Assert: rule (b) raises — this refusal is WHY the pop exists.
        assert "operator-decision" in str(exc)
    else:  # pragma: no cover — the pin is vacuous if this ever passes
        raise AssertionError("validator accepted blocker-on-cancelled")


def test_validator_accepts_a_cancelled_row_with_no_gate():
    # Arrange: the post-fix shape — gate popped, terminal status kept.
    # Arrange
    from scitex_cards._validate import _validate_tasks
    # Act
    rows = [{"id": "was-blocked", "title": "gated card", "status": "cancelled"}]
    # Assert
    assert _validate_tasks(rows, source="probe") is None


def test_blocker_empty_string_resolves_to_a_pop_not_a_write():
    # Arrange: the sentinel the CLI close passes.
    from scitex_cards._store_enums import resolve_enum_clears

    # Act
    resolved = resolve_enum_clears(
        {"status": "cancelled", "blocker": ""}, source="probe"
    )
    # Assert: `""` becomes the store's delete signal (None = pop the key);
    # the status value passes through untouched.
    assert resolved == {"status": "cancelled", "blocker": None}


def test_blocker_sentinel_is_a_noop_on_gateless_rows_case1():
    # Arrange: the common close path (a deferred card was never gated) —
    # popping an absent key must not add one.
    # Arrange
    task = {"id": "was-deferred", "status": "cancelled"}
    # Act
    task.pop("blocker", None)
    # Assert
    assert "blocker" not in task


def test_blocker_sentinel_is_a_noop_on_gateless_rows_case2():
    # Arrange: the common close path (a deferred card was never gated) —
    # popping an absent key must not add one.
    # Arrange
    task = {"id": "was-deferred", "status": "cancelled"}
    # Act
    task.pop("blocker", None)
    # Assert
    assert task["status"] == "cancelled"
