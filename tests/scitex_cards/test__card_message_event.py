#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`card-message` event emission from `comment_task`.

Lead a2a `1e8e33d0`, 2026-06-14 — Phase 6 chat surface becomes the
operator↔card↔owner+collaborators feedback channel. Every comment
landing via `comment_task` (whether from the chat panel, the
`scitex-todo comment` CLI, or the MCP tool) emits a `card-message`
event on the `scitex_cards.hooks` bus so SAC can a2a-fan it.

No mocks (STX-NM/PA-306). AAA pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scitex_cards._hooks import HookEventError, event_validate
from scitex_cards._store import add_task, comment_task

# === Validator branch for card-message =====================================


def test_validator_accepts_card_message_with_required_fields():
    # Arrange
    payload = {
        "kind": "card-message",
        "card_id": "c-1",
        "body": "hi",
    }
    # Act
    out = event_validate(payload)
    # Assert
    assert out["card_id"] == "c-1"


def test_validator_rejects_card_message_missing_card_id():
    # Arrange
    payload = {"kind": "card-message", "body": "hi"}
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(payload)


def test_validator_rejects_card_message_missing_body():
    # Arrange
    payload = {"kind": "card-message", "card_id": "c-1"}
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(payload)


def test_validator_rejects_card_message_collaborators_non_list():
    # Arrange
    payload = {
        "kind": "card-message",
        "card_id": "c-1",
        "body": "hi",
        "collaborators": "agent-a",  # must be a list
    }
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(payload)


def test_validator_rejects_card_message_collaborators_non_string_entry():
    # Arrange
    payload = {
        "kind": "card-message",
        "card_id": "c-1",
        "body": "hi",
        "collaborators": ["agent-a", 42],
    }
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(payload)


def test_validator_accepts_empty_collaborators_list():
    # Arrange — an isolated card with no prior comments has no
    # collaborators yet; that's valid.
    payload = {
        "kind": "card-message",
        "card_id": "c-1",
        "body": "hi",
        "collaborators": [],
    }
    # Act
    out = event_validate(payload)
    # Assert
    assert out["collaborators"] == []


def test_validator_card_message_does_not_inject_card_ids_plural():
    # Arrange — card-message uses singular `card_id`; the plural
    # `card_ids` (used by push/done) should NOT appear in the
    # normalized payload.
    payload = {"kind": "card-message", "card_id": "c-1", "body": "hi"}
    # Act
    out = event_validate(payload)
    # Assert
    assert "card_ids" not in out


# === comment_task emits card-message via the bus ===========================


class _Capturing:
    """Stand-in entry-point that records every event it receives."""

    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        # Store a SHALLOW copy so later in-place mutations elsewhere
        # don't fool the assertions.
        self.events.append(dict(event))


class _FakeEP:
    def __init__(self, name: str, fn):
        self.name = name
        self._fn = fn

    def load(self):
        return self._fn


class _Bus:
    """Real fake bus: a capturing handler + its entry-point list.

    Tests pass ``entry_points=bus.entry_points`` to ``comment_task`` so the
    emitted ``card-message`` event is delivered to ``sink`` and recorded in
    ``bus.events`` — a real in-process handler via the dispatcher's
    injection seam, no monkeypatch (PA-306).
    """

    def __init__(self):
        self.sink = _Capturing()
        self.entry_points = [_FakeEP("captor", self.sink)]

    @property
    def events(self):
        return self.sink.events

    def clear(self):
        self.sink.events.clear()


@pytest.fixture()
def bus():
    return _Bus()


def test_comment_task_emits_card_message_event(tmp_path: Path, bus):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", assignee="agent:test-suite")
    # Act
    comment_task(
        store=store,
        task_id="c-1",
        text="hello",
        by="operator",
        entry_points=bus.entry_points,
    )
    # Assert
    assert any(e.get("kind") == "card-message" for e in bus.events)


def test_card_message_event_carries_card_id_body_author(
    tmp_path: Path,
    bus,
):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", assignee="agent:test-suite")
    # Act
    comment_task(
        store=store,
        task_id="c-1",
        text="hello",
        by="operator",
        entry_points=bus.entry_points,
    )
    # Assert
    e = [x for x in bus.events if x.get("kind") == "card-message"][0]
    assert e["card_id"] == "c-1" and e["body"] == "hello" and e["author"] == "operator"


def test_card_message_owner_comes_from_agent_field(
    tmp_path: Path,
    bus,
):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", agent="proj-clew")
    # Act
    comment_task(
        store=store,
        task_id="c-1",
        text="hello",
        by="operator",
        entry_points=bus.entry_points,
    )
    # Assert
    e = [x for x in bus.events if x.get("kind") == "card-message"][0]
    assert e["owner"] == "proj-clew"


def test_card_message_owner_falls_back_to_assignee(
    tmp_path: Path,
    bus,
):
    # Arrange — no `agent` field; legacy `assignee` is the fallback.
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", assignee="proj-legacy")
    # Act
    comment_task(
        store=store,
        task_id="c-1",
        text="hello",
        by="operator",
        entry_points=bus.entry_points,
    )
    # Assert
    e = [x for x in bus.events if x.get("kind") == "card-message"][0]
    assert e["owner"] == "proj-legacy"


def test_card_message_owner_is_none_when_card_has_neither(
    tmp_path: Path,
    bus,
):
    # Arrange — naked card with no agent/assignee. add_task now REQUIRES an
    # owner (fail-loud), so seed a raw owner-less row straight into the
    # canonical DB to exercise the owner-is-None path (no owner ->
    # card_owner returns None).
    from conftest import seed_db_from_doc

    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    seed_db_from_doc(
        {"tasks": [{"id": "c-1", "title": "x", "status": "deferred"}]},
        os.environ["SCITEX_CARDS_DB"],
    )
    # Act
    comment_task(
        store=store,
        task_id="c-1",
        text="hello",
        by="operator",
        entry_points=bus.entry_points,
    )
    # Assert
    e = [x for x in bus.events if x.get("kind") == "card-message"][0]
    assert e["owner"] is None


def test_card_message_collaborators_excludes_owner(
    tmp_path: Path,
    bus,
):
    # Arrange — owner already in the conversation; the event should
    # NOT echo them in collaborators (SAC routes to owner directly).
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", agent="proj-clew")
    comment_task(
        store=store,
        task_id="c-1",
        text="first",
        by="proj-clew",
        entry_points=bus.entry_points,
    )
    bus.clear()
    # Act
    comment_task(
        store=store,
        task_id="c-1",
        text="second",
        by="operator",
        entry_points=bus.entry_points,
    )
    # Assert
    e = [x for x in bus.events if x.get("kind") == "card-message"][0]
    assert "proj-clew" not in e["collaborators"]


def test_card_message_collaborators_excludes_new_author(
    tmp_path: Path,
    bus,
):
    # Arrange — the new commenter should NOT appear in their own
    # event's collaborators (SAC would echo the message back at them).
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", assignee="agent:test-suite")
    comment_task(
        store=store,
        task_id="c-1",
        text="a",
        by="operator",
        entry_points=bus.entry_points,
    )
    bus.clear()
    # Act
    comment_task(
        store=store,
        task_id="c-1",
        text="b",
        by="operator",
        entry_points=bus.entry_points,
    )
    # Assert
    e = [x for x in bus.events if x.get("kind") == "card-message"][0]
    assert "operator" not in e["collaborators"]


def test_card_message_collaborators_lists_prior_commenters(
    tmp_path: Path,
    bus,
):
    # Arrange — three earlier authors, current author is different.
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", assignee="agent:test-suite")
    comment_task(
        store=store, task_id="c-1", text="a", by="alice", entry_points=bus.entry_points
    )
    comment_task(
        store=store, task_id="c-1", text="b", by="bob", entry_points=bus.entry_points
    )
    comment_task(
        store=store, task_id="c-1", text="c", by="alice", entry_points=bus.entry_points
    )  # dedupe
    bus.clear()
    # Act
    comment_task(
        store=store,
        task_id="c-1",
        text="d",
        by="operator",
        entry_points=bus.entry_points,
    )
    # Assert — alice + bob (deduped) appear, in original order.
    e = [x for x in bus.events if x.get("kind") == "card-message"][0]
    assert e["collaborators"] == ["alice", "bob"]


def test_comment_task_save_succeeds_even_when_bus_raises(tmp_path: Path):
    # Arrange — a real fake handler that raises, injected via the
    # `entry_points=` seam (no monkeypatch). The comment must still land
    # on disk because comment_task swallows bus errors.
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", assignee="agent:test-suite")

    def _bad(_event):
        raise RuntimeError("bus exploded")

    bad_eps = [_FakeEP("bad", _bad)]
    # Act — must NOT raise (comment_task swallows bus errors).
    comment_task(
        store=store,
        task_id="c-1",
        text="hello",
        by="operator",
        entry_points=bad_eps,
    )

    # Assert — read back, comment is present.
    from scitex_cards._model import load_tasks

    loaded = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    assert any(c.get("text") == "hello" for c in loaded.get("comments") or [])


def test_card_message_created_at_matches_comment_ts(
    tmp_path: Path,
    bus,
):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(store=store, id="c-1", title="x", assignee="agent:test-suite")
    # Act
    comment_task(
        store=store,
        task_id="c-1",
        text="hello",
        by="operator",
        entry_points=bus.entry_points,
    )
    # Assert — the event's created_at is the same ISO stamp the
    # comment carries (for downstream timeline reconstruction).
    from scitex_cards._model import load_tasks

    loaded = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    comment_ts = loaded["comments"][-1]["ts"]
    e = [x for x in bus.events if x.get("kind") == "card-message"][0]
    assert e["created_at"] == comment_ts
