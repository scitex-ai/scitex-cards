#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the POST /reopen handler — the board-v3 Undo affordance.

Mirrors ``src/scitex_cards/_django/handlers/crud.py::handle_reopen``. Real
``RequestFactory`` POST against the canonical SQLite store (no mocks, STX-NM /
PA-306), verifying:

  - reopen restores status to the supplied prior_status (default 'blocked')
  - reopen restores blocker (default 'operator-decision' when status=blocked)
  - blocker is dropped when restoring to a non-blocked status (schema rule)
  - reopen appends an [UNDONE] comments[] entry with actor + prior state
  - the store round-trips clean through save_tasks (validator-enforced)
  - 404 unknown id / 400 missing id / 405 GET
  - reopen→resolve→reopen cycle works (safety net stays usable across loops)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("django")

# ``seed_db_from_doc`` lives in the SHARED tests/scitex_cards/conftest.py, but
# this file sits under _django/, whose own conftest.py shadows the bare
# ``conftest`` module name — ``from conftest import seed_db_from_doc`` binds that
# sibling (which lacks the helper) and raises ImportError. Load the shared
# conftest deterministically by path instead. (Applies to every _django/** test.)
import importlib.util as _ilu  # noqa: E402

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402
from scitex_cards._model import load_tasks  # noqa: E402
from scitex_cards._yaml import safe_load  # noqa: E402

_shared_conftest = Path(__file__).resolve().parents[2] / "conftest.py"
_spec = _ilu.spec_from_file_location("_scitex_cards_shared_conftest", _shared_conftest)
_mod = _ilu.module_from_spec(_spec)
# Register BEFORE exec: the shared conftest defines a @dataclass, and on py3.12
# the dataclass machinery looks the module up in sys.modules while executing.
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
seed_db_from_doc = _mod.seed_db_from_doc

_STORE_TEXT = (
    "tasks:\n"
    "  - id: decide-clew-a-b\n"
    "    title: 'decide: clew (a)/(b) DAG depth'\n"
    "    status: done\n"
    "    kind: decision\n"
    "    agent: proj-paper-scitex-clew\n"
    "    project: paper-scitex-clew\n"
    "  - id: was-pending\n"
    "    title: A task that was pending before being resolved\n"
    "    status: done\n"
    "  - id: still-blocked\n"
    "    title: A task that is currently blocked\n"
    "    status: blocked\n"
    "    blocker: operator-decision\n"
    "    kind: decision\n"
    "    agent: proj-x\n"
)


@pytest.fixture
def store():
    # SQLite store: seed the prior cards into the canonical DB, then hand the
    # handler the PINNED store-identity path (never a tmp_path YAML — a write
    # stamped with a tmp path fails the next read's ownership check). The DB is
    # authoritative for content; the handler ignores the path except as a
    # provenance label. The board/services layer (get_board -> load_groups)
    # still stat()s the identity file, so it must EXIST even though its content
    # is never read — an empty file is enough.
    seed_db_from_doc(safe_load(_STORE_TEXT) or {}, os.environ["SCITEX_CARDS_DB"])
    store_path = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    Path(store_path).write_text("", encoding="utf-8")
    _reset_cache()
    yield store_path
    _reset_cache()


def _post(endpoint, store_path, body):
    request = RequestFactory().post(
        f"/{endpoint}?store={store_path}",
        data=json.dumps(body),
        content_type="application/json",
    )
    return views.api_dispatch(request, endpoint)


def _load(store_path):
    # Read back through the canonical store (SQLite); the path is a label only.
    return {t["id"]: t for t in load_tasks(store_path)}


def _run_resolve_reopen_cycle_twice(store_path, task_id):
    """Helper for the cycle tests: resolve→reopen→resolve→reopen.

    Returns the final loaded row so per-assertion test functions can
    each check one fact about the final state.
    """
    _post("resolve", store_path, {"id": task_id, "actor": "operator"})
    _post("reopen", store_path, {"id": task_id, "actor": "operator"})
    _post("resolve", store_path, {"id": task_id, "actor": "operator"})
    _post("reopen", store_path, {"id": task_id, "actor": "operator"})
    return _load(store_path)[task_id]


# === Defaults restore the "blocked + operator-decision" state ================


def test_reopen_returns_200(store):
    # Arrange
    body = {"id": "decide-clew-a-b", "actor": "operator"}
    # Act
    resp = _post("reopen", store, body)
    # Assert
    assert resp.status_code == 200


def test_reopen_default_status_is_blocked(store):
    # Arrange
    body = {"id": "decide-clew-a-b", "actor": "operator"}
    # Act
    _post("reopen", store, body)
    # Assert
    assert _load(store)["decide-clew-a-b"]["status"] == "blocked"


def test_reopen_default_blocker_is_operator_decision(store):
    # Arrange
    body = {"id": "decide-clew-a-b", "actor": "operator"}
    # Act
    _post("reopen", store, body)
    # Assert
    assert _load(store)["decide-clew-a-b"]["blocker"] == "operator-decision"


# === Caller-supplied prior_status / prior_blocker ============================


def test_reopen_honors_caller_prior_status(store):
    # Arrange
    body = {"id": "was-pending", "actor": "operator", "prior_status": "pending"}
    # Act
    _post("reopen", store, body)
    # Assert
    assert _load(store)["was-pending"]["status"] == "pending"


def test_reopen_drops_blocker_when_restoring_to_non_blocked(store):
    """Schema disallows blocker on non-blocked rows; restoring to
    status=pending must omit blocker even if the caller passed one.
    """
    # Arrange
    body = {
        "id": "was-pending",
        "actor": "operator",
        "prior_status": "pending",
        "prior_blocker": "operator-decision",
    }
    # Act
    _post("reopen", store, body)
    # Assert
    assert "blocker" not in _load(store)["was-pending"]


def test_reopen_uses_caller_prior_blocker_when_restoring_to_blocked(store):
    # Arrange
    body = {
        "id": "decide-clew-a-b",
        "actor": "operator",
        "prior_status": "blocked",
        "prior_blocker": "dependency",
    }
    # Act
    _post("reopen", store, body)
    # Assert
    assert _load(store)["decide-clew-a-b"]["blocker"] == "dependency"


# === Comment trail ===========================================================


def test_reopen_appends_undone_comment_with_actor(store):
    # Arrange
    body = {"id": "decide-clew-a-b", "actor": "operator"}
    # Act
    _post("reopen", store, body)
    # Assert
    assert _load(store)["decide-clew-a-b"]["comments"][-1]["author"] == "operator"


def test_reopen_comment_text_names_undone(store):
    # Arrange
    body = {"id": "decide-clew-a-b", "actor": "operator"}
    # Act
    _post("reopen", store, body)
    text = _load(store)["decide-clew-a-b"]["comments"][-1]["text"]
    # Assert
    assert "UNDONE" in text


def test_reopen_defaults_actor_when_omitted(store):
    # Arrange
    body = {"id": "decide-clew-a-b"}
    # Act
    _post("reopen", store, body)
    # Assert — actor falls back to $USER (or 'operator'); never blank.
    assert _load(store)["decide-clew-a-b"]["comments"][-1]["author"]


# === Error paths =============================================================


def test_reopen_404_on_unknown_id(store):
    # Arrange
    body = {"id": "nope-not-there"}
    # Act
    resp = _post("reopen", store, body)
    # Assert
    assert resp.status_code == 404


def test_reopen_400_on_missing_id(store):
    # Arrange
    body = {}
    # Act
    resp = _post("reopen", store, body)
    # Assert
    assert resp.status_code == 400


def test_reopen_405_on_get(store):
    # Arrange
    request = RequestFactory().get(f"/reopen?store={store}")
    # Act
    resp = views.api_dispatch(request, "reopen")
    # Assert
    assert resp.status_code == 405


# === Round-trip safety + cycle ===============================================


def test_reopen_yaml_round_trips_clean(store):
    """After reopen, the YAML must reload cleanly through the validator;
    we confirm by then resolving the row again."""
    # Arrange
    _post("reopen", store, {"id": "decide-clew-a-b", "actor": "operator"})
    # Act
    resp = _post("resolve", store, {"id": "decide-clew-a-b", "actor": "operator"})
    # Assert
    assert resp.status_code == 200


def test_resolve_reopen_cycle_final_status_is_blocked(store):
    """resolve -> reopen -> resolve -> reopen leaves status=blocked."""
    # Arrange
    task_id = "still-blocked"
    # Act
    final = _run_resolve_reopen_cycle_twice(store, task_id)
    # Assert
    assert final["status"] == "blocked"


def test_resolve_reopen_cycle_final_blocker_is_operator_decision(store):
    """After the cycle, the default blocker is restored on the final reopen."""
    # Arrange
    task_id = "still-blocked"
    # Act
    final = _run_resolve_reopen_cycle_twice(store, task_id)
    # Assert
    assert final["blocker"] == "operator-decision"


def test_resolve_reopen_cycle_appends_one_comment_per_call(store):
    """4 endpoint calls -> 4 comment entries (each one append-only)."""
    # Arrange
    task_id = "still-blocked"
    # Act
    final = _run_resolve_reopen_cycle_twice(store, task_id)
    # Assert
    assert len(final["comments"]) == 4


def test_reopen_response_carries_restored_status(store):
    # Arrange
    body = {"id": "decide-clew-a-b", "actor": "operator"}
    # Act
    resp = _post("reopen", store, body)
    parsed = json.loads(resp.content)
    # Assert
    assert parsed["status"] == "blocked"


def test_reopen_response_carries_restored_blocker(store):
    # Arrange
    body = {"id": "decide-clew-a-b", "actor": "operator"}
    # Act
    resp = _post("reopen", store, body)
    parsed = json.loads(resp.content)
    # Assert
    assert parsed["blocker"] == "operator-decision"


def test_reopen_response_carries_id(store):
    # Arrange
    body = {"id": "decide-clew-a-b", "actor": "operator"}
    # Act
    resp = _post("reopen", store, body)
    parsed = json.loads(resp.content)
    # Assert
    assert parsed["id"] == "decide-clew-a-b"


# EOF
