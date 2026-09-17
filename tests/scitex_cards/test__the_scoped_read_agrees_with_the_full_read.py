#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The scoped read must equal the full read's slice of the same project.

WHY THIS IS A TEST AND NOT A BENCHMARK. `export_tasks_scoped` exists because a
page that shows ONE project was paying for the whole store — measured on the
shared store 2026-09-17: 18.8s of document read to reach 470-763 rows a project
actually needs, and 2.05-4.66s when scoped. Speed is the point, but a FASTER
READ THAT DISAGREES WITH THE OLD ONE IS NOT A READ, it is a second opinion: the
board would render a different board depending on which one the caller reached
for. So the guard is equality against the full read's own slice, and speed is
the byproduct.

THE STORE IS SEEDED HERE ON PURPOSE. An earlier draft of this file compared the
two reads against whatever the environment's store happened to contain — which
in CI is a fresh EMPTY throwaway schema, so both sides would have been the empty
set and the equality would have passed without testing anything. That is the
"gate that cannot fail" shape this repo keeps finding, so the doc below is
seeded explicitly and the unassigned card is in it: a scope that quietly
returned the unassigned rows would be caught rather than blessed.

LOCALLY THIS FILE ERRORS, and that is the intended failure mode rather than a
skip: the seed needs `$SCITEX_STORE_DSN`, which only exists when a throwaway
cluster is resolvable (in CI). A skipped gate and a passing one look identical,
so this errors instead of pretending.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._db_export import export_doc, export_tasks_scoped

#: Two projects and one unassigned card — the smallest store that can tell a
#: scope from a filter, and that can catch a scope silently returning rows no
#: project owns.
_DOC = {
    "tasks": [
        {
            "id": "a-1",
            "title": "A one",
            "status": "in_progress",
            "project": "proj-a",
            "assignee": "agent:test-suite",
        },
        {
            "id": "a-2",
            "title": "A two",
            "status": "blocked",
            "blocker": "dependency",
            "project": "proj-a",
            "assignee": "agent:test-suite",
        },
        {
            "id": "b-1",
            "title": "B one",
            "status": "deferred",
            "project": "proj-b",
            "assignee": "agent:test-suite",
        },
        {
            "id": "none-1",
            "title": "Unassigned",
            "status": "deferred",
            "assignee": "agent:test-suite",
        },
    ]
}


@pytest.fixture
def store():
    """Seed a throwaway store this test may read; never the fleet's."""
    from conftest import seed_db_from_doc

    seed_db_from_doc(_DOC, os.environ["SCITEX_STORE_DSN"])
    yield os.environ["SCITEX_STORE_DSN"]


def test_the_scoped_read_refuses_an_empty_project():
    """An empty scope would return the UNASSIGNED rows — the opposite of scope."""
    # Arrange
    empty_project = "   "
    # Act
    read = export_tasks_scoped
    # Assert
    with pytest.raises(ValueError):
        read(project=empty_project)


def test_the_scoped_read_returns_exactly_one_projects_rows(store):
    """proj-a's two cards, and NOT the b-1 card or the unassigned one."""
    # Arrange
    expected = {"a-1", "a-2"}
    # Act
    scoped_ids = {t.get("id") for t in export_tasks_scoped(project="proj-a")}
    # Assert
    assert scoped_ids == expected


def test_the_scoped_read_agrees_with_the_full_reads_slice(store):
    """The guard that matters: same set as filtering the full document."""
    # Arrange
    doc, _threads = export_doc()
    # Act
    scoped_ids = {t.get("id") for t in export_tasks_scoped(project="proj-a")}
    # Assert
    assert scoped_ids == {
        t.get("id") for t in doc["tasks"] if (t.get("project") or None) == "proj-a"
    }


def test_the_scoped_read_is_deterministic(store):
    """Two calls, one sequence: `row_order` has ties in the real store."""
    # Arrange
    read = export_tasks_scoped
    # Act
    first = [t.get("id") for t in read(project="proj-a")]
    second = [t.get("id") for t in read(project="proj-a")]
    # Assert
    assert first == second


# DELIBERATELY NOT ASSERTED ANYWHERE IN THIS FILE: that `export_doc` is
# reproducible. An earlier revision of this change added `, id` to its task
# order to make it so, and CI caught the cost: `list-tasks --json` is contracted
# to return DOCUMENT order (pinned by
# tests/scitex_cards/_cli/test__main.py::test_list_tasks_json_emits_parseable_array),
# and the id tie-break reordered tied rows alphabetically — ['build', 'design']
# where the contract says ['design', 'build']. `row_order` is not unique and the
# store has no ordered column to fall back on, so "document order" here IS
# physical order: a data-model question with its own migration, not a one-word
# read fix. The asymmetry is therefore deliberate — the FULL read keeps its bare
# `ORDER BY row_order`, the SCOPED read is deterministic because it makes no
# document-order promise — and this comment exists so the next reader sees why
# rather than "fixing" it again.
#
# A test asserting that here would be a gate that cannot fail, which is the
# shape this session has already removed twice today.


# EOF
