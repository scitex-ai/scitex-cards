#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for the Phase-1 mutation Python API (`scitex_todo._store`).

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
from pathlib import Path

import pytest

from scitex_todo import _model, _store


# --------------------------------------------------------------------------- #
# add_task                                                                    #
# --------------------------------------------------------------------------- #
def test_add_task_returns_inserted_dict(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    inserted = _store.add_task(
        store, id="design", title="Design phase", status="pending"
    )
    # Assert
    assert inserted == {"id": "design", "title": "Design phase", "status": "pending"}


def test_add_task_creates_store_on_disk(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    _store.add_task(store, id="design", title="Design phase", status="pending")
    on_disk = _model.load_tasks(store)
    # Assert
    assert len(on_disk) == 1


def test_add_task_id_round_trips(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    _store.add_task(store, id="design", title="Design phase", status="pending")
    on_disk = _model.load_tasks(store)
    # Assert
    assert on_disk[0]["id"] == "design"


def test_add_task_appends_preserves_order(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    _store.add_task(store, id="b", title="B", status="in_progress",
                    scope="agent:proj-scitex-todo", assignee="agent:proj-scitex-todo",
                    priority=2, parent="a", note="b is under a")
    # Act
    on_disk = _model.load_tasks(store)
    # Assert
    assert [t["id"] for t in on_disk] == ["a", "b"]


def test_add_task_appends_status(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    _store.add_task(store, id="b", title="B", status="in_progress",
                    scope="agent:proj-scitex-todo", assignee="agent:proj-scitex-todo",
                    priority=2, parent="a", note="b is under a")
    # Act
    b = _model.load_tasks(store)[1]
    # Assert
    assert b["status"] == "in_progress"


def test_add_task_appends_scope(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    _store.add_task(store, id="b", title="B", status="in_progress",
                    scope="agent:proj-scitex-todo", assignee="agent:proj-scitex-todo",
                    priority=2, parent="a", note="b is under a")
    # Act
    b = _model.load_tasks(store)[1]
    # Assert
    assert b["scope"] == "agent:proj-scitex-todo"


def test_add_task_appends_assignee(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    _store.add_task(store, id="b", title="B", status="in_progress",
                    scope="agent:proj-scitex-todo", assignee="agent:proj-scitex-todo",
                    priority=2, parent="a", note="b is under a")
    # Act
    b = _model.load_tasks(store)[1]
    # Assert
    assert b["assignee"] == "agent:proj-scitex-todo"


def test_add_task_appends_priority(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    _store.add_task(store, id="b", title="B", status="in_progress",
                    scope="agent:proj-scitex-todo", assignee="agent:proj-scitex-todo",
                    priority=2, parent="a", note="b is under a")
    # Act
    b = _model.load_tasks(store)[1]
    # Assert
    assert b["priority"] == 2


def test_add_task_appends_parent(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    _store.add_task(store, id="b", title="B", status="in_progress",
                    scope="agent:proj-scitex-todo", assignee="agent:proj-scitex-todo",
                    priority=2, parent="a", note="b is under a")
    # Act
    b = _model.load_tasks(store)[1]
    # Assert
    assert b["parent"] == "a"


def test_add_task_appends_note(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    _store.add_task(store, id="b", title="B", status="in_progress",
                    scope="agent:proj-scitex-todo", assignee="agent:proj-scitex-todo",
                    priority=2, parent="a", note="b is under a")
    # Act
    b = _model.load_tasks(store)[1]
    # Assert
    assert b["note"] == "b is under a"


def test_add_task_rejects_duplicate_id(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    # Act
    ctx = pytest.raises(_model.TaskValidationError)
    # Assert
    with ctx:
        _store.add_task(store, id="a", title="A2")


def test_add_task_rejects_invalid_status(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    ctx = pytest.raises(_model.TaskValidationError)
    # Assert
    with ctx:
        _store.add_task(store, id="a", title="A", status="not-a-status")


# --------------------------------------------------------------------------- #
# update_task                                                                 #
# --------------------------------------------------------------------------- #
def test_update_task_changes_status(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", priority=10)
    # Act
    merged = _store.update_task(store, "a", status="in_progress", priority=1,
                                scope="agent:lead")
    # Assert
    assert merged["status"] == "in_progress"


def test_update_task_changes_priority(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", priority=10)
    # Act
    merged = _store.update_task(store, "a", status="in_progress", priority=1,
                                scope="agent:lead")
    # Assert
    assert merged["priority"] == 1


def test_update_task_changes_scope(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", priority=10)
    # Act
    merged = _store.update_task(store, "a", status="in_progress", priority=1,
                                scope="agent:lead")
    # Assert
    assert merged["scope"] == "agent:lead"


def test_update_task_persists_scope(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", priority=10)
    _store.update_task(store, "a", scope="agent:lead")
    # Act
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert on_disk["scope"] == "agent:lead"


def test_update_task_passing_none_clears_field_in_return(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", scope="agent:proj-scitex-todo")
    # Act
    merged = _store.update_task(store, "a", scope=None)
    # Assert
    assert "scope" not in merged


def test_update_task_passing_none_clears_field_on_disk(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", scope="agent:proj-scitex-todo")
    # Act
    _store.update_task(store, "a", scope=None)
    on_disk = _model.load_tasks(store)[0]
    # Assert
    assert "scope" not in on_disk


def test_update_task_missing_raises_TaskNotFound(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    # Act
    ctx = pytest.raises(_store.TaskNotFoundError)
    # Assert
    with ctx:
        _store.update_task(store, "nope", status="done")


def test_update_task_empty_id_typeerror(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
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
    env.set("SCITEX_TODO_AGENT", "agent:test")
    _store.add_task(store, id="a", title="A")
    # Act
    done = _store.complete_task(store, "a")
    # Assert
    assert done["status"] == "done"


def test_complete_task_stamps_completed_by(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT", "agent:test")
    _store.add_task(store, id="a", title="A")
    # Act
    done = _store.complete_task(store, "a")
    # Assert
    assert done["_log_meta"]["completed_by"] == "agent:test"


def test_complete_task_stamps_completed_at_z_suffix(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT", "agent:test")
    _store.add_task(store, id="a", title="A")
    # Act
    done = _store.complete_task(store, "a")
    # Assert
    assert done["_log_meta"]["completed_at"].endswith("Z")


def test_complete_task_stamps_completed_at_iso_format(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT", "agent:test")
    _store.add_task(store, id="a", title="A")
    # Act
    done = _store.complete_task(store, "a")
    # Assert
    assert "T" in done["_log_meta"]["completed_at"]


def test_complete_task_persists_completed_by(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT", "agent:test")
    _store.add_task(store, id="a", title="A")
    done = _store.complete_task(store, "a")
    # Act
    persisted = _model.load_tasks(store)[0]
    # Assert
    assert persisted["_log_meta"]["completed_by"] == "agent:test"


def test_complete_task_persists_completed_at(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT", "agent:test")
    _store.add_task(store, id="a", title="A")
    done = _store.complete_task(store, "a")
    stamp = done["_log_meta"]["completed_at"]
    # Act
    persisted = _model.load_tasks(store)[0]
    # Assert
    assert persisted["_log_meta"]["completed_at"] == stamp


def test_complete_task_explicit_by_overrides_env(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT", "agent:env")
    _store.add_task(store, id="a", title="A")
    # Act
    done = _store.complete_task(store, "a", by="agent:cli")
    # Assert
    assert done["_log_meta"]["completed_by"] == "agent:cli"


def test_complete_task_is_idempotent_timestamp(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT", "agent:first")
    _store.add_task(store, id="a", title="A")
    first = _store.complete_task(store, "a")
    env.set("SCITEX_TODO_AGENT", "agent:second")
    # Act
    second = _store.complete_task(store, "a")
    # Assert
    assert first["_log_meta"]["completed_at"] == second["_log_meta"]["completed_at"]


def test_complete_task_is_idempotent_preserves_original_by(tmp_path, env):
    # Arrange
    store = tmp_path / "tasks.yaml"
    env.set("SCITEX_TODO_AGENT", "agent:first")
    _store.add_task(store, id="a", title="A")
    _store.complete_task(store, "a")
    env.set("SCITEX_TODO_AGENT", "agent:second")
    # Act
    second = _store.complete_task(store, "a")
    # Assert
    assert second["_log_meta"]["completed_by"] == "agent:first"


def test_complete_task_missing_raises(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
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
    _store.add_task(store, id="a", title="A", scope="agent:lead")
    _store.add_task(
        store,
        id="b",
        title="B",
        scope="agent:proj-scitex-todo",
        assignee="agent:proj-scitex-todo",
    )
    _store.add_task(store, id="c", title="C", status="done")
    _store.add_task(
        store,
        id="d",
        title="D",
        scope="agent:proj-scitex-todo",
        status="in_progress",
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
# PR #68 — assignee/agent symmetric matching (ADR-0008 D2 Q2-locked)          #
# --------------------------------------------------------------------------- #
def test_list_tasks_assignee_filter_matches_agent_field(tmp_path):
    """Symmetric matching: row carries `agent`, filter uses `--assignee`."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    _store.update_task(store, "a", agent="proj-scitex-todo")
    # Act
    rows = _store.list_tasks(store, scope="", assignee="proj-scitex-todo")
    # Assert
    assert {r["id"] for r in rows} == {"a"}


def test_list_tasks_assignee_filter_matches_legacy_assignee_field(tmp_path):
    """Symmetric matching: row carries `assignee`, filter uses `--assignee`."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="proj-scitex-todo")
    # Act
    rows = _store.list_tasks(store, scope="", assignee="proj-scitex-todo")
    # Assert
    assert {r["id"] for r in rows} == {"a"}


def test_list_tasks_assignee_filter_prefers_agent_over_assignee(tmp_path):
    """When a row carries BOTH, the `agent` field is the canonical owner.

    Matters during the deprecation window: a row that's been touched
    once carries `agent=proj-X`; the legacy `assignee=proj-Y` is stale.
    Filter by `--assignee proj-X` should return the row (new owner is X).
    """
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="proj-stale")
    _store.update_task(store, "a", agent="proj-fresh")
    # Act
    rows = _store.list_tasks(store, scope="", assignee="proj-fresh")
    # Assert
    assert {r["id"] for r in rows} == {"a"}


def test_list_tasks_assignee_filter_excludes_other_owners(tmp_path):
    """Filter is exclusive — rows owned by other agents still don't match."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="proj-other")
    _store.add_task(store, id="b", title="B")
    _store.update_task(store, "b", agent="proj-other")
    _store.add_task(store, id="c", title="C", assignee="proj-mine")
    # Act
    rows = _store.list_tasks(store, scope="", assignee="proj-mine")
    # Assert
    assert {r["id"] for r in rows} == {"c"}


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


def test_summary_by_status_pending_count(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert
    assert info["by_status"]["pending"] == 2


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


def test_summary_by_assignee_empty_count(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="")
    # Assert
    assert info["by_assignee"][""] == 3


def test_summary_respects_scope_filter_total(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="agent:proj-scitex-todo")
    # Assert
    assert info["total"] == 2


def test_summary_respects_scope_filter_pending(populated_store):
    # Arrange
    store = populated_store
    # Act
    info = _store.summarize_tasks(store, scope="agent:proj-scitex-todo")
    # Assert
    assert info["by_status"]["pending"] == 1


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
    from scitex_todo import _store

    store, agent, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
    # Hold all writers at a barrier so they really do race the lock.
    barrier = sys.argv[4]
    while not os.path.exists(barrier):
        time.sleep(0.01)
    for i in range(count):
        _store.add_task(store, id=f"{agent}-{i}", title=f"{agent} task {i}",
                        scope=f"agent:{agent}")
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
    _store.add_task(store, id="seed", title="seed")

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
    outs = [p.communicate(timeout=30) for p in procs]
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
    expected = {"seed"} | {f"alpha-{i}" for i in range(10)} | {
        f"beta-{i}" for i in range(10)
    }
    # Assert
    assert ids == expected, (
        "lost writes: expected 21 ids, got "
        f"{len(ids)} (diff: {sorted(expected - ids)})"
    )


# --------------------------------------------------------------------------- #
# Path resolution (`_resolved_store` + `where`-style introspection)           #
# --------------------------------------------------------------------------- #
def test_explicit_store_path_wins(tmp_path, env):
    # Arrange
    other = tmp_path / "elsewhere.yaml"
    env.set("SCITEX_TODO_TASKS", str(tmp_path / "envdefault.yaml"))
    _store.add_task(other, id="here", title="Here")
    # Act
    on_disk = _model.load_tasks(other)
    # Assert
    assert on_disk[0]["id"] == "here"


# EOF
