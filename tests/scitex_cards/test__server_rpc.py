#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The hub RPC surface (remote-hub PR-2) — transport, auth, and round trips.

No mocks: a REAL :class:`ThreadingHTTPServer` on an ephemeral loopback
port, driven by real ``urllib`` requests, against a real tmp store. Every
write is verified by a DIRECT store read-back through ``_store`` — never
through the server under test (the design's PR-2 acceptance row).

Pinned here:

1. AUTH — 401 without/with-bad bearer; the identity header REQUIRED (400);
   tokens auto-minted 0600 on first serve.
2. TRANSPORT — /v1/health public; unknown verb 404 naming the verb set;
   store-retarget body keys rejected 400; TaskNotFoundError → 404.
3. ROUND TRIPS — add/update/comment/get/list, delete→restore, dm pair;
   ``_log_meta`` / authorship attribution carried by explicit kwargs.
4. CONCURRENCY — two clients interleaving writes over HTTP; both survive
   (the store flock serializes beneath the server; no lost update).
5. AUDIT — one JSONL line per authenticated request.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scitex_cards import _server, _store


@pytest.fixture()
def rig(tmp_path, env):
    """A serving rig: pinned store + tmp tokens + tmp audit + live server.

    The store is SQLite now, and the harness (tests/conftest.py) already pins
    every store-selecting env var at a per-test scratch dir and bootstraps an
    EMPTY, schema-complete canonical DB there. So the server must be pinned to
    that SAME store identity — ``$SCITEX_CARDS_TASKS_YAML_SHARED`` (==
    ``resolve_tasks_path(None)``) — not to a private ``tmp_path/tasks.yaml``.
    A write stamps the canonical DB with whatever store path the server holds;
    if that is a private path, the next read (server-side, and the direct
    ``_store`` read-backs below) resolves the pinned identity, sees a DB stamped
    for a DIFFERENT store, and refuses. Pinning them to the same path makes the
    round trips land. The rig starts empty because the bootstrapped DB is empty.
    """
    env.set("SCITEX_TODO_AGENT_ID", "rpc-tester")
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    tokens_dir = tmp_path / "tokens"
    audit_path = tmp_path / "logs" / "hub_access.jsonl"

    server = _server.make_server(
        store=store, port=0, tokens_dir=tokens_dir, audit_path=audit_path
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    token = (tokens_dir / "hub.token").read_text(encoding="utf-8").strip()

    yield {
        "store": store,
        "port": port,
        "token": token,
        "tokens_dir": tokens_dir,
        "audit_path": audit_path,
    }

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _call(
    rig_info,
    verb: str,
    body: dict | None = None,
    *,
    token: str | None = "GOOD",
    agent: str | None = "rpc-tester",
):
    """POST /v1/rpc/<verb>; returns (status, parsed-json)."""
    url = f"http://127.0.0.1:{rig_info['port']}/v1/rpc/{verb}"
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    if token is not None:
        real = rig_info["token"] if token == "GOOD" else token
        req.add_header("Authorization", f"Bearer {real}")
    if agent is not None:
        req.add_header("X-Scitex-Agent", agent)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get(rig_info, path: str):
    url = f"http://127.0.0.1:{rig_info['port']}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _add(rig_info, card_id: str, **extra):
    body = {
        "id": card_id,
        "title": f"Card {card_id}",
        "status": "deferred",
        "assignee": "alice",
        "created_by": "rpc-tester",
    }
    body.update(extra)
    return _call(rig_info, "add_task", body)


# === 1. auth ===============================================================


#: Several refusals below are asserted twice — once for the STATUS code the
#: transport returns, once for the message body naming what was wrong. They are
#: split into sibling tests because the two failures are different bugs: a wrong
#: code breaks every client's control flow, while a code that is right with an
#: unhelpful body leaves an operator with a bare 400 and nothing to act on.


def test_health_is_public(rig):
    # Arrange
    endpoint = "/v1/health"
    # Act
    status, _payload = _get(rig, endpoint)
    # Assert — no bearer, no identity header, still served.
    assert status == 200


def test_health_reports_a_live_verb_surface(rig):
    # Arrange
    endpoint = "/v1/health"
    # Act
    _status, payload = _get(rig, endpoint)
    # Assert — a probe that proves the RPC surface is actually wired up.
    assert payload["ok"] is True and payload["verbs"] > 0


def test_missing_token_is_401(rig):
    # Arrange
    verb = "list_tasks"
    # Act
    status, _payload = _call(rig, verb, token=None)
    # Assert
    assert status == 401


def test_missing_token_error_names_the_token(rig):
    # Arrange
    verb = "list_tasks"
    # Act
    _status, payload = _call(rig, verb, token=None)
    # Assert
    assert "token" in payload["error"]


def test_bad_token_is_401(rig):
    # Arrange
    wrong_token = "not-the-token"
    # Act
    status, _ = _call(rig, "list_tasks", token=wrong_token)
    # Assert
    assert status == 401


def test_missing_identity_header_is_400(rig):
    # Arrange
    verb = "list_tasks"
    # Act
    status, _payload = _call(rig, verb, agent=None)
    # Assert — authenticated but anonymous is a bad REQUEST, not a bad token.
    assert status == 400


def test_missing_identity_header_error_names_the_header(rig):
    # Arrange
    verb = "list_tasks"
    # Act
    _status, payload = _call(rig, verb, agent=None)
    # Assert
    assert "X-Scitex-Agent" in payload["error"]


def test_token_file_is_minted_0600(rig):
    # Arrange
    token_file = rig["tokens_dir"] / "hub.token"
    # Act
    mode = token_file.stat().st_mode & 0o777
    # Assert — minted on first serve, and not world-readable.
    assert mode == 0o600


# === 2. transport ==========================================================


def test_unknown_verb_is_404(rig):
    # Arrange
    verb = "no_such_verb"
    # Act
    status, _payload = _call(rig, verb)
    # Assert
    assert status == 404


def test_unknown_verb_error_names_the_surface(rig):
    # Arrange
    verb = "no_such_verb"
    # Act
    _status, payload = _call(rig, verb)
    # Assert — the refusal lists what you COULD have called.
    assert "add_task" in payload["verbs"]


def test_store_retarget_is_rejected(rig):
    # Arrange
    body = {"tasks_path": "/tmp/evil.yaml"}
    # Act
    status, _payload = _call(rig, "list_tasks", body)
    # Assert — a client must never point the hub at another store.
    assert status == 400


def test_store_retarget_error_says_the_store_is_pinned(rig):
    # Arrange
    body = {"tasks_path": "/tmp/evil.yaml"}
    # Act
    _status, payload = _call(rig, "list_tasks", body)
    # Assert
    assert "pinned" in payload["error"]


def test_unknown_task_id_is_404(rig):
    # Arrange
    body = {"task_id": "ghost"}
    # Act
    status, _payload = _call(rig, "get_task", body)
    # Assert
    assert status == 404


def test_unknown_task_id_error_names_the_exception_type(rig):
    # Arrange
    body = {"task_id": "ghost"}
    # Act
    _status, payload = _call(rig, "get_task", body)
    # Assert — the engine's exception survives the transport by name.
    assert payload["type"] == "TaskNotFoundError"


def test_validation_error_is_400(rig):
    # Arrange
    owner_less = {"id": "x", "title": "X", "created_by": "rpc-tester"}
    # Act
    status, _ = _call(rig, "add_task", owner_less)
    # Assert — a validator refusal is a 400, never a 500.
    assert status == 400


# === 3. round trips (read-back through the ENGINE, never the server) ======


#: Every round trip below is asserted twice or more: once on what the SERVER
#: answered, and once on what the ENGINE reads back off disk. The read-back is
#: the design's PR-2 acceptance row — a server that echoes a plausible response
#: without persisting anything would satisfy the first half and fail the fleet.
#: Splitting them keeps "the wire lied" and "the write never landed" apart.


def test_add_task_round_trip_returns_the_created_card(rig):
    # Arrange
    card_id = "rt-add"
    # Act
    status, created = _add(rig, card_id)
    # Assert
    assert (status, created["id"]) == (200, card_id)


def test_add_task_over_http_persists_the_title(rig):
    # Arrange
    _add(rig, "rt-add")
    # Act
    on_disk = _store.get_task(rig["store"], "rt-add")
    # Assert — read back through the ENGINE, never the server under test.
    assert on_disk["title"] == "Card rt-add"


def test_add_task_over_http_records_the_creating_agent(rig):
    # Arrange
    _add(rig, "rt-add")
    # Act
    on_disk = _store.get_task(rig["store"], "rt-add")
    # Assert — attribution survives the hop as an explicit kwarg.
    assert on_disk["created_by"] == "rpc-tester"


def test_update_task_round_trip_returns_the_new_status(rig):
    # Arrange
    _add(rig, "rt-upd")
    # Act
    status, updated = _call(
        rig, "update_task", {"task_id": "rt-upd", "status": "in_progress"}
    )
    # Assert
    assert (status, updated["status"]) == (200, "in_progress")


def test_update_task_over_http_persists_the_new_status(rig):
    # Arrange
    _add(rig, "rt-upd")
    # Act
    _call(rig, "update_task", {"task_id": "rt-upd", "status": "in_progress"})
    # Assert
    assert _store.get_task(rig["store"], "rt-upd")["status"] == "in_progress"


def _comment_over_the_wire(rig):
    _add(rig, "rt-com")
    return _call(
        rig,
        "comment_task",
        {"task_id": "rt-com", "text": "over the wire", "by": "remote-agent"},
    )


def test_comment_task_round_trip_with_author(rig):
    # Arrange
    expected = (200, "remote-agent")
    # Act
    status, result = _comment_over_the_wire(rig)
    # Assert — the REMOTE agent is the author, not the serving identity.
    assert (status, result["comment"]["author"]) == expected


def test_comment_task_over_http_persists_the_text(rig):
    # Arrange
    _comment_over_the_wire(rig)
    # Act
    comments = _store.get_task(rig["store"], "rt-com")["comments"]
    # Assert
    assert comments[-1]["text"] == "over the wire"


def test_list_tasks_over_http_returns_200(rig):
    # Arrange
    _add(rig, "rt-list")
    # Act
    status, _rows = _call(rig, "list_tasks", {"scope": ""})
    # Assert
    assert status == 200


def test_list_tasks_reads_the_pinned_store(rig):
    # Arrange
    _add(rig, "rt-list")
    # Act
    _status, rows = _call(rig, "list_tasks", {"scope": ""})
    # Assert — the rows come from the store the server was pinned to.
    assert "rt-list" in {t["id"] for t in rows}


#: Delete-then-restore, staged. Returns ``(delete_status, removed_payload)``
#: after the delete; ``_restored`` then feeds that payload back. Four tests
#: split the one round trip: the delete's answer, the delete's effect, the
#: restore's answer, and the restore's effect. An undo that reports success
#: without putting the card back is the failure worth isolating.
def _deleted(rig):
    _add(rig, "rt-del")
    return _call(rig, "delete_task", {"task_id": "rt-del"})


def _restored(rig):
    _status, removed = _deleted(rig)
    return _call(
        rig,
        "restore_task",
        {"task": removed["removed"], "refs": removed.get("refs")},
    )


def test_delete_task_over_http_returns_the_removed_card(rig):
    # Arrange
    card_id = "rt-del"
    # Act
    status, removed = _deleted(rig)
    # Assert
    assert (status, removed["removed"]["id"]) == (200, card_id)


def test_delete_task_over_http_removes_it_from_the_store(rig):
    # Arrange
    _deleted(rig)
    # Act
    gone = pytest.raises(Exception)
    # Assert — the engine no longer resolves the id at all.
    with gone:
        _store.get_task(rig["store"], "rt-del")


def test_restore_task_over_http_returns_200(rig):
    # Arrange
    expected_status = 200
    # Act
    status, _ = _restored(rig)
    # Assert
    assert status == expected_status


def test_restore_task_over_http_puts_the_card_back(rig):
    # Arrange
    _restored(rig)
    # Act
    on_disk = _store.get_task(rig["store"], "rt-del")
    # Assert — the undo is real, not just a 200.
    assert on_disk["id"] == "rt-del"


def _dm_sent(rig):
    return _call(
        rig,
        "dm_send",
        {"sender": "remote-agent", "to": "operator", "body": "hello hub"},
    )


def _dm_listed(rig):
    _dm_sent(rig)
    return _call(rig, "dm_list", {"sender": "operator", "peer": "remote-agent"})


def test_dm_send_over_http_returns_the_stored_record(rig):
    # Arrange
    expected = (200, "remote-agent")
    # Act
    status, record = _dm_sent(rig)
    # Assert — the sender the CLIENT declared is the record's author.
    assert (status, record["from"]) == expected


def test_dm_list_over_http_returns_200(rig):
    # Arrange
    expected_status = 200
    # Act
    status, _thread = _dm_listed(rig)
    # Assert
    assert status == expected_status


def test_dm_send_and_list_round_trip(rig):
    # Arrange
    expected_bodies = ["hello hub"]
    # Act
    _status, thread = _dm_listed(rig)
    # Assert — the peer reads back exactly what was sent, once.
    assert [m["body"] for m in thread["messages"]] == expected_bodies


# === 4. concurrency ========================================================


#: Two HTTP clients hammering comments onto two cards, ten each. The server
#: threads call the LOCKED verbs, so the store flock serializes beneath HTTP
#: concurrency — the design's whole claim. Returns the exceptions the client
#: threads collected (a non-200 is raised inside the thread and surfaced here
#: rather than asserted there, so every assertion in this file stays in a test
#: body). The three tests below split the proof: no client errored, and each
#: card kept ALL ten of its writes. A lost update shows up only in the counts.
def _hammer_two_cards(rig):
    _add(rig, "cc-a")
    _add(rig, "cc-b")
    errors: list = []

    def hammer(card_id: str):
        try:
            for i in range(10):
                status, _ = _call(
                    rig,
                    "comment_task",
                    {"task_id": card_id, "text": f"c{i}", "by": "hammer"},
                )
                if status != 200:
                    raise RuntimeError(f"comment_task on {card_id} returned {status}")
        except Exception as exc:  # noqa: BLE001 — surfaced via the list
            errors.append(exc)

    threads = [
        threading.Thread(target=hammer, args=("cc-a",)),
        threading.Thread(target=hammer, args=("cc-b",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return errors


def test_interleaved_remote_writers_hit_no_errors(rig):
    # Arrange
    expected_errors: list = []
    # Act
    errors = _hammer_two_cards(rig)
    # Assert — HTTP concurrency never surfaced a refusal to either client.
    assert errors == expected_errors


def test_interleaved_remote_writers_lose_nothing_on_the_first_card(rig):
    # Arrange
    _hammer_two_cards(rig)
    # Act
    comments = _store.get_task(rig["store"], "cc-a")["comments"]
    # Assert — all ten writes survived the interleave.
    assert len(comments) == 10


def test_interleaved_remote_writers_lose_nothing_on_the_second_card(rig):
    # Arrange
    _hammer_two_cards(rig)
    # Act
    comments = _store.get_task(rig["store"], "cc-b")["comments"]
    # Assert
    assert len(comments) == 10


# === 5. audit ==============================================================


#: The audit trail after one add and one get, both authenticated. Three tests
#: split what one asserted: that BOTH verbs were logged, that every line carries
#: the calling identity, and that every line is timestamped with an outcome. An
#: audit log missing any one of those is not an audit log.
def _audit_lines(rig):
    _add(rig, "au-1")
    _call(rig, "get_task", {"task_id": "au-1"})
    return [
        json.loads(line)
        for line in Path(rig["audit_path"]).read_text(encoding="utf-8").splitlines()
    ]


def test_audit_line_per_authenticated_request(rig):
    # Arrange
    lines = _audit_lines(rig)
    # Act
    verbs = [entry["verb"] for entry in lines]
    # Assert — one line per request, not one per session.
    assert "add_task" in verbs and "get_task" in verbs


def test_every_audit_line_names_the_calling_agent(rig):
    # Arrange
    lines = _audit_lines(rig)
    # Act
    agents = {entry["agent"] for entry in lines}
    # Assert
    assert agents == {"rpc-tester"}


def test_every_audit_line_is_timestamped_with_an_outcome(rig):
    # Arrange
    lines = _audit_lines(rig)
    # Act
    incomplete = [e for e in lines if "ts" not in e or "status" not in e]
    # Assert — a line without a time or a result cannot reconstruct anything.
    assert incomplete == []


def test_rotation_revokes_the_old_token(rig):
    # Arrange
    old_token = rig["token"]
    _server.mint_token(rig["tokens_dir"], "hub")
    # Act
    status, _ = _call(rig, "list_tasks", token=old_token)
    # Assert — revoked in place, with no server restart.
    assert status == 401


def test_rotation_admits_the_new_token(rig):
    # Arrange
    _server.mint_token(rig["tokens_dir"], "hub")
    new_token = (rig["tokens_dir"] / "hub.token").read_text().strip()
    # Act
    status, _ = _call(rig, "list_tasks", token=new_token)
    # Assert — rotation is not just revocation; the replacement must work.
    assert status == 200


# EOF
