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
import os
from pathlib import Path

import pytest

pytest.importorskip("django")

# ``seed_db_from_doc`` is re-exported by tests/scitex_cards/_django/conftest.py
# (the nearest ``conftest`` module for this file), which loads it from the shared
# tests/scitex_cards/conftest.py — see THE STORE-PATH RULE in the migration
# playbook.
from conftest import seed_db_from_doc  # noqa: E402
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
from scitex_cards._model import load_tasks  # noqa: E402
from scitex_cards._yaml import safe_load  # noqa: E402

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
def store(env):
    # Hermetic: no per-project lane union from the real ~/proj tree.
    env.set("SCITEX_CARDS_LANE_GLOBS", "")
    # SQLite store: seed the prior cards into the canonical DB, then hand the
    # handlers the PINNED store-identity path (never a tmp_path YAML — a write
    # stamped with a tmp path fails the next read's ownership check). The DB is
    # authoritative for content; the path survives only as a provenance label.
    # The board/services layer (get_board -> load_groups) still stat()s the
    # identity file, so it must EXIST though its content is never read (an empty
    # file suffices; the _django autouse fixture also guarantees this).
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


def _request(endpoint, body):
    return RequestFactory().post(
        f"/{endpoint}",
        data=json.dumps(body),
        content_type="application/json",
    )


def _load(store_path):
    # Read back through the canonical store (SQLite); the path is a label only.
    return {t["id"]: t for t in load_tasks(store_path)}


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


# NO SPY ON `_store.update_task` HERE ANY MORE, and no new read helper either:
# `_load(store_path)` above already reads cards back through the canonical
# store, and the end-to-end tests below were already using it.
#
# The spy proved the handler CALLED the verb with certain arguments. Reading
# the card back proves the verb's EFFECT landed in the store the handler
# resolved — which is what "delegates to the locked verb" exists to guarantee.
# A spy cannot tell a delegation that worked from one whose arguments were
# accepted and discarded.


# NO SPY ON `_store.set_edge` EITHER. The edge tests below assert which CARD
# the link landed on, which is what the depends_on swap actually decides. A
# spy comparing the delegated dict to the GUI dict cannot distinguish "not
# swapped" from "swapped twice"; the card can.


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


def test_update_delegating_to_the_verb_returns_ok(store):
    # Arrange
    # Act
    response = _post("update", store, dict(_UPDATE_BODY))
    # Assert
    assert response.status_code == 200


def test_update_lands_in_the_resolved_store(store):
    # Arrange
    _post("update", store, dict(_UPDATE_BODY))
    # Act — read back from the store the handler was pointed at
    card = _load(store)["build"]
    # Assert
    assert card.get("title") == "Renamed"


def test_update_changes_the_card_named_in_the_body(store):
    # Arrange
    _post("update", store, dict(_UPDATE_BODY))
    # Act
    card = _load(store)["build"]
    # Assert — the id in the body is the card that moved
    assert card.get("id") == "build"


def test_update_translates_gui_clears_into_real_deletions(store):
    # Arrange
    # the GUI's None/""/[] clears must become ACTUAL absences on the card,
    # not merely arrive at the verb as None — the spy this replaces could
    # only see the argument, never whether the field was really removed.
    _post("update", store, dict(_UPDATE_BODY))
    # Act
    build = _load(store)["build"]
    # Assert
    assert not {"note", "repo", "blocks"} & set(build)


def test_update_passes_real_values_through_to_the_card(store):
    # Arrange
    # the other half of the same translation: a non-clear must survive it.
    _post("update", store, dict(_UPDATE_BODY))
    # Act
    build = _load(store)["build"]
    # Assert
    assert (build.get("title"), build.get("priority")) == ("Renamed", 2)


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


_DEPENDS_BODY = {
    "action": "add",
    "kind": "depends_on",
    "source": "north",
    "target": "build",
}


@pytest.mark.parametrize("action", ["add", "remove"])
def test_edge_depends_on_delegation_returns_ok(store, action):
    # Arrange
    # Act
    response = _post("edge", store, {**_DEPENDS_BODY, "action": action})
    # Assert
    assert response.status_code == 200


def test_edge_depends_on_add_hangs_the_link_on_the_gui_target(store):
    # Arrange
    # set_edge hangs the field on ITS source; the GUI's depends_on payload
    # names the dependency on the GUI TARGET, so the handler must SWAP. The
    # swap is observable on the card: `build` ends up depending on `north`.
    # Act
    _post("edge", store, _DEPENDS_BODY)
    # Assert
    assert "north" in (_load(store)["build"].get("depends_on") or [])


def test_edge_depends_on_remove_clears_the_link_from_the_gui_target(store):
    # Arrange
    # The link must EXIST before removing it: against the seed (`build` has
    # no depends_on) a bare remove is a no-op that would pass while proving
    # nothing — which is what a call-recording spy could not have told us.
    # Split from the add case because arranging one and asserting the
    # opposite outcome is two tests sharing a signature (STX-TQ006).
    _post("edge", store, _DEPENDS_BODY)
    # Act
    _post("edge", store, {**_DEPENDS_BODY, "action": "remove"})
    # Assert
    assert "north" not in (_load(store)["build"].get("depends_on") or [])


_BLOCKS_BODY = {
    "action": "add",
    "kind": "blocks",
    "source": "north",
    "target": "build",
}


@pytest.mark.parametrize("action", ["add", "remove"])
def test_edge_blocks_delegation_returns_ok(store, action):
    # Arrange
    # Act
    response = _post("edge", store, {**_BLOCKS_BODY, "action": action})
    # Assert
    assert response.status_code == 200


def test_edge_blocks_add_hangs_the_link_on_the_gui_source(store):
    # Arrange
    # for kind=blocks the two orientations already agree, so NO swap: the
    # link lands on the GUI SOURCE. Asserting the card rather than the call
    # is what distinguishes "no swap" from "swapped twice" — a spy comparing
    # the delegated dict to the GUI dict cannot tell those apart.
    # Act
    _post("edge", store, _BLOCKS_BODY)
    # Assert
    assert "build" in (_load(store)["north"].get("blocks") or [])


def test_edge_blocks_remove_clears_the_link_from_the_gui_source(store):
    # Arrange — same reason as the depends_on pair: a remove against a link
    # that was never added is a no-op, and a no-op passes an absence check
    # without exercising anything.
    _post("edge", store, _BLOCKS_BODY)
    # Act
    _post("edge", store, {**_BLOCKS_BODY, "action": "remove"})
    # Assert
    assert "build" not in (_load(store)["north"].get("blocks") or [])


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
