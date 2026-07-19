#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``HubBackend`` (remote-hub PR-3) — the loopback integration proof.

The design's PR-3 acceptance row: serve + a ``SCITEX_CARDS_HUB_URL``
client in ONE test process; the verb matrix runs through the HubBackend
resolved by :func:`get_backend`; every write is asserted by a DIRECT
hub-side store read (never through the client under test); and the
no-fallback property is pinned — URL-set-token-missing hard-errors, an
unreachable hub hard-errors, and no code path touches a local store.

Also pinned:

- IDENTITY INJECTION — the hub executes verbs under its own env, so the
  client must stamp the REMOTE agent's identity into by/actor/created_by
  when the caller left them unset; an explicit identity is respected.
- ERROR PARITY — a missing card raises the REAL TaskNotFoundError through
  the wire, so caller error handling is backend-agnostic.
- THE NULL SENTINEL SURVIVES THE WIRE — update_task(field=None) DELETES
  hub-side (the parked/un-park clearing class; a client that strips nulls
  silently swallows remote clears).
- A caller passing its own tasks_path/store gets a loud refusal.
"""

from __future__ import annotations

import threading

import pytest

from scitex_cards import _server, _store
from scitex_cards._backend import get_backend
from scitex_cards._backend_http import HubBackend, HubBackendError


@pytest.fixture()
def hub(tmp_path, monkeypatch):
    """A live hub + a fully-provisioned client environment."""
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")
    tokens_dir = tmp_path / "tokens"
    audit_path = tmp_path / "logs" / "hub_access.jsonl"

    server = _server.make_server(
        store=str(store), port=0, tokens_dir=tokens_dir, audit_path=audit_path
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    monkeypatch.setenv("SCITEX_CARDS_HUB_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("SCITEX_CARDS_HUB_TOKEN_FILE", str(tokens_dir / "hub.token"))
    monkeypatch.delenv("SCITEX_CARDS_HUB_TOKEN", raising=False)
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "remote-agent")

    yield {"store": str(store), "port": port, "tokens_dir": tokens_dir}

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _add(backend, card_id: str, **extra):
    body = dict(
        id=card_id,
        title=f"Card {card_id}",
        status="deferred",
        assignee="alice",
    )
    body.update(extra)
    return backend.add_task(None, **body)


# === resolution + the no-fallback property =================================


def test_get_backend_resolves_to_the_hub_client(hub):
    # Arrange
    expected = HubBackend
    # Act
    backend = get_backend()
    # Assert
    assert isinstance(backend, expected)


def test_url_set_but_token_missing_hard_errors_at_first_call(hub, monkeypatch):
    # Arrange
    monkeypatch.setenv("SCITEX_CARDS_HUB_TOKEN_FILE", "/nonexistent/hub.token")
    backend = get_backend()  # resolution itself stays import-safe
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(HubBackendError, match="no hub token"):
        backend.list_tasks(None)


def test_unreachable_hub_hard_errors_with_the_tunnel_hint(hub, monkeypatch):
    # Arrange
    monkeypatch.setenv("SCITEX_CARDS_HUB_URL", "http://127.0.0.1:1")
    backend = get_backend()
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(HubBackendError, match="tunnel"):
        backend.list_tasks(None)


def test_caller_supplied_store_is_refused(hub):
    # Arrange
    caller_store = "/tmp/some/store.yaml"
    backend = get_backend()
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(HubBackendError, match="pinned hub-side"):
        backend.get_task(caller_store, "any")


# === round trips (read-back DIRECTLY hub-side) =============================


def test_add_task_round_trips_the_card_id(hub):
    # Arrange
    backend = get_backend()
    # Act
    created = _add(backend, "hb-add")
    # Assert
    assert created["id"] == "hb-add"


def test_add_task_injects_the_remote_agent_as_created_by(hub):
    # The caller never set created_by; the CLIENT must have stamped the
    # remote agent's identity — the hub's env must not leak in.
    # Arrange
    backend = get_backend()
    # Act
    _add(backend, "hb-add")
    on_disk = _store.get_task(hub["store"], "hb-add")
    # Assert
    assert on_disk["created_by"] == "remote-agent"


def test_explicit_identity_is_respected_not_overwritten(hub):
    # Arrange
    backend = get_backend()
    # Act
    _add(backend, "hb-by", created_by="named-human")
    on_disk = _store.get_task(hub["store"], "hb-by")
    # Assert
    assert on_disk["created_by"] == "named-human"


def test_update_task_round_trips_the_status(hub):
    # Arrange
    backend = get_backend()
    _add(backend, "hb-upd")
    # Act
    backend.update_task(None, "hb-upd", status="in_progress", note="over the wire")
    on_disk = _store.get_task(hub["store"], "hb-upd")
    # Assert
    assert on_disk["status"] == "in_progress"


def test_update_task_round_trips_the_note(hub):
    # Arrange
    backend = get_backend()
    _add(backend, "hb-upd")
    # Act
    backend.update_task(None, "hb-upd", status="in_progress", note="over the wire")
    on_disk = _store.get_task(hub["store"], "hb-upd")
    # Assert
    assert on_disk["note"] == "over the wire"


def test_a_note_set_over_the_wire_lands_hub_side(hub):
    """The precondition the null-sentinel test below clears — pinned on its
    own so a silently-dropped `note=` cannot make that test vacuously pass."""
    # Arrange
    backend = get_backend()
    # Act
    _add(backend, "hb-null", note="to be cleared")
    on_disk = _store.get_task(hub["store"], "hb-null")
    # Assert
    assert "note" in on_disk


def test_update_null_sentinel_deletes_hub_side(hub):
    """None must survive serialization as an explicit null and DELETE."""
    # Arrange
    backend = get_backend()
    _add(backend, "hb-null", note="to be cleared")
    # Act
    backend.update_task(None, "hb-null", note=None)
    on_disk = _store.get_task(hub["store"], "hb-null")
    # Assert — a client that strips nulls would leave the note standing.
    assert "note" not in on_disk


def test_comment_task_injects_the_remote_agent_as_author(hub):
    # Arrange
    backend = get_backend()
    _add(backend, "hb-com")
    # Act
    result = backend.comment_task(None, "hb-com", "hello from afar")
    # Assert
    assert result["comment"]["author"] == "remote-agent"


def test_comment_task_round_trips_the_comment_text(hub):
    # Arrange
    backend = get_backend()
    _add(backend, "hb-com")
    # Act
    backend.comment_task(None, "hb-com", "hello from afar")
    comments = _store.get_task(hub["store"], "hb-com")["comments"]
    # Assert
    assert comments[-1]["text"] == "hello from afar"


def test_complete_task_sets_the_status_done(hub):
    # Arrange
    backend = get_backend()
    _add(backend, "hb-done")
    # Act
    backend.complete_task(None, "hb-done")
    on_disk = _store.get_task(hub["store"], "hb-done")
    # Assert
    assert on_disk["status"] == "done"


def test_complete_task_stamps_the_remote_agent_as_completer(hub):
    # Arrange
    backend = get_backend()
    _add(backend, "hb-done")
    # Act
    backend.complete_task(None, "hb-done")
    on_disk = _store.get_task(hub["store"], "hb-done")
    # Assert — the hub's own identity must not be the one recorded.
    assert on_disk["_log_meta"]["completed_by"] == "remote-agent"


def test_list_tasks_reads_the_hub_store(hub):
    # Arrange
    backend = get_backend()
    _add(backend, "hb-read")
    # Act
    listed = {t["id"] for t in backend.list_tasks(None, scope="")}
    # Assert
    assert "hb-read" in listed


def test_get_task_reads_the_hub_store(hub):
    # Arrange
    backend = get_backend()
    _add(backend, "hb-read")
    # Act
    fetched = backend.get_task(None, "hb-read")
    # Assert
    assert fetched["title"] == "Card hb-read"


def test_delete_task_removes_the_card_hub_side(hub):
    # Arrange
    backend = get_backend()
    _add(backend, "hb-del")
    # Act
    backend.delete_task(None, "hb-del")
    # Assert — reading it back DIRECTLY hub-side now raises.
    with pytest.raises(Exception):
        _store.get_task(hub["store"], "hb-del")


def test_restore_task_puts_the_card_back(hub):
    # Arrange
    backend = get_backend()
    _add(backend, "hb-del")
    removed = backend.delete_task(None, "hb-del")
    # Act
    backend.restore_task(None, task=removed["removed"], refs=removed.get("refs"))
    on_disk = _store.get_task(hub["store"], "hb-del")
    # Assert
    assert on_disk["id"] == "hb-del"


def test_set_edge_round_trip(hub):
    # Arrange
    backend = get_backend()
    _add(backend, "hb-e1")
    _add(backend, "hb-e2")
    # Act
    backend.set_edge(
        None, action="add", kind="depends_on", source="hb-e2", target="hb-e1"
    )
    on_disk = _store.get_task(hub["store"], "hb-e2")
    # Assert
    assert on_disk["depends_on"] == ["hb-e1"]


def test_dm_send_stamps_the_sending_agent(hub):
    # Arrange
    backend = get_backend()
    # Act
    record = backend.dm_send("remote-agent", "operator", "hub dm")
    # Assert
    assert record["from"] == "remote-agent"


def test_dm_list_returns_the_sent_message(hub):
    # Arrange
    backend = get_backend()
    backend.dm_send("remote-agent", "operator", "hub dm")
    # Act
    thread = backend.dm_list("operator", peer="remote-agent")
    # Assert
    assert [m["body"] for m in thread["messages"]] == ["hub dm"]


def test_poll_notifications_echoes_the_agent(hub):
    # Arrange
    backend = get_backend()
    # Act
    inbox = backend.poll_notifications("remote-agent")
    # Assert
    assert inbox["agent"] == "remote-agent"


def test_poll_notifications_returns_a_notifications_list(hub):
    # Arrange
    backend = get_backend()
    # Act
    inbox = backend.poll_notifications("remote-agent")
    # Assert
    assert isinstance(inbox["notifications"], list)


# === error parity ==========================================================


def test_missing_card_raises_the_real_tasknotfound(hub):
    # Arrange
    from scitex_cards._store import TaskNotFoundError

    backend = get_backend()
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(TaskNotFoundError):
        backend.get_task(None, "ghost")


def test_validation_error_crosses_the_wire_typed(hub):
    # Arrange
    from scitex_cards import TaskValidationError

    backend = get_backend()
    # Act
    # Assert — no owner → the validator refuses; the client re-raises typed.
    with pytest.raises((TaskValidationError, ValueError)):
        backend.add_task(None, id="hb-bad", title="No owner")


# EOF
