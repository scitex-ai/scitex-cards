#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the mermaid adapter (no mocks; plain in-memory task dicts)."""

from __future__ import annotations

from scitex_cards._diagram import STATUS_STYLE, build_mermaid
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES


def test_build_mermaid_starts_with_flowchart_header():
    # Arrange
    tasks = [{"id": "a", "title": "Alpha", "status": "done"}]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert src.startswith("flowchart TB")


def test_build_mermaid_emits_depends_on_arrow_edge():
    # Arrange
    tasks = [
        {"id": "design", "title": "Design", "status": "done"},
        {
            "id": "build",
            "title": "Build",
            "status": "deferred",
            "depends_on": ["design"],
        },
    ]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "design --> build" in src


def test_build_mermaid_emits_blocks_inhibition_edge():
    # Arrange
    tasks = [
        {"id": "ci", "title": "Flaky CI", "status": "failed", "blocks": ["tests"]},
        {"id": "tests", "title": "Tests", "status": "deferred"},
    ]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "ci -- blocks --x tests" in src


def test_build_mermaid_includes_goal_gold_color():
    # Arrange
    tasks = [{"id": "north", "title": "North Star", "status": "goal"}]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "classDef goal fill:#ffe082" in src


def test_build_mermaid_assigns_goal_class_to_member():
    # Arrange
    tasks = [{"id": "north", "title": "North Star", "status": "goal"}]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "class north goal" in src


def test_build_mermaid_skips_unknown_depends_on_target():
    # Arrange
    tasks = [{"id": "a", "title": "A", "status": "done", "depends_on": ["ghost"]}]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "ghost -->" not in src


def test_build_mermaid_renders_note_under_title():
    # Arrange
    tasks = [{"id": "a", "title": "A", "status": "blocked", "note": "waiting"}]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "A<br/>(waiting)" in src


def test_status_style_keys_equal_the_python_statuses():
    # STATUS_STYLE is the style mirror of _task.VALID_STATUSES: as a SET
    # the keys must equal it, or a live status renders with a `class`
    # assignment but no matching `classDef` (unstyled node), and a dead
    # status keeps a `classDef` nothing can legally carry. `pending`
    # survived here uncaught after its 2026-07-10 abolition — this pins it.
    # Arrange
    # Act
    styled = set(STATUS_STYLE)
    # Assert
    assert styled == set(VALID_STATUSES), (
        "STATUS_STYLE keys have drifted from scitex_cards._task.VALID_STATUSES"
    )


def test_no_abolished_status_keeps_a_style():
    # Arrange
    # Act
    styled = set(STATUS_STYLE)
    # Assert
    assert styled.isdisjoint(ABOLISHED_STATUSES), (
        f"STATUS_STYLE still styles abolished status(es) "
        f"{sorted(styled & set(ABOLISHED_STATUSES))}"
    )


def test_build_mermaid_renders_cancelled_with_the_muted_grey():
    # Arrange
    tasks = [{"id": "old", "title": "Old Plan", "status": "cancelled"}]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "classDef cancelled fill:#eceff1" in src
    assert "class old cancelled" in src


# EOF
