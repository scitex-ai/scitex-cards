#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI: `list-tasks --blocking-operator` — the operator's glanceable queue.

The operator's decision queue is the ``blocking_me`` predicate (status=blocked
AND blocker=operator-decision) rendered as a project-grouped, glanceable view
(title + the note as why / how-to-unblock context). AAA, no mocks (STX-NM),
one assertion per test; ``created_by`` is passed so the fixture is independent
of $SCITEX_TODO_AGENT_ID.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._store import add_task


@pytest.fixture()
def store_with_blocks(tmp_path: Path) -> Path:
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store,
        id="dec-a",
        title="Decide on A",
        status="blocked",
        blocker="operator-decision",
        assignee="agent:owner-a",
        created_by="agent:owner-a",
        project="proj-a",
        note="Pick option 1 vs 2.\nsecond line of detail",
    )
    add_task(
        store=store,
        id="dec-b",
        title="Decide on B",
        status="blocked",
        blocker="operator-decision",
        assignee="agent:owner-b",
        created_by="agent:owner-b",
        project="proj-b",  # no note
    )
    # Noise the predicate must EXCLUDE: a working task and a compute-block.
    add_task(
        store=store,
        id="work-c",
        title="Working C",
        status="in_progress",
        assignee="agent:owner-a",
        created_by="agent:owner-a",
        project="proj-a",
    )
    add_task(
        store=store,
        id="comp-d",
        title="Compute-blocked D",
        status="blocked",
        blocker="compute",
        assignee="agent:owner-a",
        created_by="agent:owner-a",
        project="proj-a",
    )
    return store


def _run(store: Path, *extra: str):
    return CliRunner().invoke(
        main, ["list-tasks", "--blocking-operator", "--tasks", str(store), *extra]
    )


def test_blocking_operator_view_exits_zero(store_with_blocks):
    # Arrange
    store = store_with_blocks
    # Act
    result = _run(store)
    # Assert
    assert result.exit_code == 0, result.output


def test_lists_only_operator_decisions(store_with_blocks):
    # Arrange
    store = store_with_blocks
    # Act
    result = _run(store)
    # Assert
    assert "dec-a" in result.output and "dec-b" in result.output


def test_excludes_non_operator_blocks(store_with_blocks):
    # Arrange
    store = store_with_blocks
    # Act
    result = _run(store)
    # Assert
    assert "work-c" not in result.output and "comp-d" not in result.output


def test_groups_by_project(store_with_blocks):
    # Arrange
    store = store_with_blocks
    # Act
    result = _run(store)
    # Assert
    assert "proj-a" in result.output and "proj-b" in result.output


def test_shows_note_first_line_as_context(store_with_blocks):
    # Arrange
    store = store_with_blocks
    # Act
    result = _run(store)
    # Assert
    assert "Pick option 1 vs 2." in result.output


def test_flags_missing_context(store_with_blocks):
    # Arrange — dec-b has no note -> the view nudges the owner to add the why.
    store = store_with_blocks
    # Act
    result = _run(store)
    # Assert
    assert "no context noted" in result.output


def test_header_counts_decisions(store_with_blocks):
    # Arrange
    store = store_with_blocks
    # Act
    result = _run(store)
    # Assert
    assert "2 decision(s)" in result.output


def test_json_emits_only_matches(store_with_blocks):
    # Arrange
    store = store_with_blocks
    # Act
    result = _run(store, "--json")
    rows = json.loads(result.output)
    # Assert
    assert {r["id"] for r in rows} == {"dec-a", "dec-b"}


def _store_without_blocks(tmp_path: Path) -> Path:
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store,
        id="ok",
        title="fine",
        status="in_progress",
        assignee="agent:o",
        created_by="agent:o",
    )
    return store


def test_empty_queue_exits_zero(tmp_path: Path):
    # Arrange
    store = _store_without_blocks(tmp_path)
    # Act
    result = _run(store)
    # Assert
    assert result.exit_code == 0


def test_empty_queue_is_reported_clearly(tmp_path: Path):
    # Arrange
    store = _store_without_blocks(tmp_path)
    # Act
    result = _run(store)
    # Assert
    assert "Nothing is waiting on the operator" in result.output
