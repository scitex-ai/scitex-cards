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

import logging
import sys
import types

import pytest

from scitex_cards import _currency
from scitex_cards._currency import (
    STALE_REMEDY,
    check_currency,
    currency_verdict,
    warn_if_stale_once,
)

_CURRENCY_LOGGER = "scitex_cards._currency"


def _pin_to_bare_host(monkeypatch):
    """Assert the BARE-HOST branch: the actor can remediate, so the gate raises."""
    monkeypatch.setattr(_currency, "_running_over_overlay", lambda: False)


def _install_fake_staleness_module(monkeypatch, ensure_current, stale_error=None):
    """Register a fake `scitex_dev.staleness` module in `sys.modules` so
    `check_currency()`'s `from scitex_dev.staleness import ensure_current`
    resolves to `ensure_current` — no real scitex-dev>=0.34.0 required.

    `stale_error`, when given, is published as the module's `StalenessError`
    so `currency_verdict()` can tell a real staleness VERDICT apart from
    scitex-dev malfunctioning (the latter is `unknown`, not `stale`)."""
    fake_package = types.ModuleType("scitex_dev")
    fake_module = types.ModuleType("scitex_dev.staleness")
    fake_module.ensure_current = ensure_current
    if stale_error is not None:
        fake_module.StalenessError = stale_error
    monkeypatch.setitem(sys.modules, "scitex_dev", fake_package)
    monkeypatch.setitem(sys.modules, "scitex_dev.staleness", fake_module)


def _reset_warn_once_state(monkeypatch):
    """Clear the module-level warn-once + verdict cache for this test only.

    `monkeypatch.setattr` restores whatever the rest of the suite had already
    put there, so a test that trips the warning cannot leak into a later one
    (and cannot be silenced by an earlier one)."""
    monkeypatch.setattr(_currency, "_CACHED_VERDICT", None)
    monkeypatch.setattr(_currency, "_WARNED_STALE", False)


def _stale_fake(monkeypatch, message="scitex-cards 0.17.7 is behind latest 0.17.9"):
    """Arrange the whole stale-install world: warn-once reset + a scitex-dev
    whose `ensure_current` refuses with `message`. Returns the message."""
    _reset_warn_once_state(monkeypatch)

    class _FakeStalenessError(RuntimeError):
        pass

    def _fake_ensure_current(dist_name):
        raise _FakeStalenessError(message)

    _install_fake_staleness_module(
        monkeypatch, _fake_ensure_current, stale_error=_FakeStalenessError
    )
    return message


def _currency_warnings(caplog):
    """Only this module's WARNING records — the suite logs plenty else."""
    return [
        rec
        for rec in caplog.records
        if rec.name == _CURRENCY_LOGGER and rec.levelno == logging.WARNING
    ]


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


# --------------------------------------------------------------------------- #
# (d) currency_verdict() — the NON-RAISING sibling, three-valued              #
#                                                                             #
# The Python rail is ungated ON PURPOSE (taking the last working rail from an #
# agent whose CLI already refuses is strictly worse than the bug). It reads   #
# the same measurement through this verdict instead. The constitution's       #
# three-valued rule is what these tests pin: absent tooling is UNKNOWN, never #
# "current".                                                                  #
# --------------------------------------------------------------------------- #
def test_currency_verdict_reports_unknown_when_scitex_dev_is_absent(monkeypatch):
    # Arrange
    monkeypatch.setitem(sys.modules, "scitex_dev.staleness", None)

    # Act
    verdict = currency_verdict()

    # Assert — NOT "current": absent tooling is not evidence of currency.
    assert verdict.state == "unknown"


def test_currency_verdict_does_not_claim_a_check_when_scitex_dev_is_absent(
    monkeypatch,
):
    # Arrange
    monkeypatch.setitem(sys.modules, "scitex_dev.staleness", None)

    # Act
    verdict = currency_verdict()

    # Assert — the separate named signal for "we did not measure".
    assert verdict.checked is False


def test_currency_verdict_reports_current_when_the_install_is_fresh(monkeypatch):
    # Arrange
    _install_fake_staleness_module(monkeypatch, lambda dist_name: None)

    # Act
    verdict = currency_verdict()

    # Assert
    assert verdict.state == "current"


def test_currency_verdict_reports_stale_when_scitex_dev_refuses(monkeypatch):
    # Arrange
    _stale_fake(monkeypatch)

    # Act
    verdict = currency_verdict()

    # Assert
    assert verdict.state == "stale"


def test_currency_verdict_carries_scitex_devs_message_verbatim(monkeypatch):
    # Arrange
    message = _stale_fake(monkeypatch)

    # Act
    verdict = currency_verdict()

    # Assert — verbatim: the reader needs the versions scitex-dev computed.
    assert verdict.detail == message


def test_currency_verdict_is_unknown_when_scitex_dev_itself_malfunctions(monkeypatch):
    # Arrange — not a staleness verdict, a broken scitex-dev.
    class _FakeStalenessError(RuntimeError):
        pass

    def _fake_ensure_current(dist_name):
        raise TypeError("ensure_current() got an unexpected keyword argument")

    _install_fake_staleness_module(
        monkeypatch, _fake_ensure_current, stale_error=_FakeStalenessError
    )

    # Act
    verdict = currency_verdict()

    # Assert — degrades to UNKNOWN, not collapsed into either pole.
    assert verdict.state == "unknown"


# --------------------------------------------------------------------------- #
# (e) warn_if_stale_once() — the Python rail's notice                        #
# --------------------------------------------------------------------------- #
def test_warn_if_stale_once_does_not_raise_when_the_install_is_stale(monkeypatch):
    """THE WHOLE POINT: the rail that still works must keep working. Reaching
    the assert at all is the no-raise evidence."""
    # Arrange
    _stale_fake(monkeypatch)

    # Act
    verdict = warn_if_stale_once()

    # Assert
    assert verdict.state == "stale"


def test_warn_if_stale_once_emits_a_warning_when_the_install_is_stale(
    monkeypatch, caplog
):
    # Arrange
    _stale_fake(monkeypatch)
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once()

    # Assert
    assert len(_currency_warnings(caplog)) == 1


def test_warn_if_stale_once_warns_exactly_once_across_repeated_calls(
    monkeypatch, caplog
):
    """Every dm_send calls this; repeating the notice per message would be
    noise against the operator's standing minimum-noise instruction."""
    # Arrange
    _stale_fake(monkeypatch)
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once()
    warn_if_stale_once()
    warn_if_stale_once()

    # Assert
    assert len(_currency_warnings(caplog)) == 1


def test_warn_if_stale_once_warning_names_the_sibling_cli_command(monkeypatch, caplog):
    """A warning that does not tell the reader WHICH rail is down fails its
    job — the reader is an agent whose Python call just succeeded."""
    # Arrange
    _stale_fake(monkeypatch)
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once()

    # Assert
    assert "scitex-cards list-tasks" in _currency_warnings(caplog)[0].getMessage()


def test_warn_if_stale_once_warning_names_both_console_script_forms(
    monkeypatch, caplog
):
    """The command that ACTUALLY refused in the 2026-07-29 incident was
    `scitex-todo list-tasks` — the legacy console script, still installed
    (pyproject ships both, same `_cli:main`) and still what much of the fleet
    types. A reader who types that name must recognise the warning as being
    about the command they are running, so BOTH forms are named."""
    # Arrange
    _stale_fake(monkeypatch)
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once()

    # Assert
    message = _currency_warnings(caplog)[0].getMessage()
    assert all(
        form in message
        for form in ("scitex-cards list-tasks", "scitex-todo list-tasks")
    )


def test_warn_if_stale_once_warning_names_the_cli_and_mcp_rail(monkeypatch, caplog):
    # Arrange
    _stale_fake(monkeypatch)
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once()

    # Assert
    assert "CLI/MCP" in _currency_warnings(caplog)[0].getMessage()


def test_warn_if_stale_once_warning_quotes_scitex_devs_message_verbatim(
    monkeypatch, caplog
):
    # Arrange
    message = _stale_fake(monkeypatch)
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once()

    # Assert
    assert message in _currency_warnings(caplog)[0].getMessage()


def test_warn_if_stale_once_is_silent_when_scitex_dev_is_absent(monkeypatch, caplog):
    # Arrange
    _reset_warn_once_state(monkeypatch)
    monkeypatch.setitem(sys.modules, "scitex_dev.staleness", None)
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once()

    # Assert — nothing was measured, so there is nothing to report.
    assert _currency_warnings(caplog) == []


def test_warn_if_stale_once_reports_unknown_when_scitex_dev_is_absent(monkeypatch):
    # Arrange
    _reset_warn_once_state(monkeypatch)
    monkeypatch.setitem(sys.modules, "scitex_dev.staleness", None)

    # Act
    verdict = warn_if_stale_once()

    # Assert
    assert verdict.state == "unknown"


def test_warn_if_stale_once_does_not_raise_when_scitex_dev_malfunctions(monkeypatch):
    # Arrange
    _reset_warn_once_state(monkeypatch)

    class _FakeStalenessError(RuntimeError):
        pass

    def _fake_ensure_current(dist_name):
        raise TypeError("ensure_current() got an unexpected keyword argument")

    _install_fake_staleness_module(
        monkeypatch, _fake_ensure_current, stale_error=_FakeStalenessError
    )

    # Act
    verdict = warn_if_stale_once()

    # Assert
    assert verdict.state == "unknown"


# --------------------------------------------------------------------------- #
# (f) LIBRARY BUG vs STOP-NOW — what the guard swallows, and what it must not  #
#                                                                             #
# REFUTED HEADLINE PROPERTY (adversarial verifier, 2026-07-29): the guard      #
# caught `Exception`, and `SystemExit` is not one. Measured end-to-end through #
# the real seam, a `SystemExit` from the currency call propagated out of       #
# `dm_send` and the store was never touched — the DM did NOT go out, on the    #
# one rail this module exists to keep alive.                                   #
#                                                                             #
# The repair is NOT `except BaseException`: that would eat KeyboardInterrupt   #
# and stop Ctrl-C working whenever a check is in flight. The rule is           #
# `_RAIL_SAFE_ERRORS`: swallow LIBRARY MISBEHAVIOUR, propagate "STOP NOW".     #
# Both halves are pinned below — the second so that a later "hardening" to     #
# BaseException goes red instead of shipping.                                  #
# --------------------------------------------------------------------------- #
def _raising_fake(monkeypatch, exc):
    """Arrange a scitex-dev whose `ensure_current` raises `exc`, with a normal
    `StalenessError` published alongside so `exc` is unambiguously NOT the
    staleness verdict. Identical setup for both halves of the split, so the
    exception CLASS is the only variable between the two tests."""
    _reset_warn_once_state(monkeypatch)

    class _FakeStalenessError(RuntimeError):
        pass

    def _fake_ensure_current(dist_name):
        raise exc

    _install_fake_staleness_module(
        monkeypatch, _fake_ensure_current, stale_error=_FakeStalenessError
    )


def test_warn_if_stale_once_swallows_a_system_exit_from_the_currency_path(monkeypatch):
    """A third-party diagnostic helper calling `sys.exit()` is a LIBRARY BUG;
    absorbing it is correct. Reaching the assert at all is the did-not-escape
    evidence — before the fix, this call terminated the caller instead."""
    # Arrange
    _raising_fake(monkeypatch, SystemExit("scitex-dev called sys.exit()"))

    # Act
    verdict = warn_if_stale_once()

    # Assert
    assert verdict.state == "unknown"


def test_warn_if_stale_once_lets_a_keyboard_interrupt_propagate(monkeypatch):
    """DELIBERATE, and pinned so nobody "hardens" the guard to BaseException.
    Ctrl-C is the operator's INTENT, not a malfunction to absorb. (The other
    direction — "simplifying" back to plain Exception — is pinned by the
    SystemExit test above; both wrong edits now go red.)"""
    # Arrange
    _raising_fake(monkeypatch, KeyboardInterrupt())

    # Act / Assert
    with pytest.raises(KeyboardInterrupt):
        warn_if_stale_once()


def test_currency_verdict_is_unknown_when_scitex_dev_exits_the_process(monkeypatch):
    # Arrange
    _raising_fake(monkeypatch, SystemExit("scitex-dev called sys.exit()"))

    # Act
    verdict = currency_verdict()

    # Assert — a process exit is not a verdict about us.
    assert verdict.state == "unknown"


def test_currency_verdict_lets_a_keyboard_interrupt_propagate(monkeypatch):
    # Arrange
    _raising_fake(monkeypatch, KeyboardInterrupt())

    # Act / Assert
    with pytest.raises(KeyboardInterrupt):
        currency_verdict()


def test_currency_verdict_keeps_the_stale_verdict_when_the_message_cannot_render(
    monkeypatch,
):
    """The detail may degrade; the VERDICT may not. Losing a true "your CLI
    rail is down" because its `__str__` misbehaved is the worst outcome."""
    # Arrange
    _reset_warn_once_state(monkeypatch)

    class _UnrenderableStalenessError(RuntimeError):
        def __str__(self):
            raise SystemExit("__str__ called sys.exit()")

    def _fake_ensure_current(dist_name):
        raise _UnrenderableStalenessError()

    _install_fake_staleness_module(
        monkeypatch, _fake_ensure_current, stale_error=_UnrenderableStalenessError
    )

    # Act
    verdict = currency_verdict()

    # Assert
    assert verdict.state == "stale"


def test_currency_verdict_is_unknown_when_staleness_error_is_not_an_exception(
    monkeypatch,
):
    """A changed scitex-dev exporting a non-exception under that name makes
    `except stale_error` raise TypeError while EVALUATING the clause — which
    that clause's siblings cannot catch. The outer guard is what covers it."""
    # Arrange
    _reset_warn_once_state(monkeypatch)

    def _fake_ensure_current(dist_name):
        raise RuntimeError("scitex-cards 0.17.7 is behind latest 0.17.9")

    _install_fake_staleness_module(
        monkeypatch, _fake_ensure_current, stale_error="not an exception class"
    )

    # Act
    verdict = currency_verdict()

    # Assert
    assert verdict.state == "unknown"


# --------------------------------------------------------------------------- #
# (g) the remedy — a BASE REBAKE, never an in-place upgrade                   #
# --------------------------------------------------------------------------- #
def test_stale_remedy_does_not_prescribe_an_in_place_pip_upgrade():
    """An in-place upgrade inside an apptainer overlay leaves a whiteout that
    kills the rail AT BOOT on the next base rebake. The remedy we author must
    not name that command anywhere — not even as an aside."""
    # Arrange
    forbidden = "pip install -U"

    # Act
    remedy = STALE_REMEDY

    # Assert
    assert forbidden not in remedy


def test_stale_remedy_prescribes_a_base_rebake():
    # Arrange
    expected = "REBAKE THE CONTAINER BASE IMAGE"

    # Act
    remedy = STALE_REMEDY

    # Assert
    assert expected in remedy


# EOF
