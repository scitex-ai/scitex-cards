#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the DM reaction event log (:mod:`scitex_cards._reactions`).

The contract these pin:

  - a reaction is an APPEND-ONLY EVENT, never a field on the message;
  - "un-reacting" APPENDS a ``remove`` event and deletes nothing;
  - the fold is last-writer-wins per ``(message_id, emoji, actor)``;
  - the writer REFUSES to shrink the log (operator's append-only ruling);
  - the message records in ``threads.json`` are not touched at all.

Real tmp store throughout, passed EXPLICITLY — never the resolved default,
which is the live fleet board. No mocks (STX-NM). AAA (STX-TQ002), one
assertion per test (STX-TQ007).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scitex_cards import _reactions
from scitex_cards._threads import append_message, get_thread, thread_key

THREAD = "dm:agent-x::operator"


@pytest.fixture()
def store(tmp_path: Path, env) -> Path:
    """A real tmp task store; the reaction sidecar lands next to it."""
    env.set("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "0")
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return path


@pytest.fixture()
def one_reaction(store: Path) -> Path:
    """``operator`` has reacted 👍 to message ``m_1``."""
    _reactions.append_reaction_event(
        thread=THREAD, message_id="m_1", actor="operator", emoji="👍", store=store
    )
    return store


@pytest.fixture()
def reaction_then_removed(one_reaction: Path) -> Path:
    """…and then un-reacted."""
    _reactions.append_reaction_event(
        thread=THREAD,
        message_id="m_1",
        actor="operator",
        emoji="👍",
        action="remove",
        store=one_reaction,
    )
    return one_reaction


def _raw_events(store: Path) -> list:
    path = _reactions.reactions_path(store)
    return json.loads(path.read_text(encoding="utf-8"))["reaction_events"]


# === the sidecar ===========================================================


def test_reactions_sidecar_sits_next_to_the_store(store: Path):
    # Arrange
    # Act
    path = _reactions.reactions_path(store)
    # Assert
    assert path == store.parent / "dm_reactions.json"


def test_reading_a_path_does_not_create_the_file(store: Path):
    # Arrange
    # Act
    path = _reactions.reactions_path(store)
    # Assert
    # threads_path() writes on a legacy migration; a path query must not.
    assert not path.exists()


def test_folding_an_absent_sidecar_is_empty(store: Path):
    # Arrange
    # Act
    folded = _reactions.thread_reactions(THREAD, store=store)
    # Assert
    assert folded == {}


# === append ================================================================


def test_append_stores_exactly_one_event(one_reaction: Path):
    # Arrange
    # Act
    events = _raw_events(one_reaction)
    # Assert
    assert len(events) == 1


def test_append_records_the_action_as_add(one_reaction: Path):
    # Arrange
    events = _raw_events(one_reaction)
    # Act
    action = events[0]["action"]
    # Assert
    assert action == "add"


def test_append_mints_a_prefixed_event_id(one_reaction: Path):
    # Arrange
    events = _raw_events(one_reaction)
    # Act
    event_id = events[0]["id"]
    # Assert
    assert event_id.startswith("dmr_")


def test_append_rejects_an_unknown_action(store: Path):
    # Arrange
    # Act
    # Assert
    with pytest.raises(ValueError):
        _reactions.append_reaction_event(
            thread=THREAD,
            message_id="m_1",
            actor="operator",
            emoji="👍",
            action="explode",
            store=store,
        )


def test_append_rejects_an_empty_message_id(store: Path):
    # Arrange
    # Act
    # Assert
    with pytest.raises(ValueError):
        _reactions.append_reaction_event(
            thread=THREAD, message_id="", actor="operator", emoji="👍", store=store
        )


def test_append_rejects_an_overlong_emoji(store: Path):
    # Arrange
    # Act
    # Assert
    with pytest.raises(ValueError):
        _reactions.append_reaction_event(
            thread=THREAD,
            message_id="m_1",
            actor="operator",
            emoji="x" * (_reactions.MAX_EMOJI_LEN + 1),
            store=store,
        )


# === append-only ===========================================================


def test_removing_a_reaction_appends_rather_than_deletes(reaction_then_removed: Path):
    # Arrange
    # Act
    events = _raw_events(reaction_then_removed)
    # Assert
    # the `add` survives; the log grew, it did not shrink.
    assert len(events) == 2


def test_the_original_add_event_still_says_add(reaction_then_removed: Path):
    # Arrange
    events = _raw_events(reaction_then_removed)
    # Act
    first = events[0]["action"]
    # Assert
    assert first == "add"


def test_a_shrinking_write_is_refused(one_reaction: Path):
    # Arrange
    path = _reactions.reactions_path(one_reaction)
    # Act
    # Assert
    with pytest.raises(RuntimeError):
        _reactions._save_events_unlocked([], path, previous=1)


def test_a_refused_shrink_leaves_the_file_intact(one_reaction: Path):
    # Arrange
    path = _reactions.reactions_path(one_reaction)
    with pytest.raises(RuntimeError):
        _reactions._save_events_unlocked([], path, previous=1)
    # Act
    events = _raw_events(one_reaction)
    # Assert
    assert len(events) == 1


# === the fold ==============================================================


def test_fold_lists_the_actor_under_the_emoji(one_reaction: Path):
    # Arrange
    # Act
    folded = _reactions.thread_reactions(THREAD, store=one_reaction)
    # Assert
    assert folded == {"m_1": {"👍": ["operator"]}}


def test_fold_drops_an_emoji_once_its_last_actor_leaves(reaction_then_removed: Path):
    # Arrange
    # Act
    folded = _reactions.thread_reactions(THREAD, store=reaction_then_removed)
    # Assert
    # no zero-count chip is ever handed to the UI.
    assert folded == {}


def test_fold_keeps_the_other_actors_when_one_removes(store: Path):
    # Arrange
    for actor in ("operator", "agent-x"):
        _reactions.append_reaction_event(
            thread=THREAD, message_id="m_1", actor=actor, emoji="👍", store=store
        )
    _reactions.append_reaction_event(
        thread=THREAD,
        message_id="m_1",
        actor="operator",
        emoji="👍",
        action="remove",
        store=store,
    )
    # Act
    folded = _reactions.thread_reactions(THREAD, store=store)
    # Assert
    assert folded == {"m_1": {"👍": ["agent-x"]}}


def test_fold_is_idempotent_for_a_repeated_add(store: Path):
    # Arrange
    for _ in range(3):
        _reactions.append_reaction_event(
            thread=THREAD, message_id="m_1", actor="operator", emoji="👍", store=store
        )
    # Act
    folded = _reactions.thread_reactions(THREAD, store=store)
    # Assert
    # three taps, three audited events, ONE actor in the fold.
    assert folded == {"m_1": {"👍": ["operator"]}}


def test_fold_ignores_another_thread(store: Path):
    # Arrange
    _reactions.append_reaction_event(
        thread="dm:agent-y::operator",
        message_id="m_9",
        actor="operator",
        emoji="👍",
        store=store,
    )
    # Act
    folded = _reactions.thread_reactions(THREAD, store=store)
    # Assert
    assert folded == {}


def test_fold_orders_actors_first_reacted_first(store: Path):
    # Arrange
    for actor in ("agent-x", "operator"):
        _reactions.append_reaction_event(
            thread=THREAD, message_id="m_1", actor=actor, emoji="🎉", store=store
        )
    # Act
    folded = _reactions.thread_reactions(THREAD, store=store)
    # Assert
    assert folded["m_1"]["🎉"] == ["agent-x", "operator"]


def test_fold_skips_a_malformed_event():
    # Arrange
    events = [{"emoji": "👍", "actor": "operator"}]  # no message_id
    # Act
    folded = _reactions.fold_events(events)
    # Assert
    assert folded == {}


# === next_action ===========================================================


def test_next_action_adds_when_the_actor_has_not_reacted():
    # Arrange
    actors = ["agent-x"]
    # Act
    action = _reactions.next_action(actors, "operator")
    # Assert
    assert action == "add"


def test_next_action_removes_when_the_actor_already_reacted():
    # Arrange
    actors = ["agent-x", "operator"]
    # Act
    action = _reactions.next_action(actors, "operator")
    # Assert
    assert action == "remove"


# === isolation from the DM records =========================================


def test_reacting_does_not_touch_the_message_record(store: Path):
    # Arrange
    append_message("agent-x", "operator", "hello", store=store)
    before = get_thread("operator", "agent-x", store=store)
    _reactions.append_reaction_event(
        thread=thread_key("operator", "agent-x"),
        message_id=before[0]["id"],
        actor="operator",
        emoji="👍",
        store=store,
    )
    # Act
    after = get_thread("operator", "agent-x", store=store)
    # Assert
    # the v5 design freezes dm_messages; a reaction must not thaw it.
    assert after == before


def test_reacting_does_not_write_the_threads_sidecar(store: Path):
    # Arrange
    from scitex_cards._threads import threads_path

    append_message("agent-x", "operator", "hello", store=store)
    threads_file = threads_path(store)
    before = threads_file.stat().st_mtime_ns
    _reactions.append_reaction_event(
        thread=thread_key("operator", "agent-x"),
        message_id="m_1",
        actor="operator",
        emoji="👍",
        store=store,
    )
    # Act
    after = threads_file.stat().st_mtime_ns
    # Assert
    assert after == before


# EOF
