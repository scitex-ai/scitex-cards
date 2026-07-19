#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A card cannot be CLOSED and OPEN at the same time.

THE INVARIANT: a card carrying ``_log_meta.closed_at`` was closed. It must not
also sit in an open status (``deferred`` / ``in_progress`` / ``blocked`` /
``goal``). If it does, the close DID NOT STICK and the card is a ZOMBIE —
finished work that nags its owner in every digest, forever, invisible precisely
because it looks like ordinary backlog.

This is not hypothetical. On 2026-07-13 exactly two such cards were found on the
live board, and only by hand-scanning all 1,467 rows:

    selftest-card-20260701                              closed_at set, status=deferred
    todo-board-reads-stale-project-store-not-canonical  closed_at set, status=deferred

Both carried COMMENTS saying they had been moved to a terminal state. The prose
claimed the change; the FIELD never took it. They nagged the fleet for two days.

An invariant nobody runs is not an invariant — so it is a health check.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_cards import _store
from scitex_cards._health import (
    _check_no_falsely_blocked,
    _check_terminal_state_honest,
)

#: WHY the four ``*_a_none_store`` / ``*_for_none_store`` tests below exist, and
#: why they share the ``store_resolved_from_env`` fixture:
#:
#: A bare ``None`` store must resolve through the standard precedence chain,
#: never reach ``load_tasks`` as ``None``. Regression (2026-07-16, reported by
#: the dotfiles agent on v0.13.5): in a shell with no store env var, these
#: checks fed ``None`` straight to ``load_tasks`` and reported "cannot read the
#: task store (TypeError…)" — 7/9 UNHEALTHY on a perfectly healthy install.
#: Both checks must therefore judge the resolved store (ok) AND say nothing
#: about a TypeError (the symptom the regression printed).


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    path = tmp_path / "tasks.yaml"
    _store.add_task(
        path, id="live", title="ordinary open work", status="deferred", agent="a"
    )
    _store.add_task(path, id="finished", title="really done", status="done", agent="a")
    return path


@pytest.fixture()
def store_resolved_from_env(store: Path, monkeypatch) -> Path:
    """The fixture store becomes what the precedence chain resolves to."""
    monkeypatch.delenv("SCITEX_CARDS_TASKS_YAML_SHARED", raising=False)
    monkeypatch.setenv("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    return store


def _zombify(path: Path, task_id: str, status: str) -> None:
    """Stamp closed_at but leave the card in an OPEN status — the exact bug."""
    _store.update_task(path, task_id, status=status)
    doc = _store.load_tasks(path)
    for t in doc:
        if t.get("id") == task_id:
            t["_log_meta"] = {
                "closed_at": "2026-07-07T10:38:58Z",
                "closed_by": "someone",
            }
    from scitex_cards._model import save_tasks

    save_tasks(doc, path)


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------
def test_a_clean_store_passes(store):
    # Arrange
    untouched = store
    # Act
    result = _check_terminal_state_honest(untouched)
    # Assert
    assert result["ok"] is True


def test_a_genuinely_done_card_is_not_a_zombie(store):
    # `done` + closed_at is the CORRECT shape. The check must not flag it — a
    # check that cries wolf on healthy data is one its reader learns to ignore.
    # Arrange
    _zombify(store, "finished", "done")  # closed_at on a `done` card: legitimate
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is True


def test_a_cancelled_card_is_not_a_zombie(store):
    # Arrange
    _zombify(store, "finished", "cancelled")
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is True


# --------------------------------------------------------------------------
# the bug it exists to catch — one test per open status, because each one is a
# distinct way the close can fail to stick
# --------------------------------------------------------------------------
def test_closed_at_on_a_DEFERRED_card_is_caught(store):
    # THE EXACT SHAPE of both real zombies found on 2026-07-13.
    # Arrange
    _zombify(store, "live", "deferred")
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is False


def test_closed_at_on_an_IN_PROGRESS_card_is_caught(store):
    # Arrange
    _zombify(store, "live", "in_progress")
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is False


def test_closed_at_on_a_BLOCKED_card_is_caught(store):
    # Arrange
    _zombify(store, "live", "blocked")
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is False


def test_the_failure_NAMES_the_offending_card(store):
    # A check that says "something is wrong" without saying WHICH card sends its
    # reader hand-scanning 1,467 rows — which is exactly what I had to do.
    # Arrange
    _zombify(store, "live", "deferred")
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert "live" in result["detail"]


def test_the_hint_says_what_to_actually_DO(store):
    # Every failing check owes its reader the next step, not just a verdict.
    # Arrange
    _zombify(store, "live", "deferred")
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert "status" in result["hint"].lower()


# --------------------------------------------------------------------------
# it must never raise — a health check that explodes reports nothing
# --------------------------------------------------------------------------
def test_an_unreadable_store_is_REPORTED_not_raised(tmp_path: Path):
    """The call must return a verdict, not take the whole doctor down."""
    # Arrange
    missing = tmp_path / "nope" / "tasks.yaml"
    # Act
    result = _check_terminal_state_honest(missing)
    # Assert — it reports a verdict instead of raising.
    assert result["ok"] in (True, False)


def test_an_unreadable_store_still_reports_a_string_detail(tmp_path: Path):
    """The verdict is useless without readable prose saying what went wrong."""
    # Arrange
    missing = tmp_path / "nope" / "tasks.yaml"
    # Act
    result = _check_terminal_state_honest(missing)
    # Assert — always text, never a raised traceback.
    assert isinstance(result["detail"], str)


# --------------------------------------------------------------------------
# a bare `None` store resolves through the standard chain — see the module-level
# rationale above
# --------------------------------------------------------------------------
def test_terminal_state_check_resolves_a_none_store(store_resolved_from_env):
    # Arrange
    no_explicit_store = None
    # Act
    honest = _check_terminal_state_honest(no_explicit_store)
    # Assert — it judged the resolved store, and that store is healthy.
    assert honest["ok"] is True


def test_falsely_blocked_check_resolves_a_none_store(store_resolved_from_env):
    # Arrange
    no_explicit_store = None
    # Act
    blocked = _check_no_falsely_blocked(no_explicit_store)
    # Assert — it judged the resolved store, and that store is healthy.
    assert blocked["ok"] is True


def test_terminal_state_check_reports_no_typeerror_for_none_store(
    store_resolved_from_env,
):
    # Arrange
    no_explicit_store = None
    # Act
    honest = _check_terminal_state_honest(no_explicit_store)
    # Assert — the exact symptom the v0.13.5 regression printed.
    assert "TypeError" not in honest.get("detail", "")


def test_falsely_blocked_check_reports_no_typeerror_for_none_store(
    store_resolved_from_env,
):
    # Arrange
    no_explicit_store = None
    # Act
    blocked = _check_no_falsely_blocked(no_explicit_store)
    # Assert — the exact symptom the v0.13.5 regression printed.
    assert "TypeError" not in blocked.get("detail", "")
