#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The backend seam (PR-1 of the remote-hub design) — LocalBackend parity.

Three guarantees, per docs/design/remote-hub-backend.md §2/§6:

1. COMPLETENESS — every name in ``BACKEND_VERBS`` is a callable on
   ``LocalBackend`` (a backend missing a verb would fail at call time on a
   remote host, long after review).
2. RESOLUTION — ``get_backend()`` returns the local passthrough when
   ``SCITEX_CARDS_HUB_URL`` is unset, and FAILS LOUD when it is set on a
   build with no HTTP client. A silent local fallback would write a store
   the hub never sees (the one-database ruling), so the error is pinned.
3. ROUND TRIP — every verb, called through the seam against a real tmp
   store, persists exactly what a direct read of that store shows
   (read-back through ``_store``, not through the object under test).
"""

from __future__ import annotations

import pytest

from scitex_cards import _store
from scitex_cards._backend import (
    BACKEND_VERBS,
    LocalBackend,
    get_backend,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "seam-tester")
    monkeypatch.delenv("SCITEX_CARDS_HUB_URL", raising=False)
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return str(path)


def _add(backend, store_path, cid, title, assignee):
    return backend.add_task(
        store_path,
        id=cid,
        title=title,
        status="deferred",
        assignee=assignee,
        created_by="seam-tester",
    )


def _seed_two_cards(backend, store_path):
    """Put ``seam-a`` and ``seam-b`` in the store through the seam."""
    _add(backend, store_path, "seam-a", "Seam A", "alice")
    _add(backend, store_path, "seam-b", "Seam B", "bob")


def _seed_and_delete(backend, store_path):
    """Add a card through the seam, delete it, and hand back the payload."""
    _add(backend, store_path, "seam-del", "To delete", "alice")
    return backend.delete_task(store_path, "seam-del")


# --------------------------------------------------------------------------- #
# 1. completeness + resolution                                                #
# --------------------------------------------------------------------------- #


def test_every_declared_verb_is_a_local_backend_callable():
    # Arrange
    backend = LocalBackend()
    # Act
    missing = [v for v in BACKEND_VERBS if not callable(getattr(backend, v, None))]
    # Assert
    assert missing == []


def test_resolution_is_local_when_hub_url_unset(monkeypatch):
    # Arrange
    monkeypatch.delenv("SCITEX_CARDS_HUB_URL", raising=False)
    # Act
    backend = get_backend()
    # Assert
    assert isinstance(backend, LocalBackend)


def test_resolution_returns_the_hub_client_when_url_set(monkeypatch):
    """PR-3 replaced PR-1's resolve-time refusal with the real client.

    The fail-loud property MOVED, not vanished: it now fires at the first
    CALL when the hub is unusable (no token / unreachable) — pinned in
    test__hub_backend.py — because resolution must stay import-safe while
    a silent local fallback stays impossible.
    """
    # Arrange
    monkeypatch.setenv("SCITEX_CARDS_HUB_URL", "http://127.0.0.1:8765")
    from scitex_cards._backend_http import HubBackend

    # Act
    backend = get_backend()
    # Assert
    assert isinstance(backend, HubBackend)


def test_resolved_hub_client_carries_the_configured_url(monkeypatch):
    # Arrange
    monkeypatch.setenv("SCITEX_CARDS_HUB_URL", "http://127.0.0.1:8765")
    # Act
    backend = get_backend()
    # Assert
    assert backend.url == "http://127.0.0.1:8765"


# --------------------------------------------------------------------------- #
# 2. task verbs — round trip through the seam                                 #
# --------------------------------------------------------------------------- #


def test_add_task_through_the_seam_returns_the_created_card(store):
    # Arrange
    backend = get_backend()
    # Act
    created = _add(backend, store, "seam-a", "Seam A", "alice")
    # Assert
    assert created["id"] == "seam-a"


def test_added_card_is_readable_through_the_store_engine(store):
    # Arrange
    backend = get_backend()
    # Act
    _seed_two_cards(backend, store)
    # Assert — read-back through the engine, never through the object
    # under test.
    assert _store.get_task(store, "seam-a")["title"] == "Seam A"


def test_added_card_is_readable_through_the_seam_get_task(store):
    # Arrange
    backend = get_backend()
    # Act
    _seed_two_cards(backend, store)
    # Assert
    assert backend.get_task(store, "seam-a")["title"] == "Seam A"


def test_list_tasks_through_the_seam_returns_every_card(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    listed = backend.list_tasks(store, scope="")
    # Assert
    assert {t["id"] for t in listed} == {"seam-a", "seam-b"}


def test_update_task_through_the_seam_persists_the_new_status(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    backend.update_task(store, "seam-a", status="in_progress")
    # Assert
    assert _store.get_task(store, "seam-a")["status"] == "in_progress"


def test_update_task_through_the_seam_returns_the_updated_card(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    updated = backend.update_task(store, "seam-a", status="in_progress")
    # Assert
    assert updated["status"] == "in_progress"


def test_comment_task_through_the_seam_appends_the_comment(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    backend.comment_task(store, "seam-a", "through the seam", by="alice")
    # Assert
    comments = _store.get_task(store, "seam-a")["comments"]
    assert comments[-1]["text"] == "through the seam"


def test_set_edge_through_the_seam_persists_the_depends_on_edge(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    backend.set_edge(
        store, action="add", kind="depends_on", source="seam-b", target="seam-a"
    )
    # Assert
    assert _store.get_task(store, "seam-b")["depends_on"] == ["seam-a"]


def test_summarize_tasks_through_the_seam_returns_a_dict(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    summary = backend.summarize_tasks(store)
    # Assert
    assert isinstance(summary, dict) and summary


def test_complete_task_through_the_seam_marks_the_card_done(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    backend.complete_task(store, "seam-a", by="alice")
    # Assert
    assert _store.get_task(store, "seam-a")["status"] == "done"


def test_reassign_task_through_the_seam_returns_the_new_owner(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    reassigned = backend.reassign_task(store, "seam-b", "carol", by="seam-tester")
    # Assert
    assert reassigned["task"]["assignee"] == "carol"


def test_reassign_task_through_the_seam_persists_the_new_owner(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    backend.reassign_task(store, "seam-b", "carol", by="seam-tester")
    # Assert
    assert _store.get_task(store, "seam-b")["assignee"] == "carol"


# --------------------------------------------------------------------------- #
# 3. delete / restore                                                         #
# --------------------------------------------------------------------------- #


def test_delete_task_through_the_seam_returns_the_removed_card(store):
    # Arrange
    backend = get_backend()
    # Act
    removed = _seed_and_delete(backend, store)
    # Assert
    assert removed["removed"]["id"] == "seam-del"


def test_deleted_card_is_gone_from_the_store(store):
    # Arrange
    backend = get_backend()
    # Act
    _seed_and_delete(backend, store)
    # Assert
    with pytest.raises(Exception):
        _store.get_task(store, "seam-del")


def test_restore_task_through_the_seam_brings_the_card_back(store):
    # Arrange
    backend = get_backend()
    removed = _seed_and_delete(backend, store)
    # Act
    backend.restore_task(store, task=removed["removed"], refs=removed.get("refs"))
    # Assert
    assert _store.get_task(store, "seam-del")["title"] == "To delete"


# --------------------------------------------------------------------------- #
# 4. help wait / clear                                                        #
# --------------------------------------------------------------------------- #


def test_help_wait_through_the_seam_returns_a_blocked_card(store):
    # Arrange
    backend = get_backend()
    # Act
    card = backend.help_wait(store, "seam-agent", question="which option?")
    # Assert
    assert card["status"] == "blocked"


def test_help_wait_through_the_seam_sets_the_operator_blocker(store):
    # Arrange
    backend = get_backend()
    # Act
    card = backend.help_wait(store, "seam-agent", question="which option?")
    # Assert
    assert _store.get_task(store, card["id"])["blocker"] == "operator-decision"


def test_help_clear_through_the_seam_reports_that_it_cleared(store):
    # Arrange
    backend = get_backend()
    backend.help_wait(store, "seam-agent", question="which option?")
    # Act
    cleared = backend.help_clear(store, "seam-agent")
    # Assert
    assert cleared["cleared"] is True


def test_help_clear_through_the_seam_marks_the_card_done(store):
    # Arrange
    backend = get_backend()
    card = backend.help_wait(store, "seam-agent", question="which option?")
    # Act
    backend.help_clear(store, "seam-agent")
    # Assert
    assert _store.get_task(store, card["id"])["status"] == "done"


# --------------------------------------------------------------------------- #
# 5. dm + inbox                                                               #
# --------------------------------------------------------------------------- #


def test_dm_send_through_the_seam_records_both_peers(store):
    # Arrange
    backend = get_backend()
    # Act
    record = backend.dm_send("seam-alice", "seam-bob", "hello", store=store)
    # Assert
    assert record["from"] == "seam-alice" and record["to"] == "seam-bob"


def test_dm_list_through_the_seam_returns_the_sent_body(store):
    # Arrange
    backend = get_backend()
    backend.dm_send("seam-alice", "seam-bob", "hello", store=store)
    # Act
    thread = backend.dm_list("seam-bob", peer="seam-alice", ack=True, store=store)
    # Assert
    assert [m["body"] for m in thread["messages"]] == ["hello"]


def test_dm_list_through_the_seam_names_the_requested_peer(store):
    # Arrange
    backend = get_backend()
    backend.dm_send("seam-alice", "seam-bob", "hello", store=store)
    # Act
    thread = backend.dm_list("seam-bob", peer="seam-alice", ack=True, store=store)
    # Assert
    assert thread["peer"] == "seam-alice"


def test_poll_notifications_through_the_seam_names_the_agent(store):
    # Arrange
    backend = get_backend()
    backend.dm_send("seam-alice", "seam-bob", "hello", store=store)
    # Act
    inbox = backend.poll_notifications("seam-bob", store=store)
    # Assert
    assert inbox["agent"] == "seam-bob"


def test_poll_notifications_through_the_seam_returns_a_list(store):
    # Arrange
    backend = get_backend()
    backend.dm_send("seam-alice", "seam-bob", "hello", store=store)
    # Act
    inbox = backend.poll_notifications("seam-bob", store=store)
    # Assert
    assert isinstance(inbox["notifications"], list)


# EOF
