#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Size / burst guards for the scitex-todo channel push path.

Regression coverage for the 2026-07-02 incident: 180 solver apptainer
containers died on boot with ``JSON message exceeded maximum buffer size of
1048576 bytes`` when an oversized scitex-todo channel push overflowed the SDK's
1 MB stdio reader. Two guards are pinned here:

* :func:`build_channel_params` truncates an oversized ``content`` body to
  ``MAX_CONTENT_BYTES`` (UTF-8, multibyte-safe) with a "see the card" pointer.
* :func:`drain_once` pushes at most ``MAX_PUSH_PER_DRAIN`` records per call, so
  a large unseen backlog can never burst all at once on first connect.

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` YAML store,
real :mod:`scitex_cards._inbox` enqueue/poll/ack, and a real in-process async
``send`` recorder. Async seams are driven with ``asyncio.run`` (the repo has no
pytest-asyncio).
"""

from __future__ import annotations

import asyncio

import pytest

from scitex_cards import _inbox
from scitex_cards._channel_guard import MAX_CONTENT_BYTES, MAX_PUSH_PER_DRAIN
from scitex_cards._mcp_channel import build_channel_params, drain_once


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


class _SendRecorder:
    """A real async ``send`` callable — records every pushed params payload."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, params: dict) -> None:
        self.calls.append(params)


def _oversized_ascii_content(card_id="c42"):
    """Build params for an ASCII body well over the cap; return the content."""
    body = "x" * (MAX_CONTENT_BYTES + 5000)
    return build_channel_params({"body": body, "card_id": card_id})["content"]


def _oversized_multibyte_content(card_id="c7"):
    """Build params for a body of 3-byte UTF-8 chars, far over the cap.

    A naive byte-slice would split a multibyte char and produce invalid UTF-8.
    """
    body = "あ" * MAX_CONTENT_BYTES
    return build_channel_params({"body": body, "card_id": card_id})["content"]


# --------------------------------------------------------------------------- #
# build_channel_params — body size cap                                        #
# --------------------------------------------------------------------------- #
def test_oversized_body_is_truncated_within_the_cap():
    # Arrange
    # Act
    content = _oversized_ascii_content()
    # Assert — the FINAL content (prefix + suffix) fits the cap.
    assert len(content.encode("utf-8")) <= MAX_CONTENT_BYTES


def test_oversized_body_ends_with_the_board_pointer():
    # Arrange
    # Act
    content = _oversized_ascii_content()
    # Assert
    assert content.endswith("on the board]")


def test_oversized_body_says_it_was_truncated():
    # Arrange
    # Act
    content = _oversized_ascii_content()
    # Assert
    assert "truncated" in content


def test_oversized_body_pointer_names_the_card():
    # Arrange
    # Act
    content = _oversized_ascii_content()
    # Assert
    assert "c42" in content


def test_oversized_multibyte_body_fits_the_cap():
    # Arrange
    # Act
    content = _oversized_multibyte_content()
    # Assert
    assert len(content.encode("utf-8")) <= MAX_CONTENT_BYTES


def test_oversized_multibyte_body_is_char_boundary_safe():
    # Arrange
    # Act
    content = _oversized_multibyte_content()
    # Assert — round-trips ⇒ no split multibyte char.
    assert content.encode("utf-8").decode("utf-8") == content


def test_oversized_multibyte_body_ends_with_the_board_pointer():
    # Arrange
    # Act
    content = _oversized_multibyte_content()
    # Assert
    assert content.endswith("on the board]")


def test_oversized_multibyte_body_pointer_names_the_card():
    # Arrange
    # Act
    content = _oversized_multibyte_content()
    # Assert
    assert "c7" in content


def test_oversized_body_without_card_id_still_fits_the_cap():
    # Arrange
    body = "y" * (MAX_CONTENT_BYTES + 100)
    # Act
    content = build_channel_params({"body": body})["content"]  # no card_id
    # Assert
    assert len(content.encode("utf-8")) <= MAX_CONTENT_BYTES


def test_oversized_body_without_card_id_uses_generic_pointer():
    # Arrange
    body = "y" * (MAX_CONTENT_BYTES + 100)
    # Act
    content = build_channel_params({"body": body})["content"]  # no card_id
    # Assert
    assert content.endswith("[truncated — see the board]")


def test_normal_body_is_passed_through_unchanged():
    # Arrange
    body = "a short normal notification body"
    # Act
    params = build_channel_params({"body": body, "card_id": "c1"})
    # Assert
    assert params["content"] == body


def test_normal_body_carries_no_truncation_pointer():
    # Arrange
    body = "a short normal notification body"
    # Act
    params = build_channel_params({"body": body, "card_id": "c1"})
    # Assert
    assert "truncated" not in params["content"]


def test_truncated_params_meta_values_all_strings():
    # Arrange
    body = "z" * (MAX_CONTENT_BYTES + 10)
    # Act
    params = build_channel_params(
        {
            "body": body,
            "card_id": "c9",
            "event_type": "reassigned",
            "actor": "bob",
            "ts": "2026-07-02T00:00:00Z",
            "id": "n_1",
        }
    )
    # Assert
    for key, value in params["meta"].items():
        assert isinstance(value, str), f"meta[{key!r}] is {type(value)} not str"


# --------------------------------------------------------------------------- #
# drain_once — per-drain batch cap                                            #
# --------------------------------------------------------------------------- #
def _enqueue_n(agent, n, store, *, start=0):
    for i in range(start, start + n):
        rec = _inbox.enqueue(
            agent,
            event_type="reassigned",
            card_id=f"c{i}",
            body=f"body {i}",
            actor="bob",
            ts=f"2026-07-02T00:00:{i:02d}Z",
            store=store,
        )
        assert rec, f"enqueue {i} failed"


_BURST_EXTRA = 7
_BURST_TOTAL = MAX_PUSH_PER_DRAIN + _BURST_EXTRA


@pytest.fixture(scope="module")
def burst(tmp_path_factory):
    """One over-cap enqueue + two drains, shared by every burst assertion.

    Module-scoped on purpose: the scenario costs ~57 store writes, and each
    assertion below reads a different field of the SAME run. Re-running it
    per test would multiply that cost without testing anything new — the run
    is a pure observation, nothing mutates it.
    """
    return _drain_a_burst_twice(tmp_path_factory.mktemp("burst"))


def _drain_a_burst_twice(tmp_path, agent="agent-burst"):
    """Enqueue more than the cap, then drain twice; return everything seen."""
    store = _store(tmp_path)
    _enqueue_n(agent, _BURST_TOTAL, store)
    recorder1 = _SendRecorder()
    pushed1 = asyncio.run(drain_once(agent, recorder1, store=store))
    pending = _inbox.poll_inbox(agent, unseen_only=True, mark_seen=False, store=store)
    recorder2 = _SendRecorder()
    pushed2 = asyncio.run(drain_once(agent, recorder2, store=store))
    left = _inbox.poll_inbox(agent, unseen_only=True, mark_seen=False, store=store)
    return {
        "pushed1": pushed1,
        "recorder1": recorder1,
        "pending_after_first": pending,
        "pushed2": pushed2,
        "recorder2": recorder2,
        "left": left,
    }


def test_first_drain_pushes_exactly_the_cap(burst):
    # Arrange
    run = burst
    # Act
    # (the drains already ran in the shared fixture)
    # Assert
    assert run["pushed1"] == MAX_PUSH_PER_DRAIN


def test_first_drain_sends_exactly_the_cap_many_payloads(burst):
    # Arrange
    run = burst
    # Act
    # (the drains already ran in the shared fixture)
    # Assert
    assert len(run["recorder1"].calls) == MAX_PUSH_PER_DRAIN


def test_first_drain_acks_only_what_it_pushed(burst):
    # Arrange
    run = burst
    # Act
    # (the drains already ran in the shared fixture)
    # Assert — exactly the remainder is still unseen.
    assert len(run["pending_after_first"]) == _BURST_EXTRA


def test_second_drain_reports_delivering_the_remainder(burst):
    # Arrange
    run = burst
    # Act
    # (the drains already ran in the shared fixture)
    # Assert
    assert run["pushed2"] == _BURST_EXTRA


def test_second_drain_sends_the_remaining_payloads(burst):
    # Arrange
    run = burst
    # Act
    # (the drains already ran in the shared fixture)
    # Assert
    assert len(run["recorder2"].calls) == _BURST_EXTRA


def test_every_enqueued_record_is_delivered_across_the_drains(burst):
    # Arrange
    run = burst
    # Act
    # (the drains already ran in the shared fixture)
    # Assert
    bodies = [c["content"] for c in run["recorder1"].calls + run["recorder2"].calls]
    assert len(bodies) == _BURST_TOTAL


def test_no_record_is_delivered_twice_across_the_drains(burst):
    # Arrange
    run = burst
    # Act
    # (the drains already ran in the shared fixture)
    # Assert
    bodies = [c["content"] for c in run["recorder1"].calls + run["recorder2"].calls]
    assert len(set(bodies)) == _BURST_TOTAL


def test_nothing_is_left_unseen_after_the_second_drain(burst):
    # Arrange
    run = burst
    # Act
    # (the drains already ran in the shared fixture)
    # Assert
    assert run["left"] == []


def test_drain_under_the_cap_reports_pushing_everything(tmp_path):
    # Arrange
    store = _store(tmp_path)
    agent = "agent-small"
    _enqueue_n(agent, 3, store)
    recorder = _SendRecorder()
    # Act
    pushed = asyncio.run(drain_once(agent, recorder, store=store))
    # Assert
    assert pushed == 3


def test_drain_under_the_cap_sends_every_payload(tmp_path):
    # Arrange
    store = _store(tmp_path)
    agent = "agent-small2"
    _enqueue_n(agent, 3, store)
    recorder = _SendRecorder()
    # Act
    asyncio.run(drain_once(agent, recorder, store=store))
    # Assert
    assert len(recorder.calls) == 3


# --------------------------------------------------------------------------- #
# DM wire shape — fleet convention (scitex-dev spec v1): a dm record must     #
# render a2a-compatible: source=<sender>, conversation_id=<thread key>.       #
# --------------------------------------------------------------------------- #
_DM_RECORD = {
    "id": "m_abc123def456",
    "event_type": "dm",
    "card_id": "dm:neurovista::operator",
    "body": "please check the compute queue",
    "actor": "operator",
    "ts": "2026-07-07T12:00:00Z",
}

_NON_DM_RECORD = {
    "id": "n_1",
    "event_type": "reminder",
    "card_id": "some-card",
    "body": "digest",
    "actor": "notifyd",
    "ts": "2026-07-07T12:00:00Z",
}

_DM_RECORD_NO_ACTOR = {
    "id": "m_x",
    "event_type": "dm",
    "card_id": "dm:a::b",
    "body": "b",
    "actor": "",
    "ts": "2026-07-07T12:00:00Z",
}


def test_dm_record_renders_the_sender_as_source():
    # Arrange
    rec = dict(_DM_RECORD)
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert meta["source"] == "operator", "DM must render the SENDER as source"


def test_dm_record_renders_the_thread_key_as_conversation_id():
    # Arrange
    rec = dict(_DM_RECORD)
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert meta["conversation_id"] == "dm:neurovista::operator"


def test_dm_record_renders_the_record_id_as_msg_id():
    # Arrange
    rec = dict(_DM_RECORD)
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert meta["msg_id"] == "m_abc123def456"


def test_dm_record_meta_values_are_all_strings():
    # Arrange
    rec = dict(_DM_RECORD)
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    for value in meta.values():
        assert isinstance(value, str)


def test_non_dm_record_keeps_the_channel_source_label():
    # Arrange
    rec = dict(_NON_DM_RECORD)
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert meta["source"] == "stodo", "non-DM keeps the configured channel label"


def test_non_dm_record_carries_no_conversation_id():
    # Arrange
    rec = dict(_NON_DM_RECORD)
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert "conversation_id" not in meta


def test_dm_record_missing_actor_falls_back_to_channel_source():
    # Arrange
    rec = dict(_DM_RECORD_NO_ACTOR)
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert meta["source"] == "stodo"


def test_dm_record_missing_actor_still_carries_the_thread_key():
    # Arrange
    rec = dict(_DM_RECORD_NO_ACTOR)
    # Act
    meta = build_channel_params(rec)["meta"]
    # Assert
    assert meta["conversation_id"] == "dm:a::b"


# EOF
