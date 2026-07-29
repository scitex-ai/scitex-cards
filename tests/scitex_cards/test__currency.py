#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the CURRENCY gate (BARE-HOST behaviour: stale/broken installs ERROR).

``check_currency()`` delegates to ``scitex_dev.staleness.ensure_current`` when
scitex-dev is installed, and is a no-op otherwise (decoupling rule — see
``_currency.py``). Every case here fakes the optional dependency via
``sys.modules`` rather than requiring a real scitex-dev>=0.34.0 install or
touching the network, so these tests are deterministic regardless of what is
actually installed in the environment.

SCOPE: this file covers the BARE-HOST rail, where the gate RAISES. The gate's
behaviour is now conditional — BLOCK WHERE THE ACTOR CAN REMEDIATE, WARN WHERE
THEY CANNOT — so every raising case below pins ``_running_over_overlay`` to
``False`` explicitly rather than inheriting whatever filesystem the test runner
happens to sit on. Without that pin these tests would pass or fail according to
whether CI ran on overlayfs, which is exactly the kind of environment-coupled
assertion that reports the wrong thing later. The OVERLAY rail (warn, and no
install command in the emitted text) is covered by
``test__currency_remedy_is_container_safe.py``.
"""

from __future__ import annotations

import sys
import types

import pytest

from scitex_cards import _currency
from scitex_cards._currency import check_currency


def _pin_to_bare_host(monkeypatch):
    """Assert the BARE-HOST branch: the actor can remediate, so the gate raises."""
    monkeypatch.setattr(_currency, "_running_over_overlay", lambda: False)


def _install_fake_staleness_module(monkeypatch, ensure_current):
    """Register a fake `scitex_dev.staleness` module in `sys.modules` so
    `check_currency()`'s `from scitex_dev.staleness import ensure_current`
    resolves to `ensure_current` — no real scitex-dev>=0.34.0 required."""
    fake_package = types.ModuleType("scitex_dev")
    fake_module = types.ModuleType("scitex_dev.staleness")
    fake_module.ensure_current = ensure_current
    monkeypatch.setitem(sys.modules, "scitex_dev", fake_package)
    monkeypatch.setitem(sys.modules, "scitex_dev.staleness", fake_module)


# --------------------------------------------------------------------------- #
# (a) scitex-dev absent -> no-op                                              #
# --------------------------------------------------------------------------- #
def test_check_currency_no_ops_when_scitex_dev_lacks_the_staleness_module(
    monkeypatch,
):
    # Arrange — force the optional import to fail, regardless of whether
    # scitex-dev happens to be installed in this environment (`None` in
    # `sys.modules` makes the import system raise ImportError for that name).
    monkeypatch.setitem(sys.modules, "scitex_dev.staleness", None)

    # Act / Assert — no exception; scitex-cards stays standalone.
    check_currency()


# --------------------------------------------------------------------------- #
# (b) scitex-dev present + current -> passes through                         #
# --------------------------------------------------------------------------- #
def test_check_currency_passes_through_when_the_install_is_current(monkeypatch):
    # Arrange — a fake `ensure_current` that behaves like a fresh, intact install.
    calls = []
    _install_fake_staleness_module(monkeypatch, calls.append)

    # Act
    check_currency()

    # Assert — the gate delegates to scitex-dev, naming THIS distribution.
    assert calls == ["scitex-cards"]


# --------------------------------------------------------------------------- #
# (c) scitex-dev present + stale -> raises, message carries the remedy       #
# --------------------------------------------------------------------------- #
def test_check_currency_raises_when_the_install_is_stale(monkeypatch):
    # Arrange — a fake `ensure_current` that raises like a stale install.
    _pin_to_bare_host(monkeypatch)

    class _FakeStalenessError(RuntimeError):
        pass

    def _fake_ensure_current(dist_name):
        raise _FakeStalenessError(f"{dist_name} is stale")

    _install_fake_staleness_module(monkeypatch, _fake_ensure_current)

    # Act / Assert
    with pytest.raises(RuntimeError):
        check_currency()


def test_check_currency_stale_error_message_carries_the_remedy_command(monkeypatch):
    # Arrange — a fake `ensure_current` that raises with the exact upgrade
    # remedy scitex-dev would give a real caller. ON A BARE HOST that command
    # IS the repair, so it must reach the reader untouched; the overlay rail
    # scrubs it precisely because there it is not a repair.
    _pin_to_bare_host(monkeypatch)
    remedy = "pip install -U scitex-cards"

    def _fake_ensure_current(dist_name):
        raise RuntimeError(f"{dist_name} is stale — run: {remedy}")

    _install_fake_staleness_module(monkeypatch, _fake_ensure_current)

    # Act
    with pytest.raises(RuntimeError) as exc_info:
        check_currency()

    # Assert — the remedy text is not swallowed; it propagates verbatim.
    assert remedy in str(exc_info.value)


def test_check_currency_broken_payload_error_also_propagates(monkeypatch):
    """The gate also covers the broken-payload incident class (ambiguous
    dist-info / missing RECORD files) — any `ensure_current` raise must
    propagate, not just a plain version-staleness one."""
    # Arrange
    _pin_to_bare_host(monkeypatch)

    def _fake_ensure_current(dist_name):
        raise RuntimeError(f"{dist_name} has an ambiguous dist-info install")

    _install_fake_staleness_module(monkeypatch, _fake_ensure_current)

    # Act / Assert
    with pytest.raises(RuntimeError, match="ambiguous dist-info"):
        check_currency()


# EOF
