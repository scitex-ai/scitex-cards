#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the POST /create, /update, /delete handlers.

Mirrors ``src/scitex_cards/_django/handlers/crud.py``. Drives
``views.api_dispatch`` with a real ``RequestFactory`` POST against a tmp
``tasks.yaml`` (no mocks, STX-NM / PA-306), verifying both the JSON response
and the YAML written by ``save_tasks``.

One assertion per test (STX-TQ007); shared arrange lives in the helpers and
fixtures below.
"""

from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402

# `gate` depends on `build`; `build` nests under `north`. Lets us prove that
# delete scrubs both the depends_on edge and the parent pointer.
_STORE_TEXT = (
    "tasks:\n"
    "  - {id: north, title: North Star, status: goal}\n"
    "  - {id: build, title: Build It, status: in_progress, parent: north}\n"
    "  - {id: gate, title: Gate, status: blocked, depends_on: [build]}\n"
)


@pytest.fixture
def store():
    from conftest import seed_db_from_doc

    from scitex_cards._yaml import safe_load

    doc = safe_load(_STORE_TEXT) or {}
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    _reset_cache()
    yield os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _reset_cache()


@pytest.fixture
def no_agent_id_env(env):
    """No ``$SCITEX_TODO_AGENT_ID`` — created_by falls back to the board."""
    env.delete("SCITEX_TODO_AGENT_ID")


def _post(endpoint, store_path, body):
    request = RequestFactory().post(
        f"/{endpoint}?store={store_path}",
        data=json.dumps(body),
        content_type="application/json",
    )
    return views.api_dispatch(request, endpoint)


def _load(store_path):
    from scitex_cards._model import load_tasks

    return {t["id"]: t for t in load_tasks(store_path)}


def _created_task(store_path, body):
    """POST /create and return the created task dict from the response."""
    return json.loads(_post("create", store_path, body).content)["task"]


# ── create ───────────────────────────────────────────────────────────────
# Every create now REQUIRES an owner (handle_create delegates to add_task,
# fail-loud without one), so happy-path bodies carry an `assignee`.
def test_create_endpoint_returns_200_ok(store):
    # Arrange
    body = {"title": "New Thing", "assignee": "alice"}
    # Act
    response = _post("create", store, body)
    # Assert
    assert response.status_code == 200


def test_create_generates_slug_id(store):
    # Arrange
    body = {"title": "My New Thing!", "assignee": "alice"}
    # Act
    task = _created_task(store, body)
    # Assert
    assert task["id"] == "my-new-thing"


def test_create_defaults_status_to_deferred(store):
    # Arrange
    # `deferred` is the default since pending was abolished
    # (2026-07-10).
    body = {"title": "X", "assignee": "alice"}
    # Act
    task = _created_task(store, body)
    # Assert
    assert task["status"] == "deferred"


def test_create_persists_the_card_to_the_store(store):
    # Arrange
    body = {"title": "Persisted", "note": "hi", "assignee": "alice"}
    # Act
    _post("create", store, body)
    # Assert
    assert "persisted" in _load(store)


def test_create_dedupes_id_on_title_collision(store):
    # Arrange
    _post("create", store, {"title": "Dup", "assignee": "alice"})
    # Act
    task = _created_task(store, {"title": "Dup", "assignee": "alice"})
    # Assert
    assert task["id"] == "dup-2"


def test_create_rejects_missing_title_with_400(store):
    # Arrange
    body = {"status": "pending", "assignee": "alice"}
    # Act
    response = _post("create", store, body)
    # Assert
    assert response.status_code == 400


def test_create_rejects_get_with_405(store):
    # Arrange
    request = RequestFactory().get(f"/create?store={store}")
    # Act
    response = views.api_dispatch(request, "create")
    # Assert
    assert response.status_code == 405


def test_create_sets_the_requested_assignee(store, no_agent_id_env):
    # Arrange
    body = {"title": "Owned Card", "assignee": "bob"}
    # Act
    task = _created_task(store, body)
    # Assert
    # the card is fully OWNED.
    assert task["assignee"] == "bob"


def test_create_keeps_agent_in_lock_step_with_assignee(store, no_agent_id_env):
    # Arrange
    body = {"title": "Owned Card", "assignee": "bob"}
    # Act
    task = _created_task(store, body)
    # Assert
    assert task["agent"] == "bob"


def test_create_stamps_the_operator_as_creator(store, no_agent_id_env):
    # Arrange
    # with no agent id in the env, the board's identity is used.
    body = {"title": "Owned Card", "assignee": "bob"}
    # Act
    task = _created_task(store, body)
    # Assert
    assert task["created_by"] == "operator"


def test_create_accepts_agent_alone_as_the_owner(store):
    # Arrange
    # `agent` alone also satisfies the owner requirement.
    body = {"title": "Agent Owned", "agent": "carol"}
    # Act
    task = _created_task(store, body)
    # Assert
    assert task["agent"] == "carol"


def test_create_mirrors_agent_into_assignee(store):
    # Arrange
    body = {"title": "Agent Owned", "agent": "carol"}
    # Act
    task = _created_task(store, body)
    # Assert
    assert task["assignee"] == "carol"


def test_create_rejects_missing_assignee_with_400(store):
    # Arrange
    # no owner at all.
    body = {"title": "Ownerless"}
    # Act
    response = _post("create", store, body)
    # Assert
    assert response.status_code == 400


def test_ownerless_create_error_names_the_assignee_field(store):
    # Arrange
    response = _post("create", store, {"title": "Ownerless"})
    # Act
    payload = json.loads(response.content)
    # Assert
    assert "assignee" in payload["error"]


def test_ownerless_create_writes_nothing_to_the_store(store):
    # Arrange
    _post("create", store, {"title": "Ownerless"})
    # Act
    tasks = _load(store)
    # Assert
    # fail-loud, not fail-corrupt.
    assert "ownerless" not in tasks


# ── update ───────────────────────────────────────────────────────────────
def test_update_endpoint_returns_200_ok(store):
    # Arrange
    body = {"id": "build", "status": "done"}
    # Act
    response = _post("update", store, body)
    # Assert
    assert response.status_code == 200


def test_update_patches_the_given_field(store):
    # Arrange
    _post("update", store, {"id": "build", "status": "done"})
    # Act
    build = _load(store)["build"]
    # Assert
    assert build["status"] == "done"


def test_update_leaves_untouched_fields_alone(store):
    # Arrange
    _post("update", store, {"id": "build", "status": "done"})
    # Act
    build = _load(store)["build"]
    # Assert
    assert build["title"] == "Build It"


def test_update_clears_field_on_empty_value(store):
    # Arrange
    _post("update", store, {"id": "build", "parent": None})
    # Act
    build = _load(store)["build"]
    # Assert
    assert "parent" not in build


def test_update_unknown_id_returns_404(store):
    # Arrange
    body = {"id": "ghost", "status": "done"}
    # Act
    response = _post("update", store, body)
    # Assert
    assert response.status_code == 404


def test_update_rejects_invalid_status_with_400(store):
    # Arrange
    body = {"id": "build", "status": "nope"}
    # Act
    response = _post("update", store, body)
    # Assert
    assert response.status_code == 400


# ── delete ───────────────────────────────────────────────────────────────
def test_delete_endpoint_returns_200_ok(store):
    # Arrange
    body = {"id": "build"}
    # Act
    response = _post("delete", store, body)
    # Assert
    assert response.status_code == 200


def test_delete_removes_the_task_from_the_store(store):
    # Arrange
    _post("delete", store, {"id": "build"})
    # Act
    remaining = _load(store)
    # Assert
    assert "build" not in remaining


def test_delete_scrubs_depends_on_reference(store):
    # Arrange
    # `gate` depended on `build`.
    _post("delete", store, {"id": "build"})
    # Act
    gate = _load(store)["gate"]
    # Assert
    # that edge must be gone, not dangling.
    assert "depends_on" not in gate


def test_delete_scrubs_parent_reference(store):
    # Arrange
    # `build`'s parent is `north`; deleting `north` must clear it.
    _post("delete", store, {"id": "north"})
    # Act
    build = _load(store)["build"]
    # Assert
    assert "parent" not in build


def test_delete_unknown_id_returns_404(store):
    # Arrange
    body = {"id": "ghost"}
    # Act
    response = _post("delete", store, body)
    # Assert
    assert response.status_code == 404


def test_delete_returns_the_removed_task(store):
    # Arrange
    # Act
    payload = json.loads(_post("delete", store, {"id": "build"}).content)
    # Assert
    assert payload["removed"]["id"] == "build"


def test_delete_returns_the_scrubbed_references(store):
    # Arrange
    # `gate` depends_on `build`; deleting `build` scrubs that ref.
    # Act
    payload = json.loads(_post("delete", store, {"id": "build"}).content)
    # Assert
    assert {"id": "gate", "field": "depends_on"} in payload["refs"]


# ── restore (undo delete) ──────────────────────────────────────────────────
def test_restore_reinserts_the_deleted_task(store):
    # Arrange
    # delete `build` and keep the response.
    deleted = json.loads(_post("delete", store, {"id": "build"}).content)
    # Act
    _post("restore", store, {"task": deleted["removed"], "refs": deleted["refs"]})
    # Assert
    assert "build" in _load(store)


def test_restore_reapplies_scrubbed_refs(store):
    # Arrange
    deleted = json.loads(_post("delete", store, {"id": "build"}).content)
    # Act
    _post("restore", store, {"task": deleted["removed"], "refs": deleted["refs"]})
    # Assert
    # `gate.depends_on` regained `build`.
    assert "build" in _load(store)["gate"]["depends_on"]


def test_restore_requires_task_with_id(store):
    # Arrange
    body = {"task": {"title": "x"}, "refs": []}
    # Act
    response = _post("restore", store, body)
    # Assert
    assert response.status_code == 400


# ── comment ──────────────────────────────────────────────────────────────
def test_comment_endpoint_returns_200_ok(store):
    # Arrange
    body = {"id": "build", "text": "looks good", "author": "alice"}
    # Act
    response = _post("comment", store, body)
    # Assert
    assert response.status_code == 200


def test_comment_appends_to_thread(store):
    # Arrange
    _post("comment", store, {"id": "build", "text": "one", "author": "a"})
    _post("comment", store, {"id": "build", "text": "two", "author": "b"})
    # Act
    texts = [c["text"] for c in _load(store)["build"]["comments"]]
    # Assert
    # both comments survive (append-only, no clobber).
    assert texts == ["one", "two"]


def test_comment_stamps_the_author(store):
    # Arrange
    _post("comment", store, {"id": "build", "text": "hi", "author": "alice"})
    # Act
    entry = _load(store)["build"]["comments"][0]
    # Assert
    assert entry["author"] == "alice"


def test_comment_stamps_a_timestamp(store):
    # Arrange
    _post("comment", store, {"id": "build", "text": "hi", "author": "alice"})
    # Act
    entry = _load(store)["build"]["comments"][0]
    # Assert
    assert entry["ts"]


def test_comment_rejects_missing_text_with_400(store):
    # Arrange
    body = {"id": "build"}
    # Act
    response = _post("comment", store, body)
    # Assert
    assert response.status_code == 400


def test_comment_unknown_id_returns_404(store):
    # Arrange
    body = {"id": "ghost", "text": "x"}
    # Act
    response = _post("comment", store, body)
    # Assert
    assert response.status_code == 404


# ── comment delivery via the standalone INBOX (no direct-POST) ────────────
# Comments now deliver through the per-recipient PULL-inbox: handle_comment
# delegates to comment_task (which emits `commented` → the C4 dispatcher
# enqueues to each recipient's inbox), and the /comment toast reflects that
# QUEUE — not a direct turn-URL POST. So a comment NEVER depends on a network
# call to a (possibly containerized / unreachable) owner. No mocks: a real
# tmp store, real comment_task/emit, real poll_inbox.
def _store_with_agent(tmp_path):
    """A store whose card is OWNED by 'owner-agent' so the comment is queued."""
    from conftest import seed_db_from_doc

    doc = {
        "tasks": [
            {
                "id": "owned",
                "title": "Owned",
                "status": "in_progress",
                "agent": "owner-agent",
            }
        ]
    }
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    _reset_cache()
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def _comment_relay(tmp_path, text="ping"):
    """POST a comment on the owned card; return the response's relay block."""
    store_path = _store_with_agent(tmp_path)
    try:
        response = _post(
            "comment",
            store_path,
            {"id": "owned", "text": text, "author": "operator"},
        )
        return json.loads(response.content)["relay"]
    finally:
        _reset_cache()


def _comment_owner_inbox(tmp_path):
    """POST a comment on the owned card; return the owner's inbox items."""
    from scitex_cards._inbox import poll_inbox

    store_path = _store_with_agent(tmp_path)
    try:
        _post(
            "comment",
            store_path,
            {"id": "owned", "text": "ping", "author": "operator"},
        )
        return poll_inbox("owner-agent", store=store_path)
    finally:
        _reset_cache()


def test_comment_toast_reports_the_relay_as_sent(tmp_path):
    # Arrange
    # Act
    relay = _comment_relay(tmp_path)
    # Assert
    assert relay["sent"] is True


def test_comment_toast_names_the_inbox_wire_not_a_connection_error(tmp_path):
    # Arrange
    # the toast must reflect the INBOX QUEUE, never the old
    # direct-POST connection error.
    # Act
    relay = _comment_relay(tmp_path)
    # Assert
    assert relay["wire"] == "inbox"


def test_comment_toast_names_the_owning_agent_as_target(tmp_path):
    # Arrange
    # Act
    relay = _comment_relay(tmp_path)
    # Assert
    assert relay["target"] == "owner-agent"


def test_comment_toast_lists_the_queued_recipients(tmp_path):
    # Arrange
    # the owner is the queued recipient (author != owner).
    # Act
    relay = _comment_relay(tmp_path)
    # Assert
    # the board shows "queued to N recipient(s)".
    assert relay["queued"] == ["owner-agent"]


def test_comment_does_not_await_a_turn_url_post(tmp_path, env):
    # A comment must NOT depend on / await a turn-URL POST. Point the owner at
    # a CLOSED port (a real refused connection) and assert the relay went over
    # the inbox rail (wire == "inbox"). That structural guarantee — not a
    # wall-clock threshold — is what proves the comment path never awaits the
    # turn-URL: a refused connection resolves instantly, so timing cannot tell
    # a correct run from a regressed one, and under CI load the correct path
    # alone can exceed any tight threshold (a wall-clock assert here was a
    # flaky release blocker). Speed is covered structurally by the inbox wire.
    # Arrange
    import json as _json
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    store_path = _store_with_agent(tmp_path)
    env.set(
        "SCITEX_TODO_AGENT_TURN_URLS",
        _json.dumps({"owner-agent": f"http://127.0.0.1:{port}/v1/turn"}),
    )
    env.delete("SCITEX_TODO_PUSH_DRY_RUN")
    try:
        # Act
        resp = _post(
            "comment",
            store_path,
            {"id": "owned", "text": "ping", "author": "operator"},
        )
        payload = json.loads(resp.content)
    finally:
        _reset_cache()
    # Assert
    # the relay went over the inbox rail, not a synchronous turn-URL
    # POST (the structural proof that the comment path does not await one).
    assert payload["relay"]["wire"] == "inbox"


def test_comment_enqueues_a_commented_event_to_the_owner(tmp_path):
    # Arrange
    # end-to-end over the always-works rail; no mocks.
    # Act
    notes = _comment_owner_inbox(tmp_path)
    # Assert
    assert [n["event_type"] for n in notes] == ["commented"]


def test_comment_notification_names_the_commented_card(tmp_path):
    # Arrange
    # Act
    notes = _comment_owner_inbox(tmp_path)
    # Assert
    assert notes[0]["card_id"] == "owned"


def test_comment_still_saved_when_owner_unreachable(tmp_path, env):
    # The comment must always land on disk — there is no network on the write
    # path now, but assert persistence even with an unreachable turn-URL set.
    # Arrange
    import json as _json
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    store_path = _store_with_agent(tmp_path)
    env.set(
        "SCITEX_TODO_AGENT_TURN_URLS",
        _json.dumps({"owner-agent": f"http://127.0.0.1:{port}/v1/turn"}),
    )
    env.delete("SCITEX_TODO_PUSH_DRY_RUN")
    try:
        # Act
        _post(
            "comment",
            store_path,
            {"id": "owned", "text": "saved-anyway", "author": "operator"},
        )
        texts = [c["text"] for c in _load(store_path)["owned"]["comments"]]
    finally:
        _reset_cache()
    # Assert
    assert "saved-anyway" in texts


# ── edge ─────────────────────────────────────────────────────────────────
def test_edge_add_depends_on_returns_ok(store):
    # Arrange
    body = {
        "action": "add",
        "kind": "depends_on",
        "source": "north",
        "target": "build",
    }
    # Act
    response = _post("edge", store, body)
    # Assert
    assert response.status_code == 200


def test_edge_add_depends_on_writes_to_target(store):
    # Arrange
    # depends_on edge source->target means target.depends_on += source.
    _post(
        "edge",
        store,
        {"action": "add", "kind": "depends_on", "source": "gate", "target": "build"},
    )
    # Act
    build = _load(store)["build"]
    # Assert
    assert "gate" in build["depends_on"]


def test_edge_add_blocks_writes_to_source(store):
    # Arrange
    # blocks edge source->target means source.blocks += target.
    _post(
        "edge",
        store,
        {"action": "add", "kind": "blocks", "source": "build", "target": "gate"},
    )
    # Act
    build = _load(store)["build"]
    # Assert
    assert "gate" in build["blocks"]


def test_edge_remove_drops_reference(store):
    # Arrange
    # `gate` depends_on `build` in the seed; remove that edge.
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
    assert "depends_on" not in gate


def test_edge_rejects_self_edge_with_400(store):
    # Arrange
    body = {
        "action": "add",
        "kind": "depends_on",
        "source": "build",
        "target": "build",
    }
    # Act
    response = _post("edge", store, body)
    # Assert
    assert response.status_code == 400


def test_edge_unknown_id_returns_404(store):
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


def test_edge_rejects_bad_kind_with_400(store):
    # Arrange
    body = {
        "action": "add",
        "kind": "related",
        "source": "north",
        "target": "build",
    }
    # Act
    response = _post("edge", store, body)
    # Assert
    assert response.status_code == 400


# EOF
