#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the POST /resolve handler — operator's Resolve→notify GUI→code wire.

Mirrors ``src/scitex_cards/_django/handlers/resolve.py::handle_resolve``. Real
``RequestFactory`` POST against the canonical SQLite store (no mocks, STX-NM /
PA-306), verifying:

  - status flips to "done" + blocker is removed
  - a comments[] entry recording the resolution is appended (with actor)
  - idempotent on already-resolved tasks (200 noop)
  - 404 on unknown id
  - the store round-trips clean through save_tasks (validator-enforced)
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
    "  - id: decide-hub-go\n"
    "    title: 'decide: hub prod-cutover GO'\n"
    "    status: blocked\n"
    "    kind: decision\n"
    "    blocker: operator-decision\n"
    "    agent: proj-scitex-hub\n"
    "    project: scitex-hub\n"
    "  - id: hub-cutover-tasks\n"
    "    title: hub cutover execution\n"
    "    status: blocked\n"
    "    depends_on: [decide-hub-go]\n"
    "  - id: already-done\n"
    "    title: Already Done\n"
    "    status: done\n"
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


# === Resolve flips status + clears blocker ===================================


def test_resolve_returns_200(store):
    # Arrange
    body = {"id": "decide-hub-go", "actor": "operator"}
    # Act
    resp = _post("resolve", store, body)
    # Assert
    assert resp.status_code == 200


def test_resolve_flips_status_to_done(store):
    # Arrange
    body = {"id": "decide-hub-go", "actor": "operator"}
    # Act
    _post("resolve", store, body)
    # Assert
    assert _load(store)["decide-hub-go"]["status"] == "done"


def test_resolve_removes_blocker_field(store):
    # Arrange
    body = {"id": "decide-hub-go", "actor": "operator"}
    # Act
    _post("resolve", store, body)
    # Assert
    assert "blocker" not in _load(store)["decide-hub-go"]


def test_resolve_appends_comment_with_actor(store):
    # Arrange
    body = {"id": "decide-hub-go", "actor": "operator"}
    # Act
    _post("resolve", store, body)
    # Assert
    assert _load(store)["decide-hub-go"]["comments"][-1]["author"] == "operator"


def test_resolve_comment_text_contains_resolved_keyword(store):
    # Arrange
    body = {"id": "decide-hub-go", "actor": "operator"}
    # Act
    _post("resolve", store, body)
    text = _load(store)["decide-hub-go"]["comments"][-1]["text"]
    # Assert
    assert "RESOLVED" in text


def test_resolve_comment_text_names_the_prior_blocker(store):
    # Arrange
    body = {"id": "decide-hub-go", "actor": "operator"}
    # Act
    _post("resolve", store, body)
    text = _load(store)["decide-hub-go"]["comments"][-1]["text"]
    # Assert
    assert "operator-decision" in text


# === Idempotent on already-resolved =========================================


def test_resolve_is_noop_on_already_done_task(store):
    # Arrange
    body = {"id": "already-done"}
    # Act
    resp = _post("resolve", store, body)
    # Assert
    assert resp.status_code == 200


def test_resolve_noop_response_carries_flag(store):
    # Arrange
    body = {"id": "already-done"}
    # Act
    resp = _post("resolve", store, body)
    parsed = json.loads(resp.content)
    # Assert
    assert parsed.get("noop") is True


def test_resolve_does_not_append_comment_on_noop(store):
    # Arrange
    body = {"id": "already-done"}
    # Act
    _post("resolve", store, body)
    row = _load(store)["already-done"]
    # Assert — already-done task started with no comments; noop must not add one.
    assert not row.get("comments")


# === Error paths ============================================================


def test_resolve_404_on_unknown_id(store):
    # Arrange
    body = {"id": "nope-not-there"}
    # Act
    resp = _post("resolve", store, body)
    # Assert
    assert resp.status_code == 404


def test_resolve_400_on_missing_id(store):
    # Arrange
    body = {}
    # Act
    resp = _post("resolve", store, body)
    # Assert
    assert resp.status_code == 400


def test_resolve_405_on_get(store):
    # Arrange
    request = RequestFactory().get(f"/resolve?store={store}")
    # Act
    resp = views.api_dispatch(request, "resolve")
    # Assert
    assert resp.status_code == 405


# === Round-trip safety ======================================================


def test_resolve_yaml_round_trips_clean(store):
    """After resolve, the YAML must reload cleanly through validator."""
    # Arrange — first resolve flips decide-hub-go; the dependent's
    # post-resolve YAML must round-trip cleanly enough that a second
    # resolve on its dependent succeeds.
    _post("resolve", store, {"id": "decide-hub-go", "actor": "operator"})
    # Act
    resp = _post("resolve", store, {"id": "hub-cutover-tasks", "actor": "operator"})
    # Assert
    assert resp.status_code == 200


def test_resolve_defaults_actor_when_omitted(store):
    """Actor falls back to $USER (or 'operator'); never blank."""
    # Arrange
    body = {"id": "decide-hub-go"}
    # Act
    _post("resolve", store, body)
    # Assert
    assert _load(store)["decide-hub-go"]["comments"][-1]["author"]
