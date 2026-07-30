#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the delivery-channel registry (slice 1).

Real fake channels via the ``extra_providers=`` seam — NO mocks. Covers the
spec's required registry behaviour:
* dedup by channel name (first-wins),
* deterministic (sorted) order,
* a load-raising provider is SKIPPED and surfaced to stderr (fail-loud).

Also exercises the REAL installed ``log`` entry point so the pyproject
registration is verified end-to-end (the worktree is ``pip install -e .``).

One assertion per test (STX-TQ007); shared setup lives in the helpers below.
"""

from __future__ import annotations

from scitex_cards._delivery._registry import discover_channels

from ._fakes import RecorderChannel


class _NoNameChannel:
    """A real channel object whose ``name`` is empty — must be skipped."""

    name = ""

    def deliver(self, *, recipient, address, notification):  # pragma: no cover
        raise AssertionError("should never be called")


class _BoomEntryPoint:
    """A real entry-point-shaped object whose ``load()`` raises."""

    name = "boom"

    def load(self):
        raise ImportError("provider import blew up")


class _GoodEntryPoint:
    """A real entry-point-shaped object that loads a working factory."""

    name = "good"

    def load(self):
        return lambda: RecorderChannel(name="good")


def _discover_with_a_raising_entry_point():
    """Discover channels from one raising + one healthy entry point.

    Swaps the module's real iterator for a real list of real objects (NOT a
    mock) and restores it afterwards.
    """
    from scitex_cards._delivery import _registry

    original = _registry._iter_entry_points
    _registry._iter_entry_points = lambda group: [
        _BoomEntryPoint(),
        _GoodEntryPoint(),
    ]
    try:
        return _registry.discover_channels()
    finally:
        _registry._iter_entry_points = original


def test_extra_providers_are_registered_in_sorted_order():
    # Arrange
    # provided out of order; result must be name-sorted. Other
    # channels (the real installed `log` entry point) may also be present,
    # so check the injected ones' relative order among themselves.
    a = RecorderChannel(name="alpha")
    z = RecorderChannel(name="zeta")
    m = RecorderChannel(name="mid")
    # Act
    found = discover_channels(extra_providers=[z, a, m])
    # Assert
    injected = [k for k in found if k in {"alpha", "mid", "zeta"}]
    assert injected == ["alpha", "mid", "zeta"]


def test_duplicate_channel_name_keeps_the_first_registration():
    # Arrange
    first = RecorderChannel(name="dup")
    second = RecorderChannel(name="dup")
    # Act
    found = discover_channels(extra_providers=[first, second])
    # Assert
    assert found["dup"] is first


def test_duplicate_channel_name_is_warned_about_on_stderr(capsys):
    # Arrange
    first = RecorderChannel(name="dup")
    second = RecorderChannel(name="dup")
    discover_channels(extra_providers=[first, second])
    # Act
    err = capsys.readouterr().err
    # Assert
    assert "duplicate delivery channel name" in err


def test_duplicate_channel_warning_names_the_channel(capsys):
    # Arrange
    first = RecorderChannel(name="dup")
    second = RecorderChannel(name="dup")
    discover_channels(extra_providers=[first, second])
    # Act
    err = capsys.readouterr().err
    # Assert
    assert "dup" in err


def test_unnamed_channel_is_not_registered_at_all():
    # Arrange
    provider = _NoNameChannel()
    # Act
    found = discover_channels(extra_providers=[provider])
    # Assert
    # the real installed `log` entry point is still present.
    assert "" not in found


def test_unnamed_channel_skip_is_surfaced_on_stderr(capsys):
    # Arrange
    discover_channels(extra_providers=[_NoNameChannel()])
    # Act
    err = capsys.readouterr().err
    # Assert
    assert "no usable .name" in err


def test_load_raising_entry_point_does_not_hide_the_healthy_one():
    # Arrange
    # Act
    found = _discover_with_a_raising_entry_point()
    # Assert
    assert "good" in found


def test_load_raising_entry_point_is_skipped_from_the_registry():
    # Arrange
    # Act
    found = _discover_with_a_raising_entry_point()
    # Assert
    assert "boom" not in found


def test_load_raising_entry_point_is_surfaced_on_stderr(capsys):
    # Arrange
    _discover_with_a_raising_entry_point()
    # Act
    err = capsys.readouterr().err
    # Assert
    assert "failed to load delivery channel entry point" in err


def test_load_raising_entry_point_warning_names_the_provider(capsys):
    # Arrange
    _discover_with_a_raising_entry_point()
    # Act
    err = capsys.readouterr().err
    # Assert
    assert "boom" in err


def test_real_log_entry_point_is_discovered():
    # Arrange
    # the pyproject-registered `log` channel, installed for real.
    # Act
    found = discover_channels()
    # Assert
    assert "log" in found


def test_real_log_entry_point_keeps_its_name():
    # Arrange
    # Act
    found = discover_channels()
    # Assert
    assert found["log"].name == "log"


def test_real_telegram_entry_point_is_discovered():
    # Arrange
    # the slice-3 telegram channel; the registry instantiates it
    # from the entry point WITHOUT a token (lazy resolution).
    # Act
    found = discover_channels()
    # Assert
    assert "telegram" in found


def test_real_telegram_entry_point_keeps_its_name():
    # Arrange
    # Act
    found = discover_channels()
    # Assert
    assert found["telegram"].name == "telegram"


# EOF
