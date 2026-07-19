#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical card-event model + emit seam (foundation C1).

C1 of the card-event / notification foundation epic: a TYPED canonical
:class:`Event` + a single :func:`emit` wrapping the existing hook bus.
Pure model + thin emit, NO delivery here.

No mocks (STX-NM / PA-306) — the bus is exercised via the real
``entry_points=`` injection seam (a concrete fake handler), exactly like
the ``_hooks`` / ``card-message`` tests. AAA pattern.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from scitex_cards._events import (
    CARD_EVENT_KIND,
    EVENT_TYPES,
    Event,
    EventType,
    EventValidationError,
    emit,
)

# === Construction + defaults ===============================================

#: The thirteen constants the closed set is supposed to contain (rank_changed
#: joined with the v5-lite rank engine, ADR-0011 §1/§8).
_DECLARED_EVENT_TYPES = {
    EventType.CREATED,
    EventType.REASSIGNED,
    EventType.REASSIGNED_BATCH,
    EventType.STATUS_CHANGED,
    EventType.COMMENTED,
    EventType.COMPLETED,
    EventType.RANK_CHANGED,
    EventType.COMMITTED,
    EventType.PUSHED,
    EventType.MERGED,
    EventType.RELEASED,
    EventType.PULLED,
    EventType.DEPLOYED,
}


def test_event_type_constants_match_event_types_set():
    # Arrange
    declared = set(_DECLARED_EVENT_TYPES)

    # Act
    closed_set = set(EVENT_TYPES)

    # Assert — the closed set is exactly the declared constants.
    assert declared == closed_set


def test_event_types_set_has_exactly_thirteen_members():
    # Arrange
    # Act
    size = len(EVENT_TYPES)

    # Assert — a bare count, so ADDING a type without declaring it above fails.
    assert size == 13


def test_event_minimal_construction_sets_the_required_type():
    # Arrange
    # Act
    ev = Event(type=EventType.CREATED)

    # Assert
    assert ev.type == "created"


def test_event_minimal_construction_defaults_card_id_to_none():
    # Arrange
    # Act
    ev = Event(type=EventType.CREATED)

    # Assert
    assert ev.card_id is None


def test_event_minimal_construction_defaults_actor_to_none():
    # Arrange
    # Act
    ev = Event(type=EventType.CREATED)

    # Assert
    assert ev.actor is None


def test_event_minimal_construction_defaults_repo_to_none():
    # Arrange
    # Act
    ev = Event(type=EventType.CREATED)

    # Assert
    assert ev.repo is None


def test_event_minimal_construction_defaults_extra_to_an_empty_dict():
    # Arrange
    # Act
    ev = Event(type=EventType.CREATED)

    # Assert
    assert ev.extra == {}


def test_event_explicit_ts_is_preserved():
    # Arrange
    # Act
    ev = Event(type=EventType.CREATED, ts="2026-01-01T00:00:00Z")

    # Assert
    assert ev.ts == "2026-01-01T00:00:00Z"


# === ts auto-stamp is valid ISO ============================================


def test_event_ts_autostamped_when_absent():
    # Arrange
    # Act
    ev = Event(type=EventType.COMPLETED, card_id="c-1")

    # Assert — a non-empty string, not None and not a datetime.
    assert isinstance(ev.ts, str) and ev.ts


def test_the_autostamped_ts_is_timezone_aware_iso8601():
    # Arrange
    ev = Event(type=EventType.COMPLETED, card_id="c-1")

    # Act
    parsed = _dt.datetime.fromisoformat(ev.ts.replace("Z", "+00:00"))

    # Assert — UTC-aware, so consumers never have to guess the zone.
    assert parsed.tzinfo is not None


# === Bad type raises EventValidationError ==================================


def test_event_bad_type_raises_validation_error():
    # Arrange
    # Act
    # Assert
    with pytest.raises(EventValidationError):
        Event(type="not-a-real-type")


def test_the_bad_type_error_echoes_the_offending_value():
    # Arrange
    # Act — captured rather than `pytest.raises`: THAT it raises is pinned
    # above; this test asks only what the message says.
    try:
        Event(type="not-a-real-type")
        message = ""
    except EventValidationError as exc:
        message = str(exc)

    # Assert — fail-loud: the bad value is named.
    assert "not-a-real-type" in message


def test_event_validation_error_is_value_error():
    # Arrange
    # Act
    # Assert — subclass contract for broad except sites.
    with pytest.raises(ValueError):
        Event(type="bogus")


# === to_dict() shape =======================================================


def _pushed_envelope() -> dict:
    ev = Event(type=EventType.PUSHED, repo="o/r", branch="develop", sha="abc")
    return ev.to_dict()


def test_to_dict_has_the_stable_kind_discriminator():
    # Arrange
    # Act
    d = _pushed_envelope()

    # Assert
    assert d["kind"] == CARD_EVENT_KIND == "card-event"


def test_to_dict_carries_the_canonical_type():
    # Arrange
    # Act
    d = _pushed_envelope()

    # Assert
    assert d["type"] == "pushed"


def test_to_dict_carries_the_repo_field():
    # Arrange
    # Act
    d = _pushed_envelope()

    # Assert
    assert d["repo"] == "o/r"


def test_to_dict_carries_the_branch_field():
    # Arrange
    # Act
    d = _pushed_envelope()

    # Assert
    assert d["branch"] == "develop"


def test_to_dict_carries_the_sha_field():
    # Arrange
    # Act
    d = _pushed_envelope()

    # Assert
    assert d["sha"] == "abc"


def test_to_dict_always_carries_a_ts():
    # Arrange
    # Act
    d = _pushed_envelope()

    # Assert
    assert d["ts"]


def _card_level_envelope() -> dict:
    """A card-level event with no git fields."""
    return Event(type=EventType.CREATED, card_id="c-1").to_dict()


def test_to_dict_omits_a_none_repo():
    # Arrange
    # Act
    d = _card_level_envelope()

    # Assert — None-valued fields are absent from the envelope.
    assert "repo" not in d


def test_to_dict_omits_a_none_pr_url():
    # Arrange
    # Act
    d = _card_level_envelope()

    # Assert
    assert "pr_url" not in d


def test_to_dict_keeps_the_populated_card_id():
    # Arrange
    # Act
    d = _card_level_envelope()

    # Assert — omitting Nones must not omit real values.
    assert d["card_id"] == "c-1"


def _deployed_envelope_with_clobbering_extra() -> dict:
    ev = Event(
        type=EventType.DEPLOYED,
        repo="o/r",
        extra={"service": "web", "type": "should-not-win"},
    )
    return ev.to_dict()


def test_to_dict_merges_extra_into_the_envelope():
    # Arrange
    # Act
    d = _deployed_envelope_with_clobbering_extra()

    # Assert
    assert d["service"] == "web"


def test_to_dict_extra_cannot_clobber_a_known_key():
    # Arrange
    # Act
    d = _deployed_envelope_with_clobbering_extra()

    # Assert — extra appears, but cannot overwrite the canonical `type`.
    assert d["type"] == "deployed"


# === Ergonomic constructors ================================================


def test_card_created_constructor():
    # Arrange
    # Act
    ev = Event.card_created("c-1", actor="op")

    # Assert
    assert ev.type == "created" and ev.card_id == "c-1" and ev.actor == "op"


def test_reassigned_constructor_sets_type_and_card_id():
    # Arrange
    # Act
    ev = Event.reassigned("c-1", actor="op")

    # Assert
    assert ev.type == "reassigned" and ev.card_id == "c-1"


def test_status_changed_constructor():
    # Arrange
    # Act
    ev = Event.status_changed("c-1", actor="op")

    # Assert
    assert ev.type == "status_changed"


def test_commented_constructor_sets_the_type():
    # Arrange
    # Act
    ev = Event.commented("c-1", actor="op")

    # Assert
    assert ev.type == "commented"


def test_completed_constructor_sets_the_type():
    # Arrange
    # Act
    ev = Event.completed("c-1", actor="op")

    # Assert
    assert ev.type == "completed"


def test_committed_constructor_repo_level():
    # Arrange
    # Act
    ev = Event.committed(repo="o/r", sha="deadbeef")

    # Assert — a repo-level event carries no card.
    assert ev.type == "committed" and ev.card_id is None and ev.sha == "deadbeef"


def test_pushed_constructor_sets_type_and_branch():
    # Arrange
    # Act
    ev = Event.pushed(repo="o/r", branch="main", sha="abc")

    # Assert
    assert ev.type == "pushed" and ev.branch == "main"


def test_merged_constructor_sets_type_and_pr_url():
    # Arrange
    # Act
    ev = Event.merged(repo="o/r", pr_url="https://x/pull/1")

    # Assert
    assert ev.type == "merged" and ev.pr_url == "https://x/pull/1"


def test_released_constructor_sets_type_and_version():
    # Arrange
    # Act
    ev = Event.released(repo="o/r", version="1.2.3")

    # Assert
    assert ev.type == "released" and ev.version == "1.2.3"


def test_pulled_constructor_sets_type_and_repo():
    # Arrange
    # Act
    ev = Event.pulled(repo="o/r")

    # Assert
    assert ev.type == "pulled" and ev.repo == "o/r"


def test_deployed_constructor_sets_the_type():
    # Arrange
    # Act
    ev = Event.deployed(repo="o/r", service="api")

    # Assert
    assert ev.type == "deployed"


def test_deployed_constructor_tucks_service_into_extra():
    # Arrange
    ev = Event.deployed(repo="o/r", service="api")

    # Act
    d = ev.to_dict()

    # Assert — `service` is not a declared field, so it rides in `extra`.
    assert d["service"] == "api"


# === emit() wraps dispatch_event with the right envelope ===================


class _Capturing:
    """Concrete fake entry-point handler that records every event."""

    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(dict(event))  # shallow copy, see card-message test


class _FakeEP:
    def __init__(self, name: str, fn):
        self.name = name
        self._fn = fn

    def load(self):
        return self._fn


def _emit_a_push_into_a_capturing_handler() -> _Capturing:
    """Emit one pushed event through a real in-process handler."""
    sink = _Capturing()
    ev = Event.pushed(repo="o/r", branch="develop", sha="abc123")
    emit(ev, entry_points=[_FakeEP("captor", sink)])
    return sink


def test_emit_dispatches_exactly_one_event():
    # Arrange — a real in-process handler via the injection seam.
    # Act
    sink = _emit_a_push_into_a_capturing_handler()

    # Assert
    assert len(sink.events) == 1


def test_emit_dispatches_the_card_event_kind():
    # Arrange
    # Act
    sink = _emit_a_push_into_a_capturing_handler()

    # Assert
    assert sink.events[0]["kind"] == "card-event"


def test_emit_dispatches_the_events_type():
    # Arrange
    # Act
    sink = _emit_a_push_into_a_capturing_handler()

    # Assert
    assert sink.events[0]["type"] == "pushed"


def test_emit_dispatches_the_events_repo():
    # Arrange
    # Act
    sink = _emit_a_push_into_a_capturing_handler()

    # Assert
    assert sink.events[0]["repo"] == "o/r"


def test_emit_dispatches_the_events_sha():
    # Arrange
    # Act
    sink = _emit_a_push_into_a_capturing_handler()

    # Assert
    assert sink.events[0]["sha"] == "abc123"


def _emit_a_comment_into_a_capturing_handler() -> _Capturing:
    sink = _Capturing()
    emit(Event.commented("c-1", actor="op"), entry_points=[_FakeEP("captor", sink)])
    return sink


def test_emit_does_not_use_legacy_card_message_kind():
    # Arrange — C1 must NOT collide with the existing card-message kind.
    # Act
    sink = _emit_a_comment_into_a_capturing_handler()

    # Assert
    assert sink.events[0]["kind"] != "card-message"


def test_emit_uses_the_card_event_kind_for_a_comment():
    # Arrange
    # Act
    sink = _emit_a_comment_into_a_capturing_handler()

    # Assert
    assert sink.events[0]["kind"] == "card-event"


def test_emit_never_raises_even_when_handler_explodes():
    # Arrange — a real fake handler that raises, injected via the seam.
    def _bad(_event):
        raise RuntimeError("bus exploded")

    eps = [_FakeEP("bad", _bad)]

    # Act — emit must swallow the handler's failure.
    outcome = "returned"
    try:
        emit(Event.completed("c-1"), entry_points=eps)
    except Exception as exc:  # noqa: BLE001 - the failure this test pins
        outcome = f"raised {exc!r}"

    # Assert — the producer is never broken by a bad consumer.
    assert outcome == "returned"
