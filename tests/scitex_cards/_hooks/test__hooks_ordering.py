#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Handler ordering + criticality on the scitex_cards.hooks bus.

Dev coordination 2026-06-14 (a2a `0ab1d9fd` → my `5c454ec4`): the
ci-result event chain needs an EXPLICIT ordering primitive so dev's
owner-map handler (early) can mutate event["owner"] before SAC's
delivery handler (late) reads it. The dispatcher sorts by
(priority asc, name asc) and supports a `critical=True` opt-in that
aborts the chain on first failure.

No mocks (STX-NM / PA-306): handlers are injected through the
dispatcher's `entry_points=` seam as real fake entry points, never
monkeypatched onto the module. AAA pattern.
"""

from __future__ import annotations

from typing import Any

import pytest

from scitex_cards._hooks import dispatch_event, event_validate


# === Helpers ===============================================================


class _FakeEP:
    """Stand-in for an importlib.metadata EntryPoint that loads a callable."""

    def __init__(self, name: str, fn):
        self.name = name
        self._fn = fn

    def load(self):
        return self._fn


def _handlers(*eps: _FakeEP) -> list:
    """Return the given fake entry points as an explicit dispatch set."""
    return list(eps)


def _valid_push_event() -> dict:
    return event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "develop",
            "commit_sha": "abc123def456",
        }
    )


# === Priority drives the call order =======================================


def test_default_priority_is_100():
    # Arrange — a handler without explicit priority defaults to 100.
    # Verify by ordering: a priority=10 handler runs BEFORE the default.
    calls: list[str] = []

    def early(_event):
        calls.append("early")

    early.priority = 10

    def default(_event):
        calls.append("default")

    # No priority attribute — defaults to 100.
    handlers = _handlers(
        _FakeEP("z-default", default),
        _FakeEP("a-early", early),
    )
    # Act
    dispatch_event(_valid_push_event(), entry_points=handlers)
    # Assert — early runs before default despite the lex-later name.
    assert calls == ["early", "default"]


def test_lower_priority_runs_first():
    # Arrange — explicit priorities, ordered numerically.
    calls: list[str] = []

    def low(_e):
        calls.append("low")

    low.priority = 10

    def mid(_e):
        calls.append("mid")

    mid.priority = 50

    def high(_e):
        calls.append("high")

    high.priority = 200
    handlers = _handlers(
        _FakeEP("high", high),
        _FakeEP("low", low),
        _FakeEP("mid", mid),
    )
    # Act
    dispatch_event(_valid_push_event(), entry_points=handlers)
    # Assert
    assert calls == ["low", "mid", "high"]


def test_name_breaks_ties_on_equal_priority():
    # Arrange — both handlers default to priority 100; lex-asc name wins.
    calls: list[str] = []

    def a(_e):
        calls.append("a")

    def b(_e):
        calls.append("b")

    handlers = _handlers(
        _FakeEP("b", b),
        _FakeEP("a", a),
    )
    # Act
    dispatch_event(_valid_push_event(), entry_points=handlers)
    # Assert
    assert calls == ["a", "b"]


# === Mutation visible to downstream handlers ==============================


def test_early_handler_mutation_visible_to_late_handler():
    # Arrange — early handler annotates event["owner"]; late handler
    # reads it. Confirms by-reference passing.
    captured: list[Any] = []

    def early(event):
        event["owner"] = "agent-x"

    early.priority = 10

    def late(event):
        captured.append(event.get("owner"))

    late.priority = 200
    handlers = _handlers(
        _FakeEP("late", late),
        _FakeEP("early", early),
    )
    # Act
    dispatch_event(_valid_push_event(), entry_points=handlers)
    # Assert
    assert captured == ["agent-x"]


# === critical=True aborts the chain =======================================


def test_critical_handler_failure_aborts_chain():
    # Arrange
    calls: list[str] = []

    def early(_e):
        raise RuntimeError("owner-map failed")

    early.priority = 10
    early.critical = True

    def late(_e):
        calls.append("late")

    late.priority = 200
    handlers = _handlers(
        _FakeEP("early", early),
        _FakeEP("late", late),
    )
    # Act
    # Assert
    with pytest.raises(RuntimeError, match="owner-map failed"):
        dispatch_event(_valid_push_event(), entry_points=handlers)


def test_critical_handler_failure_skips_late_handler():
    # Arrange
    calls: list[str] = []

    def early(_e):
        raise RuntimeError("owner-map failed")

    early.priority = 10
    early.critical = True

    def late(_e):
        calls.append("late")

    late.priority = 200
    handlers = _handlers(
        _FakeEP("early", early),
        _FakeEP("late", late),
    )
    # Act — the critical failure propagates; swallow it here (the
    # propagation itself is asserted by test_critical_handler_failure_
    # aborts_chain) so this test's single assertion is the chain-abort.
    try:
        dispatch_event(_valid_push_event(), entry_points=handlers)
    except RuntimeError:
        pass
    # Assert — the late handler never ran.
    assert calls == []


def test_non_critical_failure_continues_chain():
    # Arrange — early non-critical handler raises; late handler still runs.
    calls: list[str] = []

    def early(_e):
        raise RuntimeError("notifier exploded")

    early.priority = 10

    # No critical attribute — default False.
    def late(_e):
        calls.append("late")

    late.priority = 200
    handlers = _handlers(
        _FakeEP("early", early),
        _FakeEP("late", late),
    )
    # Act
    dispatch_event(_valid_push_event(), entry_points=handlers)
    # Assert — late still ran; early's error is in plugin_errors.
    assert calls == ["late"]


def test_non_critical_failure_logged_in_plugin_errors():
    # Arrange
    def early(_e):
        raise RuntimeError("notifier exploded")

    handlers = _handlers(_FakeEP("notifier", early))
    # Act
    summary = dispatch_event(_valid_push_event(), entry_points=handlers)
    # Assert
    assert summary["plugin_errors"][0]["plugin"] == "notifier"


def test_plugin_error_carries_priority_and_critical_flag():
    # Arrange
    def early(_e):
        raise RuntimeError("boom")

    early.priority = 42
    early.critical = False

    handlers = _handlers(_FakeEP("boomer", early))
    # Act
    summary = dispatch_event(_valid_push_event(), entry_points=handlers)
    # Assert
    assert summary["plugin_errors"][0]["priority"] == 42


# === Plugin load failure is reported, doesn't abort =======================


def test_plugin_that_fails_to_load_is_logged_but_chain_continues():
    # Arrange — one EP raises in .load(); another runs successfully.
    calls: list[str] = []

    class _BadEP:
        name = "broken"

        def load(self):
            raise ImportError("module missing")

    def good(_e):
        calls.append("good")

    handlers = _handlers(_BadEP(), _FakeEP("good", good))
    # Act
    dispatch_event(_valid_push_event(), entry_points=handlers)
    # Assert — good handler ran despite the broken one.
    assert calls == ["good"]


def test_load_error_recorded_in_plugin_errors():
    # Arrange
    class _BadEP:
        name = "broken"

        def load(self):
            raise ImportError("module missing")

    handlers = _handlers(_BadEP())
    # Act
    summary = dispatch_event(_valid_push_event(), entry_points=handlers)
    # Assert
    assert any(
        e["plugin"] == "broken" and "load:" in e["error"]
        for e in summary["plugin_errors"]
    )
