#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C2 hook-bus tests: per-plugin wall-time budget + canonical card-event.

Split out of ``test__hooks.py`` (which would otherwise blow the file
line cap). Covers the two C2 problems:

  1. A slow/hung entry-point plugin can NEVER hang dispatch — each
     handler runs under a wall-time budget, and ordering + mutation +
     critical-abort contracts are preserved.
  2. The C1 canonical ``card-event`` kind is accepted by
     :func:`event_validate` (inner type in EVENT_TYPES, card_id
     required; fail-loud otherwise).

No mocks (STX-NM / PA-306): real fake entry-point objects are injected
through the dispatcher's ``entry_points=`` seam. AAA pattern.
"""

from __future__ import annotations

import contextlib
import threading
import time

import pytest

from scitex_cards._hooks import (
    PLUGIN_TIMEOUT_ENV,
    HookEventError,
    dispatch_event,
    event_validate,
)

# === card-event validation (C1 canonical envelope) ========================


def test_event_validate_accepts_well_formed_card_event():
    # Arrange — a C1 canonical envelope: inner type in EVENT_TYPES +
    # card_id present.
    payload = {"kind": "card-event", "type": "completed", "card_id": "card-1"}
    # Act
    out = event_validate(payload)
    # Assert
    assert out["type"] == "completed"


def test_event_validate_card_event_requires_card_id():
    # Arrange — fail-loud: a card-event with no card_id.
    bad = {"kind": "card-event", "type": "completed"}
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(bad)


def test_event_validate_card_event_rejects_unknown_inner_type():
    # Arrange — fail-loud: inner type not in EVENT_TYPES.
    bad = {"kind": "card-event", "type": "frobnicated", "card_id": "card-1"}
    # Act
    # Assert
    with pytest.raises(HookEventError):
        event_validate(bad)


def test_event_validate_card_event_passes_extra_envelope_fields_through():
    # Arrange — the canonical envelope carries repo/sha/etc.; the
    # validator must pass them through untouched for plugins.
    payload = {
        "kind": "card-event",
        "type": "pushed",
        "card_id": "card-1",
        "repo": "owner/repo",
        "sha": "deadbeef",
    }
    # Act
    out = event_validate(payload)
    # Assert
    assert out["repo"] == "owner/repo"


# === per-plugin wall-time budget (the headline fix) =======================


class _SlowEP:
    """Entry point whose handler sleeps far longer than the budget."""

    name = "slow-plugin"

    def __init__(self, sleep_s: float):
        self._sleep_s = sleep_s

    def load(self):
        sleep_s = self._sleep_s

        def _slow(_event):
            time.sleep(sleep_s)

        return _slow


class _RecorderEP:
    """Entry point whose handler records that it ran (later in chain)."""

    name = "z-recorder"  # name sorts AFTER slow-plugin at equal priority

    def __init__(self, sink: list):
        self._sink = sink

    def load(self):
        sink = self._sink

        def _record(_event):
            sink.append("ran")

        return _record


class _CriticalSlowEP:
    """CRITICAL entry point (priority=10) whose handler outlives any budget."""

    name = "critical-slow"

    def load(self):
        def _fn(_event):
            time.sleep(30.0)

        _fn.priority = 10
        _fn.critical = True
        return _fn


class _CriticalRaiseEP:
    """CRITICAL entry point (priority=10) whose handler raises immediately."""

    name = "critical-raise"

    def load(self):
        def _fn(_event):
            raise RuntimeError("boom")

        _fn.priority = 10
        _fn.critical = True
        return _fn


class _LaterEP:
    """Fast entry point (priority=200) that records that it ran."""

    name = "later"

    def __init__(self, sink: list):
        self._sink = sink

    def load(self):
        sink = self._sink

        def _fn(_event):
            sink.append("ran")

        _fn.priority = 200
        return _fn


def _push_event() -> dict:
    return event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "develop",
            "commit_sha": "c2sha",
            "card_ids": [],
        }
    )


def test_slow_plugin_does_not_hang_dispatch(env):
    # a tiny budget against a handler that sleeps 30s
    # Arrange
    env.set(PLUGIN_TIMEOUT_ENV, "0.2")
    event = _push_event()
    started = time.monotonic()
    # Act
    dispatch_event(event, entry_points=[_SlowEP(30.0)])
    elapsed = time.monotonic() - started
    # Assert — bounded return: ~the budget, NOT the handler's 30s.
    assert elapsed < 5.0


def test_slow_plugin_records_a_timeout_flagged_error(env):
    # a tiny budget against a handler that sleeps 30s
    # Arrange
    env.set(PLUGIN_TIMEOUT_ENV, "0.2")
    event = _push_event()
    # Act
    summary = dispatch_event(event, entry_points=[_SlowEP(30.0)])
    # Assert — flagged as a TIMEOUT, not an anonymous plugin failure.
    assert summary["plugin_errors"][0]["timeout"] is True


def test_later_handler_still_runs_after_a_timed_out_handler(env):
    # Arrange — a slow (non-critical) handler precedes a fast recorder.
    env.set(PLUGIN_TIMEOUT_ENV, "0.2")
    sink: list = []
    event = _push_event()
    # Act
    dispatch_event(event, entry_points=[_SlowEP(30.0), _RecorderEP(sink)])
    # Assert — the chain continued; the later handler ran.
    assert sink == ["ran"]


def test_ordering_and_mutation_preserved_in_bounded_mode(env):
    # Arrange — priority=10 mutates event["owner"]; priority=200 asserts
    # it sees the mutation. Both complete within the budget.
    env.set(PLUGIN_TIMEOUT_ENV, "5.0")
    seen: dict = {}

    class _OwnerMapEP:
        name = "owner-map"

        def load(self):
            def _fn(event):
                event["owner"] = "agent-x"

            _fn.priority = 10
            return _fn

    class _DeliveryEP:
        name = "delivery"

        def load(self):
            def _fn(event):
                seen["owner"] = event.get("owner")

            _fn.priority = 200
            return _fn

    event = _push_event()
    # Act — pass delivery FIRST in the list to prove sort (not list)
    # order drives execution.
    dispatch_event(event, entry_points=[_DeliveryEP(), _OwnerMapEP()])
    # Assert — the priority=200 handler saw the priority=10 mutation.
    assert seen["owner"] == "agent-x"


def test_critical_timeout_aborts_the_dispatch_chain(env):
    # a CRITICAL slow handler (priority=10) precedes a fast recorder
    # (priority=200); the critical timeout must abort the whole chain
    # Arrange
    env.set(PLUGIN_TIMEOUT_ENV, "0.2")
    sink: list = []
    event = _push_event()
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(Exception):
        dispatch_event(event, entry_points=[_CriticalSlowEP(), _LaterEP(sink)])


def test_critical_timeout_stops_the_later_handler(env):
    # the other half of the abort contract: the priority=200 handler
    # queued behind the critical timeout must never run
    # Arrange
    env.set(PLUGIN_TIMEOUT_ENV, "0.2")
    sink: list = []
    event = _push_event()
    # Act
    with contextlib.suppress(Exception):
        dispatch_event(event, entry_points=[_CriticalSlowEP(), _LaterEP(sink)])
    # Assert — the chain aborted before the later handler could record.
    assert sink == []


def test_critical_raise_aborts_the_dispatch_chain(env):
    # regression guard: in BOUNDED mode a CRITICAL handler that RAISES must
    # still abort the chain (the pre-C2 contract)
    # Arrange
    env.set(PLUGIN_TIMEOUT_ENV, "5.0")
    sink: list = []
    event = _push_event()
    # Act
    # Assert — the raise IS the behaviour; act and assert are one statement.
    with pytest.raises(Exception):
        dispatch_event(event, entry_points=[_CriticalRaiseEP(), _LaterEP(sink)])


def test_critical_raise_stops_the_later_handler(env):
    # regression guard, second half: the priority=200 handler queued behind
    # the raising critical handler must never run
    # Arrange
    env.set(PLUGIN_TIMEOUT_ENV, "5.0")
    sink: list = []
    event = _push_event()
    # Act
    with contextlib.suppress(Exception):
        dispatch_event(event, entry_points=[_CriticalRaiseEP(), _LaterEP(sink)])
    # Assert — the chain aborted before the later handler could record.
    assert sink == []


def test_timeout_disabled_runs_inline_legacy_behavior(env):
    # Arrange — budget <= 0 disables bounding; a fast handler runs
    # inline (no worker thread). Prove it still executes + mutates.
    env.set(PLUGIN_TIMEOUT_ENV, "0")
    seen: dict = {}

    class _InlineEP:
        name = "inline"

        def load(self):
            def _fn(event):
                seen["thread"] = threading.current_thread().name

            return _fn

    event = _push_event()
    # Act
    dispatch_event(event, entry_points=[_InlineEP()])
    # Assert — ran on the MAIN thread (inline, not a worker).
    assert seen["thread"] == threading.current_thread().name
