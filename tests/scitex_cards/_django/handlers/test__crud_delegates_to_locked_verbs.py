#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The GUI write handlers must not lose concurrent writes (lock-bypass fix).

Until 2026-07-17 the board's write handlers (update / delete / restore / edge
/ resolve / reopen / priority / archive) read ``board.tasks`` — a
request-scoped CACHE — mutated it in memory, then saved the WHOLE list back
with no flock across the read-modify-write. Any concurrent ``_store`` write
landing between the cache read and the save was silently clobbered (lost
update). ``handle_create`` / ``handle_comment`` already delegated to the
locked ``_store`` verbs and were never affected.

This file pins the fix three ways:

1. LOST-UPDATE survival — per handler: hand the handler a deliberately STALE
   ``BoardState``, land a concurrent ``add_task`` write, then assert the
   handler's write did NOT erase the concurrent card (the old code did).
2. DELEGATION spies — ``handle_update`` -> ``update_task`` (with the GUI's
   None/""/[]-clears translated to the verb's None-deletes) and
   ``handle_edge`` -> ``set_edge`` (with the GUI's ``depends_on``
   source/target SWAPPED — the two surfaces hang the field on opposite ends).
3. EDGE-ORIENTATION on-disk parity — for both kinds x both actions the field
   lands exactly where the old handler put it.

One assertion per test (STX-TQ007): the spy installers below keep each
scenario's arrange in a single place.
"""

from __future__ import annotations

import json

import pytest
import yaml

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers import (  # noqa: E402
    crud,
    priority,
    reopen,
    resolve,
    stale,
    undo,
)
from scitex_cards._django.handlers import edge as edge_handlers  # noqa: E402
from scitex_cards._django.services import _reset_cache, get_board  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: north, title: North Star, status: goal,"
    " agent: alice, assignee: alice}\n"
    "  - {id: build, title: Build It, status: in_progress, parent: north,"
    " note: keep, agent: alice, assignee: alice}\n"
    "  - {id: gate, title: Gate, status: blocked, blocker: operator-decision,"
    " depends_on: [build], agent: bob, assignee: bob}\n"
    "  - {id: done-card, title: Done Card, status: done}\n"
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Hermetic: no per-project lane union from the real ~/proj tree.
    monkeypatch.setenv("SCITEX_TODO_LANE_GLOBS", "")
    path = tmp_path / "tasks.yaml"
    path.write_text(_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


def _post(endpoint, store_path, body):
    request = RequestFactory().post(
        f"/{endpoint}?store={store_path}",
        data=json.dumps(body),
        content_type="application/json",
    )
    return views.api_dispatch(request, endpoint)


def _request(endpoint, body):
    return RequestFactory().post(
        f"/{endpoint}",
        data=json.dumps(body),
        content_type="application/json",
    )


def _load(store_path):
    with open(store_path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return {t["id"]: t for t in data["tasks"]}


def _stale_board(store_path):
    """A BoardState snapshot that will NOT see writes landing after it."""
    _reset_cache()
    board = get_board(store_path)
    _reset_cache()
    return board


def _land_concurrent_write(store_path):
    """A concurrent MCP/CLI writer inserting a card via the locked verb."""
    from scitex_cards._store import add_task

    add_task(
        store_path,
        id="concurrent",
        title="Concurrent Card",
        status="deferred",
        assignee="carol",
        created_by="carol",
    )


def _stale_board_with_concurrent_write(store_path):
    """The lost-update setup: a stale board plus a landed concurrent write."""
    board = _stale_board(store_path)
    _land_concurrent_write(store_path)
    return board


def _spy_on_update_task(monkeypatch):
    """Record every ``_store.update_task`` call; return the call log."""
    calls = []

    def spy(store_arg, task_id, **fields):
        calls.append((store_arg, task_id, fields))
        return {"id": task_id, "title": "Build It", "status": "in_progress"}

    monkeypatch.setattr("scitex_cards._store.update_task", spy)
    return calls


def _spy_on_set_edge(monkeypatch):
    """Record every ``_store.set_edge`` call; return the call log."""
    calls = []

    def spy(store_arg, action=None, kind=None, source=None, target=None):
        calls.append(
            {"action": action, "kind": kind, "source": source, "target": target}
        )
        return {
            "action": action,
            "kind": kind,
            "source": source,
            "target": target,
            "subscribed": None,
        }

    monkeypatch.setattr("scitex_cards._store.set_edge", spy)
    return calls


# ── 1. lost-update survival, one test per converted handler ───────────────


def test_update_against_a_stale_board_returns_ok(store):
    # Arrange
    # the handler holds a stale board; a concurrent write lands.
    board = _stale_board_with_concurrent_write(store)
    # Act
    response = crud.handle_update(
        _request("update", {"id": "build", "status": "done"}), board
    )
    # Assert
    assert response.status_code == 200


def test_update_survives_concurrent_write(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    crud.handle_update(_request("update", {"id": "build", "status": "done"}), board)
    # Act
    tasks = _load(store)
    # Assert
    # both writes survive (the old cache-save erased `concurrent`).
    assert tasks["build"]["status"] == "done" and "concurrent" in tasks


def test_delete_against_a_stale_board_returns_ok(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    # Act
    response = undo.handle_delete(_request("delete", {"id": "build"}), board)
    # Assert
    assert response.status_code == 200


def test_delete_survives_concurrent_write(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    undo.handle_delete(_request("delete", {"id": "build"}), board)
    # Act
    tasks = _load(store)
    # Assert
    assert "build" not in tasks and "concurrent" in tasks


def test_restore_against_a_stale_board_returns_ok(store):
    # Arrange
    # delete first (fresh), then restore against a stale board.
    deleted = json.loads(_post("delete", store, {"id": "build"}).content)
    board = _stale_board_with_concurrent_write(store)
    # Act
    response = undo.handle_restore(
        _request("restore", {"task": deleted["removed"], "refs": deleted["refs"]}),
        board,
    )
    # Assert
    assert response.status_code == 200


def test_restore_survives_concurrent_write(store):
    # Arrange
    deleted = json.loads(_post("delete", store, {"id": "build"}).content)
    board = _stale_board_with_concurrent_write(store)
    undo.handle_restore(
        _request("restore", {"task": deleted["removed"], "refs": deleted["refs"]}),
        board,
    )
    # Act
    tasks = _load(store)
    # Assert
    assert "build" in tasks and "concurrent" in tasks


def test_edge_against_a_stale_board_returns_ok(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    body = {"action": "add", "kind": "blocks", "source": "build", "target": "gate"}
    # Act
    response = edge_handlers.handle_edge(_request("edge", body), board)
    # Assert
    assert response.status_code == 200


def test_edge_survives_concurrent_write(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    body = {"action": "add", "kind": "blocks", "source": "build", "target": "gate"}
    edge_handlers.handle_edge(_request("edge", body), board)
    # Act
    tasks = _load(store)
    # Assert
    assert "gate" in tasks["build"]["blocks"] and "concurrent" in tasks


def test_resolve_against_a_stale_board_returns_ok(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    # Act
    response = resolve.handle_resolve(
        _request("resolve", {"id": "gate", "actor": "operator"}), board
    )
    # Assert
    assert response.status_code == 200


def test_resolve_survives_concurrent_write(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    resolve.handle_resolve(
        _request("resolve", {"id": "gate", "actor": "operator"}), board
    )
    # Act
    tasks = _load(store)
    # Assert
    assert tasks["gate"]["status"] == "done" and "concurrent" in tasks


def test_reopen_against_a_stale_board_returns_ok(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    # Act
    response = reopen.handle_reopen(
        _request("reopen", {"id": "done-card", "actor": "operator"}), board
    )
    # Assert
    assert response.status_code == 200


def test_reopen_survives_concurrent_write(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    reopen.handle_reopen(
        _request("reopen", {"id": "done-card", "actor": "operator"}), board
    )
    # Act
    tasks = _load(store)
    # Assert
    assert tasks["done-card"]["status"] == "blocked" and "concurrent" in tasks


def test_priority_against_a_stale_board_returns_ok(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    # Act
    response = priority.handle_priority(
        _request("priority", {"order": ["gate", "build", "north"]}), board
    )
    # Assert
    assert response.status_code == 200


def test_priority_survives_concurrent_write(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    priority.handle_priority(
        _request("priority", {"order": ["gate", "build", "north"]}), board
    )
    # Act
    tasks = _load(store)
    # Assert
    assert tasks["gate"]["priority"] == 1 and "concurrent" in tasks


def test_archive_against_a_stale_board_returns_ok(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    # Act
    response = stale.handle_archive(
        _request("archive", {"id": "north", "reason": "stale", "by": "operator"}),
        board,
    )
    # Assert
    assert response.status_code == 200


def test_archive_survives_concurrent_write(store):
    # Arrange
    board = _stale_board_with_concurrent_write(store)
    stale.handle_archive(
        _request("archive", {"id": "north", "reason": "stale", "by": "operator"}),
        board,
    )
    # Act
    tasks = _load(store)
    # Assert
    assert tasks["north"]["status"] == "deferred" and "concurrent" in tasks


# ── 2a. handle_update delegates to the locked update_task verb ────────────

_UPDATE_BODY = {
    "id": "build",
    "title": "Renamed",
    "note": "",
    "repo": None,
    "blocks": [],
    "priority": 2,
}


def test_update_delegating_to_the_verb_returns_ok(store, monkeypatch):
    # Arrange
    _spy_on_update_task(monkeypatch)
    # Act
    response = _post("update", store, dict(_UPDATE_BODY))
    # Assert
    assert response.status_code == 200


def test_update_passes_the_resolved_store_to_the_verb(store, monkeypatch):
    # Arrange
    calls = _spy_on_update_task(monkeypatch)
    _post("update", store, dict(_UPDATE_BODY))
    # Act
    store_arg, _task_id, _fields = calls[0]
    # Assert
    assert str(store_arg) == store


def test_update_passes_the_card_id_to_the_verb(store, monkeypatch):
    # Arrange
    calls = _spy_on_update_task(monkeypatch)
    _post("update", store, dict(_UPDATE_BODY))
    # Act
    _store_arg, task_id, _fields = calls[0]
    # Assert
    assert task_id == "build"


def test_update_translates_gui_clears_into_verb_none_deletes(store, monkeypatch):
    # Arrange
    # the GUI's None/""/[] clears must all arrive as the verb's
    # None-deletes, and real values must pass through verbatim.
    calls = _spy_on_update_task(monkeypatch)
    _post("update", store, dict(_UPDATE_BODY))
    # Act
    _store_arg, _task_id, fields = calls[0]
    # Assert
    assert fields == {
        "title": "Renamed",
        "note": None,
        "repo": None,
        "blocks": None,
        "priority": 2,
    }


def test_update_clears_an_empty_string_field_through_the_verb(store):
    # Arrange
    # end-to-end: the real verb, an empty-string clear.
    _post("update", store, {"id": "build", "note": "", "status": "done"})
    # Act
    build = _load(store)["build"]
    # Assert
    assert "note" not in build


def test_update_patches_a_real_value_through_the_verb(store):
    # Arrange
    _post("update", store, {"id": "build", "note": "", "status": "done"})
    # Act
    build = _load(store)["build"]
    # Assert
    assert build["status"] == "done"


def test_update_leaves_untouched_fields_intact_through_the_verb(store):
    # Arrange
    _post("update", store, {"id": "build", "note": "", "status": "done"})
    # Act
    build = _load(store)["build"]
    # Assert
    assert build["title"] == "Build It"


def test_update_stamps_last_activity_via_the_verb(store):
    # Arrange
    # the verb's D11 auto-stamp now applies to GUI updates too (the
    # old cache-write path silently skipped it).
    _post("update", store, {"id": "build", "priority": 3})
    # Act
    build = _load(store)["build"]
    # Assert
    assert build["last_activity"]


def test_update_response_keeps_contract_keys(store):
    # Arrange
    # an owner change makes the verb annotate liveness.
    response = _post("update", store, {"id": "build", "assignee": "dave"})
    # Act
    payload = json.loads(response.content)
    # Assert
    assert set(payload) == {"task", "store_path"}


def test_update_response_hides_the_transport_only_liveness_key(store):
    # Arrange
    response = _post("update", store, {"id": "build", "assignee": "dave"})
    # Act
    payload = json.loads(response.content)
    # Assert
    # the endpoint must not leak it into its response.
    assert "assignee_liveness" not in payload["task"]


def test_update_owner_change_lands_in_the_store(store):
    # Arrange
    _post("update", store, {"id": "build", "assignee": "dave"})
    # Act
    build = _load(store)["build"]
    # Assert
    assert build["assignee"] == "dave"


def test_update_unknown_id_still_404(store):
    # Arrange
    body = {"id": "ghost", "status": "done"}
    # Act
    response = _post("update", store, body)
    # Assert
    assert response.status_code == 404


def test_update_invalid_status_still_400(store):
    # Arrange
    body = {"id": "build", "status": "nope"}
    # Act
    response = _post("update", store, body)
    # Assert
    assert response.status_code == 400


# ── 2b. handle_edge delegates to set_edge with the depends_on SWAP ────────


@pytest.mark.parametrize("action", ["add", "remove"])
def test_edge_depends_on_delegation_returns_ok(store, monkeypatch, action):
    # Arrange
    _spy_on_set_edge(monkeypatch)
    body = {
        "action": action,
        "kind": "depends_on",
        "source": "north",
        "target": "build",
    }
    # Act
    response = _post("edge", store, body)
    # Assert
    assert response.status_code == 200


@pytest.mark.parametrize("action", ["add", "remove"])
def test_edge_depends_on_swaps_source_and_target_for_set_edge(
    store, monkeypatch, action
):
    # Arrange
    # set_edge hangs the field on ITS source; the GUI's depends_on
    # payload hangs it on the GUI target. The handler must SWAP.
    calls = _spy_on_set_edge(monkeypatch)
    body = {
        "action": action,
        "kind": "depends_on",
        "source": "north",
        "target": "build",
    }
    _post("edge", store, body)
    # Act
    delegated = calls[0]
    # Assert
    # verb source = GUI target, verb target = GUI source.
    assert delegated == {
        "action": action,
        "kind": "depends_on",
        "source": "build",
        "target": "north",
    }


@pytest.mark.parametrize("action", ["add", "remove"])
def test_edge_blocks_delegation_returns_ok(store, monkeypatch, action):
    # Arrange
    _spy_on_set_edge(monkeypatch)
    body = {"action": action, "kind": "blocks", "source": "north", "target": "build"}
    # Act
    response = _post("edge", store, body)
    # Assert
    assert response.status_code == 200


@pytest.mark.parametrize("action", ["add", "remove"])
def test_edge_blocks_passes_source_and_target_through(store, monkeypatch, action):
    # Arrange
    # for kind=blocks the two orientations already agree.
    calls = _spy_on_set_edge(monkeypatch)
    body = {"action": action, "kind": "blocks", "source": "north", "target": "build"}
    _post("edge", store, body)
    # Act
    delegated = calls[0]
    # Assert
    assert delegated == {
        "action": action,
        "kind": "blocks",
        "source": "north",
        "target": "build",
    }


# ── 2c. edge-orientation ON-DISK parity with the old handler ──────────────


def test_edge_depends_on_add_lands_on_gui_target(store):
    # Arrange
    # old handler: owner = GUI target, gains GUI source in depends_on.
    _post(
        "edge",
        store,
        {"action": "add", "kind": "depends_on", "source": "north", "target": "build"},
    )
    # Act
    tasks = _load(store)
    # Assert
    # field on `build` (the GUI target).
    assert "north" in tasks["build"]["depends_on"]


def test_edge_depends_on_add_never_lands_on_gui_source(store):
    # Arrange
    _post(
        "edge",
        store,
        {"action": "add", "kind": "depends_on", "source": "north", "target": "build"},
    )
    # Act
    tasks = _load(store)
    # Assert
    # never on `north` (the GUI source).
    assert "depends_on" not in tasks["north"]


def test_edge_depends_on_remove_clears_gui_target(store):
    # Arrange
    # seed: gate.depends_on == [build]; remove(source=build,
    # target=gate).
    _post(
        "edge",
        store,
        {
            "action": "remove",
            "kind": "depends_on",
            "source": "build",
            "target": "gate",
        },
    )
    # Act
    gate = _load(store)["gate"]
    # Assert
    # an emptied list drops the key (the old handler's convention).
    assert "depends_on" not in gate


def test_edge_blocks_add_lands_on_gui_source(store):
    # Arrange
    # old handler: owner = GUI source, gains GUI target in blocks.
    _post(
        "edge",
        store,
        {"action": "add", "kind": "blocks", "source": "build", "target": "gate"},
    )
    # Act
    tasks = _load(store)
    # Assert
    assert "gate" in tasks["build"]["blocks"]


def test_edge_blocks_add_never_lands_on_gui_target(store):
    # Arrange
    _post(
        "edge",
        store,
        {"action": "add", "kind": "blocks", "source": "build", "target": "gate"},
    )
    # Act
    tasks = _load(store)
    # Assert
    assert "blocks" not in tasks["gate"]


def test_edge_blocks_remove_clears_gui_source(store):
    # Arrange
    # add then remove through the endpoint.
    _post(
        "edge",
        store,
        {"action": "add", "kind": "blocks", "source": "build", "target": "gate"},
    )
    _post(
        "edge",
        store,
        {"action": "remove", "kind": "blocks", "source": "build", "target": "gate"},
    )
    # Act
    build = _load(store)["build"]
    # Assert
    assert "blocks" not in build


def test_edge_add_now_subscribes_the_waiter_owner(store):
    # Arrange
    # deliberate behaviour CHANGE: set_edge subscribes the waiting
    # card's owner to the card they wait on (2026-07-13 fix; the GUI path had
    # been silently missing it). depends_on(source=north, target=build) means
    # `build` (owner alice) waits on `north` -> alice subscribes to north.
    _post(
        "edge",
        store,
        {"action": "add", "kind": "depends_on", "source": "north", "target": "build"},
    )
    # Act
    north = _load(store)["north"]
    # Assert
    assert north["subscribers"] == ["alice"]


def test_edge_response_shape_is_unchanged(store):
    # Arrange
    # the FE contract predates set_edge's `subscribed` key.
    response = _post(
        "edge",
        store,
        {
            "action": "add",
            "kind": "depends_on",
            "source": "north",
            "target": "build",
        },
    )
    # Act
    payload = json.loads(response.content)
    # Assert
    # GUI orientation echoed back, exactly the historical keys.
    assert payload == {
        "action": "add",
        "kind": "depends_on",
        "source": "north",
        "target": "build",
        "store_path": store,
    }


def test_edge_unknown_id_still_404(store):
    # Arrange
    body = {
        "action": "add",
        "kind": "depends_on",
        "source": "ghost",
        "target": "build",
    }
    # Act
    response = _post("edge", store, body)
    # Assert
    assert response.status_code == 404


# ── 3. delete/restore keep the FE Undo contract (refs = {id, field}) ──────


def test_delete_refs_carry_the_field_placement(store):
    # Arrange
    # the `_store` verbs return bare-id refs and never re-apply
    # them; the GUI pair must keep the lossless {id, field} contract.
    # Act
    deleted = json.loads(_post("delete", store, {"id": "build"}).content)
    # Assert
    assert {"id": "gate", "field": "depends_on"} in deleted["refs"]


def test_restore_reapplies_the_scrubbed_edge_exactly(store):
    # Arrange
    deleted = json.loads(_post("delete", store, {"id": "build"}).content)
    # Act
    _post("restore", store, {"task": deleted["removed"], "refs": deleted["refs"]})
    # Assert
    # the FE Undo replay puts the edge back.
    assert "build" in _load(store)["gate"]["depends_on"]


# EOF
