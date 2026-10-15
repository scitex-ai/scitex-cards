#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dogfood UX fixes: `add` success echo + lenient `blocker: none` validation.

Covers two cards surfaced from the 2026-06-29 neurovista dogfood:

  * ``cards-add-empty-stdout-on-success`` — a successful ``add`` must print a
    one-line confirmation that LEADS with the created id, so success is never
    indistinguishable from a silent failure (empty stdout). The ``--json``
    path stays machine-readable (JSON only, no extra human line).
  * ``cards-blocker-none-validation-lenient`` — the ``"none"`` sentinel ("no
    specific blocker named") must NOT error on a non-blocked status; it is
    normalized away. A REAL blocker variant on a non-blocked status still
    errors.

CliRunner against the ambient per-test store. No mocks.

Store-touching tests request the ``postgres_dsn`` fail-LOUD fixture
(bare-param pattern): every write below targets the ambient per-test
store, which is refused without a writable cluster.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from scitex_cards import _model
from scitex_cards._cli import main


def _store_path() -> str:
    # The store is the database; load/save read+write it and IGNORE any
    # path except as the provenance stamp. Return the PINNED STORE identity
    # (== resolve_tasks_path(None)) so a read-after-write round-trips instead
    # of tripping the stamp-mismatch guard.
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def _add(runner, ident, title, *extra):
    return runner.invoke(
        main,
        [
            "add",
            "--assignee",
            "agent:test-suite",
            ident,
            title,
            *extra,
        ],
    )


# --------------------------------------------------------------------------- #
# Bug 1: add prints a non-empty success line that leads with the id           #
# --------------------------------------------------------------------------- #
def test_add_success_exits_zero(postgres_dsn):
    # Arrange
    runner = CliRunner()
    # Act
    result = _add(runner, "design", "Design phase")
    # Assert
    assert result.exit_code == 0, result.output


def test_add_success_stdout_is_non_empty(postgres_dsn):
    # Arrange
    runner = CliRunner()
    # Act
    result = _add(runner, "design", "Design phase")
    # Assert
    assert result.output.strip() != ""


def test_add_success_stdout_mentions_created_id(postgres_dsn):
    # Arrange
    runner = CliRunner()
    # Act
    result = _add(runner, "design", "Design phase")
    # Assert
    assert "design" in result.output


def test_add_json_stdout_is_pure_json(postgres_dsn):
    """The --json path emits ONLY machine-readable JSON (no human line)."""
    # Arrange
    runner = CliRunner()
    # Act
    result = _add(runner, "design", "Design phase", "--json")
    # Assert — the whole stdout parses as a single JSON object.
    payload = json.loads(result.output.strip())
    assert payload["id"] == "design"


# --------------------------------------------------------------------------- #
# Bug 2: blocker=none is lenient on a non-blocked status                       #
# --------------------------------------------------------------------------- #
def test_add_deferred_blocker_none_succeeds(postgres_dsn):
    # Arrange
    runner = CliRunner()
    # Act
    result = _add(runner, "x", "X", "--status", "deferred", "--blocker", "none")
    # Assert
    assert result.exit_code == 0, result.output


def _add_deferred_blocker_none(postgres_dsn):
    """Add a deferred card carrying the `none` blocker sentinel; reload it."""
    runner = CliRunner()
    store = _store_path()
    _add(runner, "x", "X", "--status", "deferred", "--blocker", "none")
    return _model.load_tasks(store)


def test_add_deferred_blocker_none_keeps_deferred_status(postgres_dsn):
    # Arrange
    # Act
    tasks = _add_deferred_blocker_none(postgres_dsn)
    # Assert
    assert tasks[0]["status"] == "deferred"


def test_add_deferred_blocker_none_reads_back_as_absent(postgres_dsn):
    # Arrange
    # Act
    tasks = _add_deferred_blocker_none(postgres_dsn)
    # Assert — the "none" sentinel is normalized away on a non-blocked row.
    assert tasks[0].get("blocker") in (None,)


def test_add_deferred_blocker_none_stores_no_active_blocker(postgres_dsn):
    # Arrange
    # Act
    tasks = _add_deferred_blocker_none(postgres_dsn)
    # Assert — the key itself is dropped, not written as an empty value.
    assert "blocker" not in tasks[0]


def test_add_deferred_real_blocker_exits_nonzero(postgres_dsn):
    """A REAL blocker variant on a non-blocked status STILL fails loud."""
    # Arrange
    runner = CliRunner()
    # Act
    result = _add(runner, "y", "Y", "--status", "deferred", "--blocker", "compute")
    # Assert
    assert result.exit_code != 0


def test_add_deferred_real_blocker_names_the_field(postgres_dsn):
    """The refusal says WHICH field is wrong."""
    # Arrange
    runner = CliRunner()
    # Act
    result = _add(runner, "y", "Y", "--status", "deferred", "--blocker", "compute")
    # Assert
    assert "blocker" in result.output.lower()


def test_add_rejects_abolished_pending_at_the_cli_boundary(postgres_dsn):
    """`pending` must be unreachable from the CLI.

    Save-side validation only WARNS on an unknown status (operator ruling
    2026-07-10: a status value must never cost someone their card), so the
    enum is held honest at the SOURCES instead. This is one of them.
    """
    # Arrange
    runner = CliRunner()
    # Act
    result = _add(runner, "p", "P", "--status", "pending")
    # Assert
    assert result.exit_code != 0


def test_add_rejecting_pending_names_the_bad_status(postgres_dsn):
    """The refusal echoes the offending value so the fix is obvious."""
    # Arrange
    runner = CliRunner()
    # Act
    result = _add(runner, "p", "P", "--status", "pending")
    # Assert
    assert "pending" in result.output


def test_add_defaults_to_deferred(postgres_dsn):
    """A new card carries a real decision; the default is the backlog.

    The old default was `pending`, and it kept minting abolished cards from
    every agent still on an older build — two appeared in the live store
    within hours of the sweep that removed them.
    """
    # Arrange
    runner = CliRunner()
    store = _store_path()
    # Act
    _add(runner, "d", "D")
    tasks = _model.load_tasks(store)
    # Assert
    assert tasks[0]["status"] == "deferred"


# --------------------------------------------------------------------------- #
# Bug 2 at the model layer (save_tasks / load_tasks validation gate)          #
# --------------------------------------------------------------------------- #
def test_save_tasks_drops_none_blocker_from_the_reloaded_row(postgres_dsn):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    # `deferred` is the successor default: `pending` was abolished 2026-07-10
    # and this test's intent (blocker-`none` normalization) is status-agnostic.
    tasks = [{"id": "x", "title": "X", "status": "deferred", "blocker": "none"}]
    # Act — must NOT raise; the field is dropped in place.
    _model.save_tasks(tasks, store)
    reloaded = _model.load_tasks(store)
    # Assert
    assert "blocker" not in reloaded[0]


def test_save_tasks_normalizes_none_blocker_in_place(postgres_dsn):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    # `deferred` is the successor default: `pending` was abolished 2026-07-10
    # and this test's intent (blocker-`none` normalization) is status-agnostic.
    tasks = [{"id": "x", "title": "X", "status": "deferred", "blocker": "none"}]
    # Act — the caller's own dict is normalized, not just the persisted copy.
    _model.save_tasks(tasks, store)
    # Assert
    assert "blocker" not in tasks[0]


def test_save_tasks_real_blocker_on_deferred_still_raises(postgres_dsn):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    # `deferred` is the successor default: `pending` was abolished 2026-07-10.
    # A real blocker on any non-blocked row raises (rule b, _validate.py).
    tasks = [{"id": "x", "title": "X", "status": "deferred", "blocker": "compute"}]
    # Act
    # Assert
    with pytest.raises(_model.TaskValidationError, match=r"blocker"):
        _model.save_tasks(tasks, store)


def test_save_tasks_none_blocker_on_blocked_is_preserved(postgres_dsn):
    """On a BLOCKED row, `none` is a legitimate sentinel and is kept."""
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    tasks = [{"id": "x", "title": "X", "status": "blocked", "blocker": "none"}]
    # Act
    _model.save_tasks(tasks, store)
    reloaded = _model.load_tasks(store)
    # Assert
    assert reloaded[0]["blocker"] == "none"
