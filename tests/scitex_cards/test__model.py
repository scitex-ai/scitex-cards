#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the task model + loader/validator (no mocks; real tmp files)."""

from __future__ import annotations

import contextlib
import os
import warnings

import pytest

from scitex_cards import TaskValidationError
from scitex_cards._model import load_tasks, save_tasks
from scitex_cards._validate import _validate_tasks


def _write(tmp_path, text):
    """Seed the canonical DB from a YAML-text document; return the STORE path.

    The store is SQLite now: ``load_tasks`` / ``save_tasks`` read and write the
    canonical database and IGNORE the path argument (it survives only as a
    label in error text). Tests still author their fixtures as readable YAML
    text, so parse it, seed the DB, and return the STORE IDENTITY path — NOT
    the DB path (see the migration playbook's STORE-PATH RULE) so that a
    read-after-write round-trips instead of tripping the provenance stamp.
    """
    from conftest import seed_db_from_doc

    from scitex_cards._yaml import safe_load

    doc = safe_load(text) or {}
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def test_load_tasks_returns_validated_list_in_order(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - {id: a, title: First, status: done}\n"
        "  - {id: b, title: Second, status: pending}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert [t["id"] for t in tasks] == ["a", "b"]


def test_load_tasks_accepts_goal_status(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {id: north, title: Big Goal, status: goal}\n")
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["status"] == "goal"


def test_load_tasks_raises_on_duplicate_id():
    # Arrange — the DB dedups a duplicate id (INSERT OR REPLACE), so the fault
    # cannot be seeded; assert the same rule via the validator load_tasks uses.
    tasks = [
        {"id": "dup", "title": "One", "status": "done"},
        {"id": "dup", "title": "Two", "status": "done"},
    ]
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


#: WHY the two `bad_status` tests below are split but share this rationale:
#: tolerant read (2026-07-10 outage fix): an unknown status VALUE must never
#: take the whole store down; it may have been written by a newer agent.
#: Structural corruption (missing title, dup id) still raises. "Tolerant" is a
#: two-part contract — the loader must SHOUT about the value it does not know
#: AND still hand the row back — and silently dropping either half is what the
#: outage was. So each half is asserted on its own.
_BAD_STATUS_STORE = "tasks:\n  - {id: x, title: X, status: wibble}\n"


def test_load_tasks_warns_on_an_unknown_status_value(tmp_path):
    # Arrange
    store = _write(tmp_path, _BAD_STATUS_STORE)
    # Act
    ctx = pytest.warns(UserWarning, match="wibble")
    # Assert — the unknown value is shouted about, not swallowed.
    with ctx:
        load_tasks(store)


def test_load_tasks_still_returns_the_row_with_its_unknown_status(tmp_path):
    # Arrange
    store = _write(tmp_path, _BAD_STATUS_STORE)
    # Act
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        tasks = load_tasks(store)
    # Assert — the row survives, readable, with its unknown value intact.
    assert tasks[0]["status"] == "wibble"


def test_load_tasks_raises_on_missing_title():
    # Arrange — a NOT-NULL title cannot be seeded; test the validator directly.
    tasks = [{"id": "notitle", "status": "pending"}]
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_tasks_raises_on_missing_id():
    # Arrange
    tasks = [{"title": "No Id", "status": "pending"}]
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_tasks_raises_when_tasks_not_a_list():
    # Arrange — pass the non-list value directly, exactly what `tasks: not-a-list`
    # would have yielded as `data["tasks"]`.
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        _validate_tasks("not-a-list", source="x", strict=False)


def test_load_tasks_accepts_integer_priority(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done, priority: 3}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["priority"] == 3


def test_load_tasks_raises_on_non_integer_priority():
    # Arrange
    tasks = [{"id": "a", "title": "First", "status": "done", "priority": "high"}]
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_tasks_rejects_boolean_priority():
    # Arrange
    tasks = [{"id": "a", "title": "First", "status": "done", "priority": True}]
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_save_tasks_round_trip_preserves_priority(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done}\n",
    )
    tasks = load_tasks(store)
    tasks[0]["priority"] = 7
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)
    # Assert
    assert reloaded[0]["priority"] == 7


def test_save_tasks_round_trips_data_across_rewrite(tmp_path):
    # Contract CHANGE (fix/fast-store-write): the write path swapped the
    # slow ruamel round-trip (which preserved hand-written comments) for a
    # fast safe dump. Comments are INTENTIONALLY dropped — the store is
    # machine-managed. What MUST survive is the task DATA. This pins that.
    # Arrange — seed the canonical DB, then mutate + rewrite via the STORE path.
    from conftest import seed_db_from_doc

    seed_db_from_doc(
        {"tasks": [{"id": "a", "title": "First", "status": "done"}]},
        os.environ["SCITEX_CARDS_DB"],
    )
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    tasks = load_tasks(store)
    tasks[0]["priority"] = 1
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)
    # Assert — the mutated data round-trips exactly.
    assert reloaded == [{"id": "a", "title": "First", "status": "done", "priority": 1}]


def test_save_tasks_raises_on_bad_priority_type(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {id: a, title: First, status: done}\n")
    tasks = load_tasks(store)
    tasks[0]["priority"] = "high"
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        save_tasks(tasks, store)


def test_save_tasks_does_not_write_when_validation_fails(tmp_path):
    # Arrange
    # STRUCTURAL fault (missing title) still fails loud and writes nothing.
    # (A bad status VALUE now warns-and-writes — operator ruling 2026-07-10.)
    from conftest import seed_db_from_doc

    seed_db_from_doc(
        {"tasks": [{"id": "a", "title": "First", "status": "done"}]},
        os.environ["SCITEX_CARDS_DB"],
    )
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    bad = [{"id": "a", "status": "done"}]
    with contextlib.suppress(TaskValidationError):
        save_tasks(bad, store)
    # Act
    reloaded = load_tasks(store)
    # Assert — the failed save wrote nothing; the good doc is intact.
    assert reloaded == [{"id": "a", "title": "First", "status": "done"}]


def test_save_tasks_writes_fresh_store_when_absent(tmp_path):
    # Arrange — the canonical DB is bootstrapped empty; the first write
    # populates it (there is no file to pre-create).
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    tasks = [{"id": "a", "title": "First", "status": "pending", "priority": 1}]
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)
    # Assert
    assert reloaded[0]["id"] == "a"


_PARENT_STORE_TEXT = (
    "tasks:\n"
    "  - {id: hub, title: Hub, status: goal}\n"
    "  - {id: child, title: Child, status: pending, parent: hub}\n"
)


def test_load_tasks_reads_parent_id_on_child(tmp_path):
    # Arrange — additive-optional `parent` is a task-id string identifying the
    # node this task nests under (drill-down view follows this relation).
    store = _write(tmp_path, _PARENT_STORE_TEXT)
    # Act
    by_id = {t["id"]: t for t in load_tasks(store)}
    # Assert
    assert by_id["child"]["parent"] == "hub"


def test_load_tasks_leaves_parentless_task_without_parent(tmp_path):
    # Arrange
    store = _write(tmp_path, _PARENT_STORE_TEXT)
    # Act
    by_id = {t["id"]: t for t in load_tasks(store)}
    # Assert
    assert by_id["hub"].get("parent") is None


def test_load_tasks_treats_missing_parent_as_optional(tmp_path):
    # Arrange — pre-`parent` YAML must keep loading unchanged.
    store = _write(
        tmp_path,
        "tasks:\n  - {id: solo, title: Solo, status: pending}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert "parent" not in tasks[0]


def test_load_tasks_raises_on_non_string_parent():
    # Arrange — a non-string parent (here: an int) is a structural fault.
    tasks = [{"id": "a", "title": "First", "status": "done", "parent": 7}]
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_tasks_raises_on_empty_string_parent():
    # Arrange — explicit empty-string parent is ambiguous; reject so the
    # operator sees the typo rather than getting a silently top-level node.
    tasks = [{"id": "a", "title": "First", "status": "done", "parent": ""}]
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_save_tasks_round_trip_preserves_parent(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - {id: hub, title: Hub, status: goal}\n"
        "  - {id: child, title: Child, status: pending, parent: hub}\n",
    )
    tasks = load_tasks(store)
    # Act — touch an unrelated field and rewrite; `parent` must survive.
    for task in tasks:
        if task["id"] == "child":
            task["priority"] = 2
    save_tasks(tasks, store)
    reloaded = load_tasks(store)
    # Assert
    child = next(t for t in reloaded if t["id"] == "child")
    assert child["parent"] == "hub"


def test_load_tasks_treats_missing_comments_as_optional(tmp_path):
    # Arrange — pre-`comments` YAML must keep loading unchanged.
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert "comments" not in tasks[0]


def test_load_tasks_accepts_valid_comments(tmp_path):
    # Arrange — a comment with a non-empty text is valid.
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: First\n"
        "    status: done\n"
        "    comments:\n"
        "      - {ts: '2026-01-01T00:00:00+00:00', author: alice, text: hi}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["comments"][0]["text"] == "hi"


def test_load_tasks_raises_on_non_list_comments():
    # Arrange — comments must be a list, not a scalar.
    tasks = [{"id": "a", "title": "First", "status": "done", "comments": "nope"}]
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_tasks_raises_on_comment_missing_text():
    # Arrange — each comment needs a non-empty string `text`.
    tasks = [
        {
            "id": "a",
            "title": "First",
            "status": "done",
            "comments": [{"author": "alice"}],
        }
    ]
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


# --------------------------------------------------------------------------- #
# Phase 1 additions — scope / assignee / _log_meta validation                 #
# --------------------------------------------------------------------------- #
def test_load_accepts_scope(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: A\n"
        "    status: pending\n"
        "    scope: agent:proj-scitex-todo\n"
        "    assignee: agent:lead\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["scope"] == "agent:proj-scitex-todo"


def test_load_accepts_assignee(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: A\n"
        "    status: pending\n"
        "    scope: agent:proj-scitex-todo\n"
        "    assignee: agent:lead\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["assignee"] == "agent:lead"


def test_load_rejects_non_string_scope():
    # Arrange
    tasks = [{"id": "a", "title": "A", "status": "pending", "scope": 42}]
    # Act
    ctx = pytest.raises(TaskValidationError, match="non-string scope")
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_rejects_empty_string_assignee():
    # Arrange
    tasks = [{"id": "a", "title": "A", "status": "pending", "assignee": ""}]
    # Act
    ctx = pytest.raises(TaskValidationError, match="assignee")
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_accepts_log_meta_mapping(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: A\n"
        "    status: done\n"
        "    _log_meta:\n"
        "      completed_at: '2026-05-27T10:00:00Z'\n"
        "      completed_by: agent:test\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["_log_meta"]["completed_by"] == "agent:test"


def test_load_rejects_non_mapping_log_meta():
    # Arrange
    tasks = [{"id": "a", "title": "A", "status": "done", "_log_meta": "oops"}]
    # Act
    ctx = pytest.raises(TaskValidationError, match="_log_meta")
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_save_tasks_round_trip_preserves_log_meta_completed_by(tmp_path):
    """A done task's `_log_meta.completed_by` survives a save_tasks rewrite."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: A\n"
        "    status: done\n"
        "    _log_meta:\n"
        "      completed_at: '2026-05-27T10:00:00Z'\n"
        "      completed_by: agent:original\n",
    )
    tasks = load_tasks(store)
    tasks[0]["priority"] = 1
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    # Assert
    assert reloaded["_log_meta"]["completed_by"] == "agent:original"


def test_save_tasks_round_trip_preserves_log_meta_completed_at(tmp_path):
    """A done task's `_log_meta.completed_at` survives a save_tasks rewrite."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: A\n"
        "    status: done\n"
        "    _log_meta:\n"
        "      completed_at: '2026-05-27T10:00:00Z'\n"
        "      completed_by: agent:original\n",
    )
    tasks = load_tasks(store)
    tasks[0]["priority"] = 1
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    # Assert
    assert reloaded["_log_meta"]["completed_at"] == "2026-05-27T10:00:00Z"


# ---------------------------------------------------------------------------
# kind: Literal["task", "compute"] (north-star pillar #1 — compute-state deps)
# Closed validated enum per lead a2a `2c7a431d` — fail-loud on unknown values
# so a "comput" typo can't silently create an unrecognized kind.
# ---------------------------------------------------------------------------


def test_load_tasks_kind_defaults_to_task_when_absent(tmp_path):
    """Absence of `kind` is equivalent to `kind: task` (the default)."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: t1, title: Plain, status: pending}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert — the loader doesn't synthesize a value; downstream consumers
    # treat absence as "task".
    assert "kind" not in tasks[0]


def test_load_tasks_accepts_kind_task(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: t1, title: Plain, status: pending, kind: task}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["kind"] == "task"


_KIND_COMPUTE_FULL_YAML = (
    "tasks:\n"
    "  - id: spartan-pac\n"
    "    title: 'compute: PAC SLE multi-lane'\n"
    "    status: in_progress\n"
    "    kind: compute\n"
    "    job_id: '25754194'\n"
    "    host: spartan\n"
    "    command: srun -p h100 -t 12:00:00 python pac/sle.py\n"
    "    started_at: '2026-06-06T03:14:00Z'\n"
)


def test_load_tasks_kind_compute_persists_kind(tmp_path):
    # Arrange
    store = _write(tmp_path, _KIND_COMPUTE_FULL_YAML)
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["kind"] == "compute"


def test_load_tasks_kind_compute_persists_job_id(tmp_path):
    # Arrange
    store = _write(tmp_path, _KIND_COMPUTE_FULL_YAML)
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["job_id"] == "25754194"


def test_load_tasks_kind_compute_persists_host(tmp_path):
    # Arrange
    store = _write(tmp_path, _KIND_COMPUTE_FULL_YAML)
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["host"] == "spartan"


def test_load_tasks_kind_compute_persists_started_at(tmp_path):
    # Arrange
    store = _write(tmp_path, _KIND_COMPUTE_FULL_YAML)
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["started_at"] == "2026-06-06T03:14:00Z"


def test_load_tasks_raises_on_unknown_kind():
    """`comput` typo (or any value not in VALID_KINDS) is fail-loud."""
    # Arrange
    tasks = [{"id": "x", "title": "X", "status": "pending", "kind": "comput"}]
    # Act — match folds the raise + message-content check into one assertion.
    ctx = pytest.raises(TaskValidationError, match=r"comput.*compute|compute.*comput")
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_tasks_raises_on_compute_metadata_without_kind():
    """Setting job_id/host/etc. on a non-compute row is a config error.

    The lead's `kind` discriminator is what tells the writer-side watcher
    "this row is mine to update". Allowing compute metadata on a plain task
    would silently break that contract — fail-loud instead.
    """
    # Arrange
    tasks = [{"id": "x", "title": "X", "status": "pending", "job_id": "12345"}]
    # Act
    ctx = pytest.raises(TaskValidationError, match=r"job_id.*kind: compute")
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_tasks_raises_on_compute_metadata_with_kind_task():
    """`job_id` (a TRUE compute-only field) on a kind=task row fails-loud.

    Note: pre-PR-#57, `host` was also in the compute-only fence and was
    used as the example here. Per operator co-design TG 9667, `host` is
    now a generic field allowed on any row.
    """
    # Arrange
    tasks = [
        {"id": "x", "title": "X", "status": "pending", "kind": "task", "job_id": "42"}
    ]
    # Act
    ctx = pytest.raises(TaskValidationError, match="job_id")
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_tasks_allows_host_on_kind_task_row(tmp_path):
    """`host` is GENERIC (operator TG 9667) — allowed on any row, not just compute."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: pending, host: ywata-note-win}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert — no kind set + host present → valid; host is generic.
    assert tasks[0]["host"] == "ywata-note-win"


def test_load_tasks_raises_on_non_string_compute_field():
    # Arrange — job_id as an int (not a string) on a compute row.
    tasks = [
        {
            "id": "bad",
            "title": "bad",
            "status": "pending",
            "kind": "compute",
            "job_id": 25754194,
        }
    ]
    # Act
    ctx = pytest.raises(
        TaskValidationError, match=r"job_id.*non-string|non-string.*job_id"
    )
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


_COMPUTE_ROUND_TRIP_SETUP_YAML = (
    "# preserved header\n"
    "tasks:\n"
    "  - id: c1\n"
    "    title: 'compute: example'\n"
    "    status: in_progress\n"
    "    kind: compute\n"
    "    job_id: '99'\n"
    "    host: spartan\n"
    "    command: echo hi\n"
)


def _prepare_compute_store(tmp_path):
    """Arrange helper: write + load + mutate; returns (store_path, tasks)."""
    store = _write(tmp_path, _COMPUTE_ROUND_TRIP_SETUP_YAML)
    tasks = load_tasks(store)
    tasks[0]["status"] = "done"
    tasks[0]["finished_at"] = "2026-06-06T13:30:00Z"
    return store, tasks


def test_save_tasks_round_trip_preserves_kind_compute(tmp_path):
    # Arrange
    store, tasks = _prepare_compute_store(tmp_path)
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    # Assert
    assert reloaded["kind"] == "compute"


def test_save_tasks_round_trip_preserves_job_id(tmp_path):
    # Arrange
    store, tasks = _prepare_compute_store(tmp_path)
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    # Assert
    assert reloaded["job_id"] == "99"


def test_save_tasks_round_trip_writes_finished_at(tmp_path):
    # Arrange
    store, tasks = _prepare_compute_store(tmp_path)
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    # Assert
    assert reloaded["finished_at"] == "2026-06-06T13:30:00Z"


# ---------------------------------------------------------------------------
# kind="decision" — decision-nodes are first-class graph nodes (north-star
# pillar #4, operator TG 9524). Extends VALID_KINDS from ADR-0002.
# ---------------------------------------------------------------------------


def test_load_tasks_accepts_kind_decision(tmp_path):
    """`kind: decision` is a valid kind alongside task / compute."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: decide-x\n"
        "    title: 'decide: X'\n"
        "    status: pending\n"
        "    kind: decision\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["kind"] == "decision"


def test_load_tasks_decision_kind_uses_existing_statuses(tmp_path):
    """A decision-node's lifecycle uses VALID_STATUSES (pending → done)."""
    # Arrange — pending decision awaiting resolution.
    store = _write(
        tmp_path,
        "tasks:\n  - {id: d1, title: 'decide: a/b', status: pending, kind: decision}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# blocker: Literal["compute", "dep", "operator-decision", "agent-wait"]
# Operator TG 9522/9524, lead a2a 4691b114/c839c59b/2bd37bd2/554435df.
# ADR-0004: closed validated enum, fail-loud, only on status=blocked rows.
# ---------------------------------------------------------------------------


def test_load_tasks_accepts_blocker_operator_decision_on_blocked(tmp_path):
    """`blocker: operator-decision` valid on a status=blocked task."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: blocked, blocker: operator-decision}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["blocker"] == "operator-decision"


def test_load_tasks_accepts_all_four_blocker_variants(tmp_path):
    """The four operator-named blocker variants each load cleanly."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - {id: a, title: A, status: blocked, blocker: compute}\n"
        "  - {id: b, title: B, status: blocked, blocker: dep}\n"
        "  - {id: c, title: C, status: blocked, blocker: operator-decision}\n"
        "  - {id: d, title: D, status: blocked, blocker: agent-wait}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert [t["blocker"] for t in tasks] == [
        "compute",
        "dep",
        "operator-decision",
        "agent-wait",
    ]


def test_load_tasks_raises_on_unknown_blocker():
    """A typo (or any value not in VALID_BLOCKERS) is fail-loud."""
    # Arrange
    tasks = [{"id": "x", "title": "X", "status": "blocked", "blocker": "oprator"}]
    # Act
    ctx = pytest.raises(
        TaskValidationError,
        match=r"oprator.*operator-decision|operator-decision.*oprator",
    )
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_tasks_raises_on_blocker_with_non_blocked_status():
    """Naming a blocker on a non-blocked task is a config error."""
    # Arrange
    tasks = [
        {
            "id": "x",
            "title": "X",
            "status": "in_progress",
            "blocker": "operator-decision",
        }
    ]
    # Act
    ctx = pytest.raises(
        TaskValidationError, match=r"blocker.*status: blocked|status: blocked.*blocker"
    )
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_tasks_blocker_absent_on_blocked_is_acceptable(tmp_path):
    """A blocked task without a `blocker` field is still valid (soft-degrade)."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: blocked}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert "blocker" not in tasks[0]


_ORTHOGONAL_KIND_BLOCKER_YAML = (
    "tasks:\n"
    "  - id: d1\n"
    "    title: 'decide: model-picks-a-or-b'\n"
    "    status: blocked\n"
    "    kind: decision\n"
    "    blocker: compute\n"  # decision blocked on a model run; unusual but legal
)


def test_load_tasks_orthogonal_enums_persist_kind_decision(tmp_path):
    """A kind=decision row can have ANY blocker (validator-orthogonal)."""
    # Arrange
    store = _write(tmp_path, _ORTHOGONAL_KIND_BLOCKER_YAML)
    # Act
    t = load_tasks(store)[0]
    # Assert
    assert t["kind"] == "decision"


def test_load_tasks_orthogonal_enums_persist_blocker_compute(tmp_path):
    """Companion to the kind-decision test — confirms blocker independently."""
    # Arrange
    store = _write(tmp_path, _ORTHOGONAL_KIND_BLOCKER_YAML)
    # Act
    t = load_tasks(store)[0]
    # Assert
    assert t["blocker"] == "compute"


_DECISION_ROUND_TRIP_YAML = (
    "# preserved\n"
    "tasks:\n"
    "  - id: decide-hub-cutover\n"
    "    title: 'decide: hub prod-cutover final GO'\n"
    "    status: blocked\n"
    "    kind: decision\n"
    "    blocker: operator-decision\n"
)


def _prepare_decision_store(tmp_path):
    """Arrange helper: write + load + mutate; returns (store_path, tasks)."""
    store = _write(tmp_path, _DECISION_ROUND_TRIP_YAML)
    tasks = load_tasks(store)
    tasks[0]["status"] = "done"  # operator decided
    tasks[0].pop("blocker")  # no longer blocked
    return store, tasks


def test_save_tasks_round_trip_decision_flips_status_to_done(tmp_path):
    # Arrange
    store, tasks = _prepare_decision_store(tmp_path)
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    # Assert
    assert reloaded["status"] == "done"


def test_save_tasks_round_trip_decision_drops_blocker(tmp_path):
    # Arrange
    store, tasks = _prepare_decision_store(tmp_path)
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    # Assert
    assert "blocker" not in reloaded


def test_save_tasks_round_trip_decision_preserves_kind(tmp_path):
    # Arrange
    store, tasks = _prepare_decision_store(tmp_path)
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    # Assert
    assert reloaded["kind"] == "decision"


# ===========================================================================
# Task dataclass — the SINGLE schema source (ADR-0007 / quality-hygiene PR)
# Operator co-design TG 9667 + lead a2a `6d9b6073` + `a62db48c`.
# ===========================================================================


_MIN_TASK_PAYLOAD = {"id": "x", "title": "X"}


def test_task_dataclass_from_dict_carries_id():
    # Arrange
    from scitex_cards._model import Task

    # Act
    t = Task.from_dict(_MIN_TASK_PAYLOAD)
    # Assert
    assert t.id == "x"


def test_task_dataclass_from_dict_carries_title():
    # Arrange
    from scitex_cards._model import Task

    # Act
    t = Task.from_dict(_MIN_TASK_PAYLOAD)
    # Assert
    assert t.title == "X"


def test_task_dataclass_from_dict_defaults_status_to_deferred():
    # Arrange — `deferred` replaced the abolished `pending` as the default.
    from scitex_cards._model import Task

    # Act
    t = Task.from_dict(_MIN_TASK_PAYLOAD)
    # Assert
    assert t.status == "deferred"


def test_task_dataclass_from_dict_defaults_comments_to_empty_list():
    # Arrange
    from scitex_cards._model import Task

    # Act
    t = Task.from_dict(_MIN_TASK_PAYLOAD)
    # Assert
    assert t.comments == []


_OPERATOR_FIELDS_PAYLOAD = {
    "id": "x",
    "title": "X",
    "task": "the BIG line",
    "project": "scitex-todo",
    "host": "ywata-note-win",
    "created_at": "2026-06-07T01:00:00Z",
    "goal": "make the board the fleet's shared SSoT",
    "agent": "proj-scitex-todo",
    "last_activity": "12s ago",
    "pr_url": "https://github.com/ywatanabe1989/scitex-todo/pull/54",
    "issue_url": "https://github.com/ywatanabe1989/scitex-agent-container/issues/324",
}


def test_task_dataclass_from_dict_carries_task_field():
    # Arrange
    from scitex_cards._model import Task

    # Act
    t = Task.from_dict(_OPERATOR_FIELDS_PAYLOAD)
    # Assert
    assert t.task == "the BIG line"


def test_task_dataclass_from_dict_carries_project_field():
    # Arrange
    from scitex_cards._model import Task

    # Act
    t = Task.from_dict(_OPERATOR_FIELDS_PAYLOAD)
    # Assert
    assert t.project == "scitex-todo"


def test_task_dataclass_from_dict_carries_host_field():
    # Arrange
    from scitex_cards._model import Task

    # Act
    t = Task.from_dict(_OPERATOR_FIELDS_PAYLOAD)
    # Assert
    assert t.host == "ywata-note-win"


def test_task_dataclass_from_dict_carries_goal_field():
    # Arrange
    from scitex_cards._model import Task

    # Act
    t = Task.from_dict(_OPERATOR_FIELDS_PAYLOAD)
    # Assert
    assert t.goal == "make the board the fleet's shared SSoT"


def test_task_dataclass_from_dict_carries_pr_url_field():
    # Arrange
    from scitex_cards._model import Task

    # Act
    t = Task.from_dict(_OPERATOR_FIELDS_PAYLOAD)
    # Assert
    assert t.pr_url.endswith("/pull/54")


def test_task_dataclass_from_dict_ignores_unknown_keys():
    # Arrange
    from scitex_cards._model import Task

    # Act — unknown `future_field` must not raise (forward-compat).
    t = Task.from_dict({"id": "x", "title": "X", "future_field": "ok"})
    # Assert
    assert t.id == "x"


def test_task_dataclass_from_dict_normalizes_legacy_dep_to_dependency():
    """Legacy `blocker: "dep"` → canonical `"dependency"` on dataclass read."""
    # Arrange
    from scitex_cards._model import Task

    # Act
    t = Task.from_dict({"id": "x", "title": "X", "status": "blocked", "blocker": "dep"})
    # Assert
    assert t.blocker == "dependency"


def test_task_dataclass_to_dict_omits_default_fields():
    # Arrange
    from scitex_cards._model import Task

    t = Task(id="x", title="X")
    # Act
    d = t.to_dict()
    # Assert
    assert d == {"id": "x", "title": "X", "status": "deferred"}


def test_task_dataclass_to_dict_omits_empty_depends_on(tmp_path):
    # Arrange
    from scitex_cards._model import Task

    t = Task(id="x", title="X", depends_on=[], blocks=[], comments=[])
    # Act
    d = t.to_dict()
    # Assert
    assert "depends_on" not in d


def test_task_dataclass_to_dict_omits_empty_blocks(tmp_path):
    # Arrange
    from scitex_cards._model import Task

    t = Task(id="x", title="X", depends_on=[], blocks=[], comments=[])
    # Act
    d = t.to_dict()
    # Assert
    assert "blocks" not in d


def test_task_dataclass_to_dict_omits_empty_comments(tmp_path):
    # Arrange
    from scitex_cards._model import Task

    t = Task(id="x", title="X", depends_on=[], blocks=[], comments=[])
    # Act
    d = t.to_dict()
    # Assert
    assert "comments" not in d


_ROUND_TRIP_PAYLOAD = {
    "id": "x",
    "title": "X",
    "task": "do the thing",
    "project": "scitex-todo",
    "host": "ywata",
    "agent": "proj-scitex-todo",
    "status": "blocked",
    "blocker": "operator-decision",
    "goal": "ship the board",
    "depends_on": ["a", "b"],
    "tags": ["P0", "infra"],  # unknown key, gets dropped
}


def test_task_dataclass_round_trip_preserves_task_field():
    # Arrange
    from scitex_cards._model import Task

    payload = _ROUND_TRIP_PAYLOAD
    # Act
    d = Task.from_dict(payload).to_dict()
    # Assert
    assert d["task"] == "do the thing"


def test_task_dataclass_round_trip_preserves_status():
    # Arrange
    from scitex_cards._model import Task

    payload = _ROUND_TRIP_PAYLOAD
    # Act
    d = Task.from_dict(payload).to_dict()
    # Assert
    assert d["status"] == "blocked"


def test_task_dataclass_round_trip_preserves_blocker():
    # Arrange
    from scitex_cards._model import Task

    payload = _ROUND_TRIP_PAYLOAD
    # Act
    d = Task.from_dict(payload).to_dict()
    # Assert
    assert d["blocker"] == "operator-decision"


def test_task_dataclass_round_trip_preserves_depends_on():
    # Arrange
    from scitex_cards._model import Task

    payload = _ROUND_TRIP_PAYLOAD
    # Act
    d = Task.from_dict(payload).to_dict()
    # Assert
    assert d["depends_on"] == ["a", "b"]


def test_task_dataclass_round_trip_drops_unknown_tags_field():
    # Arrange
    from scitex_cards._model import Task

    payload = _ROUND_TRIP_PAYLOAD
    # Act
    d = Task.from_dict(payload).to_dict()
    # Assert
    assert "tags" not in d


# ---------------------------------------------------------------------------
# `dependency` enum rename + `none` value (operator co-design TG 9667)
# ---------------------------------------------------------------------------


def test_load_tasks_accepts_canonical_dependency_blocker(tmp_path):
    """`blocker: "dependency"` (the canonical spelling) loads cleanly."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: blocked, blocker: dependency}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["blocker"] == "dependency"


def test_load_tasks_still_accepts_legacy_dep_blocker(tmp_path):
    """Legacy `blocker: "dep"` is still accepted during the deprecation window.

    Validator passes; the dict still carries "dep". The Task dataclass
    normalizes on read; legacy writers that go through save_tasks without
    converting still produce "dep" until they migrate.
    """
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: blocked, blocker: dep}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["blocker"] == "dep"


def test_load_tasks_accepts_none_blocker(tmp_path):
    """`blocker: "none"` explicitly says "we looked, no blocker named" — distinct from absent."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: blocked, blocker: none}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["blocker"] == "none"


# ---------------------------------------------------------------------------
# New operator-co-designed fields — additive validators only.
# ---------------------------------------------------------------------------


def _all_operator_fields_yaml() -> str:
    return (
        "tasks:\n"
        "  - id: x\n"
        "    title: X\n"
        "    status: pending\n"
        "    task: 'PR #54 in CI'\n"
        "    project: scitex-todo\n"
        "    host: ywata-note-win\n"
        "    created_at: '2026-06-07T01:00:00Z'\n"
        "    goal: ship the board\n"
        "    agent: proj-scitex-todo\n"
        "    last_activity: '12s ago'\n"
        "    pr_url: https://github.com/ywatanabe1989/scitex-todo/pull/54\n"
        "    issue_url: https://github.com/ywatanabe1989/scitex-agent-container/issues/324\n"
    )


def test_load_tasks_accepts_new_operator_field_task(tmp_path):
    """`task` (BIG board-card text) loads cleanly when present."""
    # Arrange
    store = _write(tmp_path, _all_operator_fields_yaml())
    # Act
    t = load_tasks(store)[0]
    # Assert
    assert t["task"] == "PR #54 in CI"


def test_load_tasks_accepts_new_operator_field_host(tmp_path):
    """`host` (where the work happens) loads cleanly when present."""
    # Arrange
    store = _write(tmp_path, _all_operator_fields_yaml())
    # Act
    t = load_tasks(store)[0]
    # Assert
    assert t["host"] == "ywata-note-win"


def test_load_tasks_raises_on_non_string_task_field():
    """`task: 123` (int) fails-loud with a message naming the bad field."""
    # Arrange
    tasks = [{"id": "x", "title": "X", "status": "pending", "task": 123}]
    # Act
    ctx = pytest.raises(TaskValidationError, match=r"task.*non-string")
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_tasks_raises_on_non_string_pr_url():
    """`pr_url: 12345` (int) fails-loud — URL must be a string."""
    # Arrange
    tasks = [{"id": "x", "title": "X", "status": "pending", "pr_url": 12345}]
    # Act
    ctx = pytest.raises(TaskValidationError, match="pr_url")
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_load_tasks_raises_on_empty_goal_string():
    """`goal: ""` (empty string) fails-loud — non-empty rule."""
    # Arrange
    tasks = [{"id": "x", "title": "X", "status": "pending", "goal": ""}]
    # Act
    ctx = pytest.raises(TaskValidationError, match="goal")
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


# ---------------------------------------------------------------------------
# kind="status" — non-actionable status-tracking rows (e.g. q-* quality-CI
# cards). Per board card `scitex-todo-relocate-q-status-tracking` + lead
# a2a `60a1a93d`: option (b) — flag the rows with this axis so the board
# can filter them out of the actionable default lens (separate frontend
# PR). Just a flag — no compute-fields constraint, no cross-imply.
# ---------------------------------------------------------------------------


def test_load_tasks_accepts_kind_status(tmp_path):
    """`kind: status` is a valid kind alongside task / compute / decision."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: q-gen\n"
        "    title: 'q-gen quality status'\n"
        "    status: pending\n"
        "    kind: status\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["kind"] == "status"


def test_load_tasks_kind_status_requires_no_compute_fields(tmp_path):
    """`kind: status` rows carry NO compute fields and load cleanly."""
    # Arrange — only the bare flag, no job_id / command / *_at.
    store = _write(
        tmp_path,
        "tasks:\n  - {id: q-io, title: 'q-io status', status: pending, kind: status}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert "job_id" not in tasks[0]


def test_load_tasks_kind_status_rejects_compute_fields():
    """A `kind: status` row with a compute-only field fails-loud."""
    # Arrange — job_id is compute-only; pairing it with kind=status is a typo.
    tasks = [
        {
            "id": "q-ml",
            "title": "q-ml status",
            "status": "pending",
            "kind": "status",
            "job_id": "42",
        }
    ]
    # Act
    ctx = pytest.raises(TaskValidationError, match=r"job_id.*kind: compute")
    # Assert
    with ctx:
        _validate_tasks(tasks, source="x", strict=False)


def test_save_tasks_round_trip_preserves_kind_status(tmp_path):
    """`kind: status` survives a save → reload round-trip."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: q-plt, title: 'q-plt status', status: pending, kind: status}\n",
    )
    tasks = load_tasks(store)
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    # Assert
    assert reloaded["kind"] == "status"


# EOF
