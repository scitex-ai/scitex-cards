#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for scitex_cards._groups (project-cluster schema + validator).

Each test follows the AAA pattern + one assertion per logical concept
(TQ001 / TQ002 / TQ007). No mocks (STX-NM / PA-306) — tests write a
minimal real YAML store to a tmp_path and load it through the public
``load_groups`` entry point.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_cards._groups import Group, load_groups
from scitex_cards._model import TaskValidationError


def _write_store(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "tasks.yaml"
    p.write_text(body, encoding="utf-8")
    return p


class TestStoreWithoutGroupsKey:
    def test_returns_empty_list(self, tmp_path: Path) -> None:
        # Arrange — store without any `groups:` key (the back-compat path).
        # Fixture seeds `status: deferred`: `pending` was abolished
        # 2026-07-10 (ABOLISHED_STATUSES in _task.py) and the assertion
        # under test never touches the status, so a `pending` seed would
        # pass while teaching the abolished value as a live example.
        store = _write_store(
            tmp_path,
            "tasks:\n  - id: a\n    title: A\n    status: deferred\n",
        )
        # Act
        groups = load_groups(store)
        # Assert
        assert groups == []


class TestSimpleProjectGroup:
    def test_loads_two_project_group(self, tmp_path: Path) -> None:
        # Arrange
        store = _write_store(
            tmp_path,
            "groups:\n"
            "  - id: collab\n"
            "    label: 'Collab'\n"
            "    projects: [a, b]\n"
            "    color: '#abcdef'\n"
            "tasks: []\n",
        )
        # Act
        groups = load_groups(store)
        # Assert
        assert groups == [
            Group(
                id="collab",
                label="Collab",
                projects=("a", "b"),
                spans_all=False,
                color="#abcdef",
            )
        ]


class TestSpansAllGroup:
    def test_loads_spans_all_lead_group(self, tmp_path: Path) -> None:
        # Arrange
        store = _write_store(
            tmp_path,
            "groups:\n"
            "  - id: lead\n"
            "    label: 'lead (all)'\n"
            "    spans_all: true\n"
            "tasks: []\n",
        )
        # Act
        groups = load_groups(store)
        # Assert
        assert groups[0].spans_all is True

    def test_spans_all_omits_projects_in_wire_shape(self, tmp_path: Path) -> None:
        # Arrange
        store = _write_store(
            tmp_path,
            "groups:\n"
            "  - id: lead\n"
            "    label: 'lead (all)'\n"
            "    spans_all: true\n"
            "tasks: []\n",
        )
        # Act
        groups = load_groups(store)
        # Assert
        assert "projects" not in groups[0].to_dict()


class TestRejectMissingId:
    def test_raises_when_id_missing(self, tmp_path: Path) -> None:
        # Arrange
        store = _write_store(
            tmp_path,
            "groups:\n" "  - label: 'No id'\n" "    projects: [a]\n" "tasks: []\n",
        )
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="id must be a non-empty string"):
            load_groups(store)


class TestRejectMissingLabel:
    def test_raises_when_label_missing(self, tmp_path: Path) -> None:
        # Arrange
        store = _write_store(
            tmp_path,
            "groups:\n" "  - id: g1\n" "    projects: [a]\n" "tasks: []\n",
        )
        # Act
        # Assert
        with pytest.raises(
            TaskValidationError, match="label must be a non-empty string"
        ):
            load_groups(store)


class TestRejectBothSpansAllAndProjects:
    def test_raises_when_spans_all_and_projects_both_set(self, tmp_path: Path) -> None:
        # Arrange
        store = _write_store(
            tmp_path,
            "groups:\n"
            "  - id: g1\n"
            "    label: 'bad'\n"
            "    spans_all: true\n"
            "    projects: [a]\n"
            "tasks: []\n",
        )
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="cannot set both"):
            load_groups(store)


class TestRejectEmptyGroup:
    def test_raises_no_spans_no_projects(self, tmp_path: Path) -> None:
        # Arrange
        store = _write_store(
            tmp_path,
            "groups:\n" "  - id: g1\n" "    label: 'empty'\n" "tasks: []\n",
        )
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="needs either spans_all=true"):
            load_groups(store)


class TestRejectDuplicateIds:
    def test_raises_on_duplicate_group_id(self, tmp_path: Path) -> None:
        # Arrange
        store = _write_store(
            tmp_path,
            "groups:\n"
            "  - id: g1\n"
            "    label: 'one'\n"
            "    projects: [a]\n"
            "  - id: g1\n"
            "    label: 'two'\n"
            "    projects: [b]\n"
            "tasks: []\n",
        )
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="duplicate group id"):
            load_groups(store)


class TestRejectIdCollisionWithTask:
    def test_raises_when_task_ids_supplied(self, tmp_path: Path) -> None:
        # Arrange
        store = _write_store(
            tmp_path,
            "groups:\n"
            "  - id: ghost\n"
            "    label: 'collides'\n"
            "    spans_all: true\n"
            "tasks: []\n",
        )
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="collides with a task id"):
            load_groups(store, task_ids={"ghost"})


class TestRejectNonStringProject:
    def test_raises_on_non_string_project_entry(self, tmp_path: Path) -> None:
        # Arrange
        store = _write_store(
            tmp_path,
            "groups:\n"
            "  - id: g1\n"
            "    label: 'mixed'\n"
            "    projects: [a, 42]\n"
            "tasks: []\n",
        )
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="projects\\[1\\] must be"):
            load_groups(store)


class TestRejectNonBoolSpansAll:
    def test_raises_on_non_bool_spans_all(self, tmp_path: Path) -> None:
        # Arrange
        store = _write_store(
            tmp_path,
            "groups:\n"
            "  - id: g1\n"
            "    label: 'bad'\n"
            "    spans_all: 'yes'\n"
            "    projects: [a]\n"
            "tasks: []\n",
        )
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="spans_all must be a boolean"):
            load_groups(store)


# EOF
