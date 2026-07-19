#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for the Phase-1 mutation Python API (`scitex_cards._store`).

Real round-trips against a `tmp_path` YAML file — no mocks, no
green-theater (Req STX-NM / PA-306). The concurrent-writer test spawns
two real subprocesses with the actual `fcntl.flock` semantics so it
proves the lock serializes interleaved add/update/done calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

from scitex_cards import _model, _store


# --------------------------------------------------------------------------- #
# add_task                                                                    #
# --------------------------------------------------------------------------- #
def test_add_task_returns_inserted_dict(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    inserted = _store.add_task(
        store,
        id="design",
        title="Design phase",
        status="pending",
        assignee="agent:test-suite",
    )
    # Assert — core fields present (created_at + last_activity auto-stamped
    # by D11 partial-fix; their exact ISO values are tested separately).
    assert {k: inserted[k] for k in ("id", "title", "status")} == {
        "id": "design",
        "title": "Design phase",
        "status": "pending",
    }


def test_add_task_creates_store_on_disk(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    _store.add_task(
        store,
        id="design",
        title="Design phase",
        status="pending",
        assignee="agent:test-suite",
    )
    on_disk = _model.load_tasks(store)
    # Assert
    assert len(on_disk) == 1


def test_add_task_id_round_trips(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    _store.add_task(
        store,
        id="design",
        title="Design phase",
        status="pending",
        assignee="agent:test-suite",
    )
    on_disk = _model.load_tasks(store)
    # Assert
    assert on_disk[0]["id"] == "design"


def test_add_task_appends_preserves_order(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    _store.add_task(
        store,
        id="b",
        title="B",
        status="in_progress",
        scope="agent:proj-scitex-todo",
        assignee="agent:proj-scitex-todo",
        priority=2,
        parent="a",
        note="b is under a",
    )
    # Act
    on_disk = _model.load_tasks(store)
    # Assert
    assert [t["id"] for t in on_disk] == ["a", "b"]


def test_add_task_appends_status(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    _store.add_task(
        store,
        id="b",
        title="B",
        status="in_progress",
        scope="agent:proj-scitex-todo",
        assignee="agent:proj-scitex-todo",
        priority=2,
        parent="a",
        note="b is under a",
    )
    # Act
    b = _model.load_tasks(store)[1]
    # Assert
    assert b["status"] == "in_progress"


def test_add_task_appends_scope(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    _store.add_task(
        store,
        id="b",
        title="B",
        status="in_progress",
        scope="agent:proj-scitex-todo",
        assignee="agent:proj-scitex-todo",
        priority=2,
        parent="a",
        note="b is under a",
    )
    # Act
    b = _model.load_tasks(store)[1]
    # Assert
    assert b["scope"] == "agent:proj-scitex-todo"


def test_add_task_appends_assignee(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    _store.add_task(
        store,
        id="b",
        title="B",
        status="in_progress",
        scope="agent:proj-scitex-todo",
        assignee="agent:proj-scitex-todo",
        priority=2,
        parent="a",
        note="b is under a",
    )
    # Act
    b = _model.load_tasks(store)[1]
    # Assert
    assert b["assignee"] == "agent:proj-scitex-todo"


def test_add_task_appends_priority(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    _store.add_task(
        store,
        id="b",
        title="B",
        status="in_progress",
        scope="agent:proj-scitex-todo",
        assignee="agent:proj-scitex-todo",
        priority=2,
        parent="a",
        note="b is under a",
    )
    # Act
    b = _model.load_tasks(store)[1]
    # Assert
    assert b["priority"] == 2


def test_add_task_appends_parent(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    _store.add_task(
        store,
        id="b",
        title="B",
        status="in_progress",
        scope="agent:proj-scitex-todo",
        assignee="agent:proj-scitex-todo",
        priority=2,
        parent="a",
        note="b is under a",
    )
    # Act
    b = _model.load_tasks(store)[1]
    # Assert
    assert b["parent"] == "a"


def test_add_task_appends_note(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    _store.add_task(
        store,
        id="b",
        title="B",
        status="in_progress",
        scope="agent:proj-scitex-todo",
        assignee="agent:proj-scitex-todo",
        priority=2,
        parent="a",
        note="b is under a",
    )
    # Act
    b = _model.load_tasks(store)[1]
    # Assert
    assert b["note"] == "b is under a"


def test_add_task_rejects_duplicate_id(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Act
    ctx = pytest.raises(_model.TaskValidationError)
    # Assert
    with ctx:
        _store.add_task(store, id="a", title="A2", assignee="agent:test-suite")


#: Operator ruling 2026-07-10: a status value must never cost someone their
#: card. Save-side validation WARNS and persists; the fail-loud rejection lives
#: at the sources (the CLI --status Choice). Both halves are asserted, in
#: separate tests: silently persisting a bad value would be as wrong as
#: refusing it, and one test could only ever have caught one of the two.
def _add_with_invalid_status(store):
    return _store.add_task(
        store, id="a", title="A", status="not-a-status", assignee="agent:test-suite"
    )


def test_add_task_warns_about_an_invalid_status(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    warned = pytest.warns(UserWarning, match="not-a-status")
    # Assert — loud, but not fatal.
    with warned:
        _add_with_invalid_status(store)


def test_add_task_tolerates_invalid_status_with_warning(tmp_path):
    # Arrange — the warning itself is pinned by the sibling test above, so it is
    # merely silenced here rather than asserted on a second time.
    store = tmp_path / "tasks.yaml"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        _add_with_invalid_status(store)
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert — the card exists; nothing was destroyed over a bad value.
    assert on_disk["status"] == "not-a-status"


# --------------------------------------------------------------------------- #
# add_task — operator-co-designed fields via **extras (PR #65)                #
# --------------------------------------------------------------------------- #
def test_add_task_accepts_project_via_extras(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    _store.add_task(
        store, id="a", title="A", project="scitex-todo", assignee="agent:test-suite"
    )
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["project"] == "scitex-todo"


def test_add_task_accepts_agent_via_extras(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    _store.add_task(store, id="a", title="A", agent="proj-scitex-todo")
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["agent"] == "proj-scitex-todo"


def test_add_task_accepts_pr_url_via_extras(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    url = "https://github.com/ywatanabe1989/scitex-todo/pull/65"
    # Act
    _store.add_task(store, id="a", title="A", pr_url=url, assignee="agent:test-suite")
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["pr_url"] == url


def test_add_task_kind_compute_persists_kind(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    _store.add_task(
        store,
        id="a",
        title="A",
        kind="compute",
        job_id="25754194",
        command="srun -p gpu my_script.py",
        started_at="2026-06-07T00:00:00Z",
        assignee="agent:test-suite",
    )
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["kind"] == "compute"


def test_add_task_invalid_kind_raises_validation_error(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    ctx = pytest.raises(_model.TaskValidationError)
    # Assert
    with ctx:
        _store.add_task(
            store, id="a", title="A", kind="bogus", assignee="agent:test-suite"
        )


def test_add_task_none_extras_are_dropped(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    _store.add_task(
        store, id="a", title="A", project=None, agent=None, assignee="agent:test"
    )
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert "project" not in on_disk


# --------------------------------------------------------------------------- #
# D11 partial-fix — auto-stamp created_at + last_activity (PR #67-stamps)     #
# --------------------------------------------------------------------------- #
def test_add_task_auto_stamps_created_at(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    inserted = _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Assert — created_at present and ISO-Z formatted
    assert inserted["created_at"].endswith("Z")


def test_add_task_auto_stamps_last_activity(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    inserted = _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Assert
    assert inserted["last_activity"].endswith("Z")


def test_add_task_created_at_equals_last_activity_on_insert(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    inserted = _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Assert
    assert inserted["created_at"] == inserted["last_activity"]


def test_update_task_auto_bumps_last_activity(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    insert_stamp = inserted["last_activity"]
    # Wait a beat so the next stamp differs at second resolution.
    import time as _time

    _time.sleep(1.1)
    # Act
    merged = _store.update_task(store, "a", status="in_progress")
    # Assert
    assert merged["last_activity"] != insert_stamp


def test_update_task_preserves_created_at(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    created = inserted["created_at"]
    # Act
    merged = _store.update_task(store, "a", status="in_progress")
    # Assert
    assert merged["created_at"] == created


def test_update_task_explicit_last_activity_wins_over_auto_stamp(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    explicit = "2026-01-01T00:00:00Z"
    # Act
    merged = _store.update_task(store, "a", last_activity=explicit)
    # Assert
    assert merged["last_activity"] == explicit


# --------------------------------------------------------------------------- #
# update_task                                                                 #
# --------------------------------------------------------------------------- #
def test_update_task_changes_status(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", priority=10, assignee="agent:test-suite")
    # Act
    merged = _store.update_task(
        store, "a", status="in_progress", priority=1, scope="agent:lead"
    )
    # Assert
    assert merged["status"] == "in_progress"


def test_update_task_changes_priority(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", priority=10, assignee="agent:test-suite")
    # Act
    merged = _store.update_task(
        store, "a", status="in_progress", priority=1, scope="agent:lead"
    )
    # Assert
    assert merged["priority"] == 1


def test_update_task_changes_scope(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", priority=10, assignee="agent:test-suite")
    # Act
    merged = _store.update_task(
        store, "a", status="in_progress", priority=1, scope="agent:lead"
    )
    # Assert
    assert merged["scope"] == "agent:lead"


def test_update_task_persists_scope(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", priority=10, assignee="agent:test-suite")
    _store.update_task(store, "a", scope="agent:lead")
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["scope"] == "agent:lead"


def test_update_task_passing_none_clears_field_in_return(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(
        store,
        id="a",
        title="A",
        scope="agent:proj-scitex-todo",
        assignee="agent:test-suite",
    )
    # Act
    merged = _store.update_task(store, "a", scope=None)
    # Assert
    assert "scope" not in merged


def test_update_task_passing_none_clears_field_on_disk(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(
        store,
        id="a",
        title="A",
        scope="agent:proj-scitex-todo",
        assignee="agent:test-suite",
    )
    # Act
    _store.update_task(store, "a", scope=None)
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert "scope" not in on_disk


def test_update_task_missing_raises_TaskNotFound(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Act
    ctx = pytest.raises(_store.TaskNotFoundError)
    # Assert
    with ctx:
        _store.update_task(store, "nope", status="done")


def test_update_task_empty_id_typeerror(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Act
    ctx = pytest.raises(TypeError)
    # Assert
    with ctx:
        _store.update_task(store, "", status="done")


# --------------------------------------------------------------------------- #
# complete_task                                                               #
# --------------------------------------------------------------------------- #
def test_complete_task_sets_status_done(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT_ID", "agent:test")
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Act
    done = _store.complete_task(store, "a")
    # Assert
    assert done["status"] == "done"


def test_complete_task_stamps_completed_by(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT_ID", "agent:test")
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Act
    done = _store.complete_task(store, "a")
    # Assert
    assert done["_log_meta"]["completed_by"] == "agent:test"


def test_complete_task_stamps_completed_at_z_suffix(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT_ID", "agent:test")
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Act
    done = _store.complete_task(store, "a")
    # Assert
    assert done["_log_meta"]["completed_at"].endswith("Z")


def test_complete_task_stamps_completed_at_iso_format(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT_ID", "agent:test")
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Act
    done = _store.complete_task(store, "a")
    # Assert
    assert "T" in done["_log_meta"]["completed_at"]


def test_complete_task_persists_completed_by(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT_ID", "agent:test")
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    done = _store.complete_task(store, "a")
    # Act
    persisted = _model.load_tasks(store)[0]
    # Assert
    assert persisted["_log_meta"]["completed_by"] == "agent:test"


def test_complete_task_persists_completed_at(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT_ID", "agent:test")
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    done = _store.complete_task(store, "a")
    stamp = done["_log_meta"]["completed_at"]
    # Act
    persisted = _model.load_tasks(store)[0]
    # Assert
    assert persisted["_log_meta"]["completed_at"] == stamp


def test_complete_task_explicit_by_overrides_env(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT_ID", "agent:env")
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Act
    done = _store.complete_task(store, "a", by="agent:cli")
    # Assert
    assert done["_log_meta"]["completed_by"] == "agent:cli"


def test_complete_task_is_idempotent_timestamp(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT_ID", "agent:first")
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    first = _store.complete_task(store, "a")
    env.set("SCITEX_TODO_AGENT_ID", "agent:second")
    # Act
    second = _store.complete_task(store, "a")
    # Assert
    assert first["_log_meta"]["completed_at"] == second["_log_meta"]["completed_at"]


def test_complete_task_is_idempotent_preserves_original_by(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT_ID", "agent:first")
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    _store.complete_task(store, "a")
    env.set("SCITEX_TODO_AGENT_ID", "agent:second")
    # Act
    second = _store.complete_task(store, "a")
    # Assert
    assert second["_log_meta"]["completed_by"] == "agent:first"


def test_complete_task_missing_raises(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Act
    ctx = pytest.raises(_store.TaskNotFoundError)
    # Assert
    with ctx:
        _store.complete_task(store, "nope")


# --------------------------------------------------------------------------- #
# list_tasks (scope/assignee/status filters)                                  #
# --------------------------------------------------------------------------- #
@pytest.fixture
def populated_store(tmp_path):
    store = tmp_path / "tasks.yaml"
    _store.add_task(
        store, id="a", title="A", scope="agent:lead", assignee="agent:test-suite"
    )
    _store.add_task(
        store,
        id="b",
        title="B",
        scope="agent:proj-scitex-todo",
        assignee="agent:proj-scitex-todo",
    )
    _store.add_task(
        store, id="c", title="C", status="done", assignee="agent:test-suite"
    )
    _store.add_task(
        store,
        id="d",
        title="D",
        scope="agent:proj-scitex-todo",
        status="in_progress",
        assignee="agent:test-suite",
    )
    return store


def test_list_tasks_no_filter_returns_all(populated_store):
    # Arrange
    store = populated_store
    # Act
    rows = _store.list_tasks(store, scope="")
    # Assert
    assert {r["id"] for r in rows} == {"a", "b", "c", "d"}


def test_list_tasks_filters_by_scope(populated_store):
    # Arrange
    store = populated_store
    # Act
    rows = _store.list_tasks(store, scope="agent:proj-scitex-todo")
    # Assert
    assert {r["id"] for r in rows} == {"b", "d"}


def test_list_tasks_filters_by_assignee(populated_store):
    # Arrange
    store = populated_store
    # Act
    rows = _store.list_tasks(store, scope="", assignee="agent:proj-scitex-todo")
    # Assert
    assert {r["id"] for r in rows} == {"b"}


def test_list_tasks_filters_by_status(populated_store):
    # Arrange
    store = populated_store
    # Act
    rows = _store.list_tasks(store, scope="", status="done")
    # Assert
    assert {r["id"] for r in rows} == {"c"}


def test_list_tasks_env_scope_is_default(populated_store, env):
    # Arrange
    store = populated_store
    env.set("SCITEX_TODO_SCOPE", "agent:lead")
    # Act
    rows = _store.list_tasks(store)
    # Assert
    assert {r["id"] for r in rows} == {"a"}


def test_list_tasks_explicit_empty_string_overrides_env(populated_store, env):
    # Arrange
    store = populated_store
    env.set("SCITEX_TODO_SCOPE", "agent:lead")
    # Act
    rows = _store.list_tasks(store, scope="")
    # Assert
    assert {r["id"] for r in rows} == {"a", "b", "c", "d"}


# --------------------------------------------------------------------------- #
# list_tasks — PR #66 filter expansion (agent / project / host / blocker /    #
# kind / id_prefix / blocking_me + multi-status via statuses=)                #
# --------------------------------------------------------------------------- #
@pytest.fixture
def extended_store(tmp_path):
    """Store seeded with operator-co-designed fields for filter tests.

    Uses ``add_task`` for the schema fields the develop-side API
    accepts directly, then ``update_task(**fields)`` for the
    operator-co-designed extras (project / host / agent / blocker /
    kind / job_id). The CLI / Python **extras surface on add_task
    lands in a sibling PR; this PR's filter logic doesn't require
    that surface to be exercised end-to-end.
    """
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="proj-x-1", title="X1", assignee="agent:test-suite")
    _store.update_task(store, "proj-x-1", agent="proj-x", project="x", host="alpha")
    _store.add_task(
        store,
        id="proj-x-2",
        title="X2",
        status="in_progress",
        assignee="agent:test-suite",
    )
    _store.update_task(store, "proj-x-2", agent="proj-x", project="x", host="beta")
    _store.add_task(
        store, id="proj-y-1", title="Y1", status="blocked", assignee="agent:test-suite"
    )
    _store.update_task(
        store,
        "proj-y-1",
        agent="proj-y",
        project="y",
        host="alpha",
        blocker="operator-decision",
    )
    _store.add_task(
        store, id="proj-y-2", title="Y2", status="blocked", assignee="agent:test-suite"
    )
    _store.update_task(
        store,
        "proj-y-2",
        agent="proj-y",
        project="y",
        host="alpha",
        blocker="dependency",
    )
    _store.add_task(store, id="compute-1", title="C1", assignee="agent:test-suite")
    _store.update_task(store, "compute-1", agent="proj-x", kind="compute", job_id="999")
    return store


def test_list_tasks_filters_by_agent(extended_store):
    # Arrange
    store = extended_store
    # Act
    rows = _store.list_tasks(store, scope="", agent="proj-x")
    # Assert
    assert {r["id"] for r in rows} == {"proj-x-1", "proj-x-2", "compute-1"}


def test_list_tasks_filters_by_project(extended_store):
    # Arrange
    store = extended_store
    # Act
    rows = _store.list_tasks(store, scope="", project="y")
    # Assert
    assert {r["id"] for r in rows} == {"proj-y-1", "proj-y-2"}


def test_list_tasks_filters_by_host(extended_store):
    # Arrange
    store = extended_store
    # Act
    rows = _store.list_tasks(store, scope="", host="alpha")
    # Assert
    assert {r["id"] for r in rows} == {"proj-x-1", "proj-y-1", "proj-y-2"}


def test_list_tasks_filters_by_blocker_exact(extended_store):
    # Arrange
    store = extended_store
    # Act
    rows = _store.list_tasks(store, scope="", blocker="operator-decision")
    # Assert
    assert {r["id"] for r in rows} == {"proj-y-1"}


def test_list_tasks_filters_by_blocker_none_token(extended_store):
    # Arrange
    store = extended_store
    # Act
    rows = _store.list_tasks(store, scope="", blocker="__none")
    # Assert — all rows WITHOUT a blocker field
    assert {r["id"] for r in rows} == {"proj-x-1", "proj-x-2", "compute-1"}


def test_list_tasks_filters_by_kind_compute(extended_store):
    # Arrange
    store = extended_store
    # Act
    rows = _store.list_tasks(store, scope="", kind="compute")
    # Assert
    assert {r["id"] for r in rows} == {"compute-1"}


# proj-x-1, proj-x-2, proj-y-1 and proj-y-2 have NO kind field at all
# (absent ≡ "task" per ADR-0002).
def test_list_tasks_kind_task_matches_absent(extended_store):
    # Arrange
    store = extended_store
    # Act
    rows = _store.list_tasks(store, scope="", kind="task")
    # Assert
    assert {r["id"] for r in rows} == {"proj-x-1", "proj-x-2", "proj-y-1", "proj-y-2"}


def test_list_tasks_blocking_me_predicate(extended_store):
    # Arrange
    store = extended_store
    # Act
    rows = _store.list_tasks(store, scope="", blocking_me=True)
    # Assert
    assert {r["id"] for r in rows} == {"proj-y-1"}


def test_list_tasks_id_prefix_matches_prefix(extended_store):
    # Arrange
    store = extended_store
    # Act
    rows = _store.list_tasks(store, scope="", id_prefix="proj-y")
    # Assert
    assert {r["id"] for r in rows} == {"proj-y-1", "proj-y-2"}


def test_list_tasks_multi_status_unions(extended_store):
    # Arrange
    store = extended_store
    # Act
    rows = _store.list_tasks(store, scope="", statuses=["in_progress", "blocked"])
    # Assert
    assert {r["id"] for r in rows} == {"proj-x-2", "proj-y-1", "proj-y-2"}


def test_list_tasks_filters_compose_AND(extended_store):
    # Arrange
    store = extended_store
    # Act
    rows = _store.list_tasks(
        store, scope="", agent="proj-y", blocker="operator-decision"
    )
    # Assert — agent AND blocker narrow together, they do not union.
    assert {r["id"] for r in rows} == {"proj-y-1"}


@pytest.fixture
def overdue_store(tmp_path):
    """Fixture for the ``--overdue`` predicate. Three tasks:
    * past-due pending  → matches
    * past-due done     → terminal, does NOT match
    * future-due pending → not yet due, does NOT match
    """
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="past-pending", title="P", assignee="agent:test-suite")
    _store.update_task(store, "past-pending", deadline="2000-01-01")
    _store.add_task(
        store, id="past-done", title="D", status="done", assignee="agent:test-suite"
    )
    _store.update_task(store, "past-done", deadline="2000-01-01")
    _store.add_task(store, id="future-pending", title="F", assignee="agent:test-suite")
    _store.update_task(store, "future-pending", deadline="2099-01-01")
    return store


def test_list_tasks_overdue_predicate_matches_past_due_only(overdue_store):
    # Arrange
    store = overdue_store
    # Act
    rows = _store.list_tasks(store, scope="", overdue=True)
    # Assert — only the past-due pending row matches.
    assert {r["id"] for r in rows} == {"past-pending"}


# --------------------------------------------------------------------------- #
# summary                                                                     #
# --------------------------------------------------------------------------- #
def test_summary_total_count(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert
    assert info["total"] == 4


def test_summary_by_status_has_all_valid_statuses(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert
    for status in _model.VALID_STATUSES:
        assert status in info["by_status"]


# `add_task`'s default status is `deferred` since the abolition, so cards a
# and b land there.
def test_summary_by_status_deferred_count(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert
    assert info["by_status"]["deferred"] == 2


def test_summary_by_status_done_count(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert
    assert info["by_status"]["done"] == 1


def test_summary_by_status_in_progress_count(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert
    assert info["by_status"]["in_progress"] == 1


def test_summary_by_scope_lead_count(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert
    assert info["by_scope"]["agent:lead"] == 1


def test_summary_by_scope_proj_count(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert
    assert info["by_scope"]["agent:proj-scitex-todo"] == 2


def test_summary_by_scope_empty_count(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert
    assert info["by_scope"][""] == 1


def test_summary_by_assignee_proj_count(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert
    assert info["by_assignee"]["agent:proj-scitex-todo"] == 1


#: Assignee is now MANDATORY (no card can have an empty assignee via
#: `add_task`), so the three non-proj cards (a/c/d) bucket under their owner
#: `agent:test-suite` rather than the old empty-string bucket. Split in two:
#: the old bucket is EMPTY, and the owner bucket holds all three. Asserting only
#: the first would also pass if the summary had simply lost the three cards.
def test_summary_by_assignee_has_no_empty_bucket(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert
    assert info["by_assignee"].get("", 0) == 0


def test_summary_by_assignee_buckets_cards_under_their_owner(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert — the three otherwise-unscoped cards are counted, not dropped.
    assert info["by_assignee"]["agent:test-suite"] == 3


def test_summary_respects_scope_filter_total(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="agent:proj-scitex-todo")
    # Assert
    assert info["total"] == 2


def test_summary_respects_scope_filter_deferred(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="agent:proj-scitex-todo")
    # Assert
    assert info["by_status"]["deferred"] == 1


def test_summary_respects_scope_filter_in_progress(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="agent:proj-scitex-todo")
    # Assert
    assert info["by_status"]["in_progress"] == 1


# --------------------------------------------------------------------------- #
# Concurrent-writer lock (REAL subprocesses, no mocks)                        #
# --------------------------------------------------------------------------- #
_WRITER_SCRIPT = textwrap.dedent(
    """
    import os, sys, time
    from scitex_cards import _store

    store, agent, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
    # Hold all writers at a barrier so they really do race the lock.
    barrier = sys.argv[4]
    while not os.path.exists(barrier):
        time.sleep(0.01)
    for i in range(count):
        _store.add_task(store, id=f"{agent}-{i}", title=f"{agent} task {i}",
                        scope=f"agent:{agent}", assignee="agent:test-suite")
    print("ok", flush=True)
    """
)


def test_two_concurrent_writers_serialize_via_flock(tmp_path):
    """Two real subprocesses each insert N tasks; the lock must serialize
    them so ALL 2N tasks land in the store with no lost write."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    barrier = tmp_path / "barrier"
    # Seed an existing store so the writers exercise the merge path
    # (existing_doc is not None), which is where a non-locking writer
    # would clobber.
    _store.add_task(store, id="seed", title="seed", assignee="agent:test-suite")

    script = tmp_path / "writer.py"
    script.write_text(_WRITER_SCRIPT)

    env = os.environ.copy()
    # Use the worktree's source via PYTHONPATH so both subprocesses
    # import the SAME `_store` we just tested.
    env["PYTHONPATH"] = (
        str(Path(__file__).resolve().parents[2] / "src")
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )

    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(store), agent, "10", str(barrier)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        for agent in ("alpha", "beta")
    ]
    # Drop the barrier — both writers leap.
    barrier.write_text("go")
    # This timeout is a DEADLOCK DETECTOR, not a latency assertion. The test
    # asserts that flock SERIALIZES the two writers; how long that takes is a
    # property of the machine, not of the lock. A deadlocked flock never
    # returns, so any generous bound catches the real failure — while a tight
    # one fails on a merely busy host and says "lock broken" when it means
    # "runner loaded".
    #
    # It was 30s, and that is exactly what happened: on the shared GPFS-backed
    # CI runner this test timed out and BLOCKED a P0 data-integrity fix (#508)
    # that had nothing to do with locking. Measured on one idle container,
    # same code, same interpreter, five consecutive runs:
    #     35.08s · 4.02s · 16.33s · 8.10s · 2.86s   (12x spread, max > the old bound)
    # The spread is the signal: a bound inside the noise band converts load
    # into a false failure, and a red CI is a drift detector switched off.
    _DEADLOCK_TIMEOUT_SEC = 300
    outs = [p.communicate(timeout=_DEADLOCK_TIMEOUT_SEC) for p in procs]
    # Raise (not assert) on subprocess failure so the lock assertion below is
    # the single assert in this test body (STX-TQ007).
    for (stdout, stderr), p in zip(outs, procs):
        if p.returncode != 0 or stdout.decode().strip() != "ok":
            raise RuntimeError(
                f"writer subprocess failed (rc={p.returncode}): {stderr.decode()}"
            )
    # Act
    tasks = _model.load_tasks(store)
    ids = {t["id"] for t in tasks}
    expected = (
        {"seed"} | {f"alpha-{i}" for i in range(10)} | {f"beta-{i}" for i in range(10)}
    )
    # Assert
    assert ids == expected, (
        f"lost writes: expected 21 ids, got {len(ids)} (diff: {sorted(expected - ids)})"
    )


# --------------------------------------------------------------------------- #
# Path resolution (`_resolved_store` + `where`-style introspection)           #
# --------------------------------------------------------------------------- #
def test_explicit_store_path_wins(tmp_path, env):
    # Arrange
    other = tmp_path / "elsewhere.yaml"
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(tmp_path / "envdefault.yaml"))
    _store.add_task(other, id="here", title="Here", assignee="agent:test-suite")
    # Act
    on_disk = _model.load_tasks(other)
    # Assert
    assert on_disk[0]["id"] == "here"


# --------------------------------------------------------------------------- #
# created_by — the creating USER captured at insert (board ROLES section)     #
# --------------------------------------------------------------------------- #
#: Authorship is asserted twice for each source — on the RETURNED dict and on
#: the row that reached DISK. They are separate tests because a verb that
#: stamps the return value without persisting it looks perfectly correct to
#: its caller and loses the attribution the board's ROLES section reads.
def _added_with_explicit_author(tmp_path):
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(
        store,
        id="a",
        title="A",
        created_by="agent:explicit",
        assignee="agent:test-suite",
    )
    return inserted, _model.load_tasks(store)


def test_add_task_returns_the_explicit_created_by(tmp_path):
    # Arrange
    inserted, _on_disk = _added_with_explicit_author(tmp_path)
    # Act
    author = inserted["created_by"]
    # Assert — an explicit author wins over the env/login chain.
    assert author == "agent:explicit"


def test_add_task_stores_created_by_explicit(tmp_path):
    # Arrange
    _inserted, on_disk = _added_with_explicit_author(tmp_path)
    # Act
    author = on_disk[0]["created_by"]
    # Assert
    assert author == "agent:explicit"


def test_add_task_returns_created_by_from_env(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT_ID", "agent:fromenv")
    # Act
    inserted = _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Assert — no explicit author resolves from $SCITEX_TODO_AGENT_ID, the
    # same chain comment authorship uses.
    assert inserted["created_by"] == "agent:fromenv"


def test_add_task_defaults_created_by_from_env(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT_ID", "agent:fromenv")
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")
    # Act
    on_disk = _model.load_tasks(store)
    # Assert
    assert on_disk[0]["created_by"] == "agent:fromenv"


#: A hand-written legacy row with no created_by field must still load and
#: validate — back-compat: absent created_by is valid. Two claims: the row
#: loads at all, and the field really is absent (rather than back-filled,
#: which would silently invent an author for someone else's card).
def _legacy_row_without_created_by(tmp_path):
    store = tmp_path / "tasks.yaml"
    store.write_text(
        "tasks:\n  - id: legacy\n    title: Legacy\n    status: pending\n",
        encoding="utf-8",
    )
    return _model.load_tasks(store)


def test_legacy_task_without_created_by_is_valid(tmp_path):
    # Arrange
    on_disk = _legacy_row_without_created_by(tmp_path)
    # Act
    task_id = on_disk[0]["id"]
    # Assert — it loaded and validated rather than being refused.
    assert task_id == "legacy"


def test_legacy_task_created_by_is_not_back_filled(tmp_path):
    # Arrange
    on_disk = _legacy_row_without_created_by(tmp_path)
    # Act
    row = on_disk[0]
    # Assert — absent stays absent; no author is invented for the card.
    assert "created_by" not in row


# EOF
