#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the runbook close-target leftover (deferred abolition).

Doc ``50`` (``50_board-reconciliation-runbook.md``) §3 correctly teaches
that ``close`` flips status to ``cancelled`` (terminal), but its §8
gotcha #4 still said ``Close uses `deferred` today`` with a plan to
switch over if ``VALID_STATUSES`` ever grew a dedicated ``closed``
value. That plan is superseded: the CLI ``close`` verb writes
``cancelled`` (``src/scitex_cards/_cli/_close.py``), the board
``/archive`` twin writes ``cancelled`` (``handlers/stale.py``), and
``deferred`` is OPEN since the 2026-07-10 ruling ("deferred
は終了ではない") — so a reader following the gotcha writes an
OPEN-as-closed row the ``terminal_state_honest`` health check FAILs
on. Same dead-prose class as the runnable / reconcile doc sweeps:
the code was already correct, the prose named the abolished
arrangement.

These pins read the bundled doc and the verb source only: no store,
no PostgreSQL — they run in containers where the store-backed suite
refuses via the fail-LOUD guard.
"""

from __future__ import annotations

from scitex_cards._cli._skills import _skills_root  # type: ignore[attr-defined]


def _runbook() -> str:
    root = _skills_root()
    path = root / "50_board-reconciliation-runbook.md"
    assert path.is_file(), f"runbook moved? not found at {path}"
    return path.read_text(encoding="utf-8")


def _close_source() -> str:
    import inspect

    import scitex_cards._cli._close as close_mod

    return inspect.getsource(close_mod)


def test_gotcha_names_cancelled_not_deferred():
    # Arrange
    text = _runbook()
    # Act + Assert: the gotcha teaches the terminal close target.
    assert "Close writes `cancelled` (terminal), not `deferred`" in text


def test_old_deferred_sentinel_plan_absent():
    # Arrange
    text = _runbook()
    # Act + Assert: the superseded sentinel plan is gone.
    assert "Close uses `deferred` today" not in text


def test_gotcha_points_at_the_close_section():
    # Arrange
    text = _runbook()
    # Act + Assert: the gotcha defers to §3, the section that records
    # the reason, so the two cannot drift apart silently.
    assert "see §3 above" in text


def test_close_verb_code_mirror():
    # Arrange
    src = _close_source()
    # Act + Assert: the verb really writes the status the gotcha names
    # (call-site spelling with trailing comma — prose mentions of the
    # word do not match).
    assert 'status="cancelled",' in src


def test_close_target_is_terminal_and_live():
    # Arrange
    from scitex_cards._task import VALID_STATUSES
    from scitex_cards._throughput import TERMINAL_STATUSES
    # Act + Assert: the documented target is a live terminal state and
    # the old sentinel is OPEN, so stamping closed_at on it is honest.
    assert "cancelled" in VALID_STATUSES
    # Assert
    assert "cancelled" in TERMINAL_STATUSES
    # Assert
    assert "deferred" not in TERMINAL_STATUSES


def test_runbook_holds_the_skill_line_budget():
    # Arrange
    text = _runbook()
    # Act + Assert: SK-401 caps skill docs at 200 lines — the repoint
    # must stay line-neutral.
    assert len(text.splitlines()) <= 200
