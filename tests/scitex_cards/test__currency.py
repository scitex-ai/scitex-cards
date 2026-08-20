#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the CURRENCY gate (BARE-HOST behaviour: stale/broken installs ERROR).

``check_currency()`` delegates to ``scitex_dev.staleness.ensure_current`` when
scitex-dev is installed, and is a no-op otherwise (decoupling rule — see
``_currency.py``). Every case here supplies that optional dependency AS AN
ARGUMENT — ``load_ensure_current`` / ``load_checker``, the seams the production
verbs obtain their checker through — so no test requires a real
scitex-dev>=0.34.0 install, touches the network, or rewrites ``sys.modules``
(PA-306 §3). The results are therefore the same whatever is installed here.

SCOPE: this file covers the BARE-HOST rail, where the gate RAISES. The gate's
behaviour is conditional — BLOCK WHERE THE ACTOR CAN REMEDIATE, WARN WHERE THEY
CANNOT — so every raising case below pins the overlay answer to ``False``
through ``_bare_host`` rather than inheriting whatever filesystem the runner
sits on.

THAT PIN IS NOT A TIDINESS MEASURE. Inside a container the real
``_running_over_overlay()`` answers True from ``$APPTAINER_CONTAINER``, so the
raising rail these tests exist to cover is UNREACHABLE without it — the suite
would quietly assert the WARNING path while claiming to test the raise. The
OVERLAY rail (warn, and no install command in the emitted text) is covered by
``test__currency_remedy_is_container_safe.py``.
"""

from __future__ import annotations

import logging

import pytest

from scitex_cards import _currency
from scitex_cards._currency import (
    STALE_REMEDY,
    check_currency,
    currency_verdict,
    reset_currency_cache,
    warn_if_stale_once,
)

_CURRENCY_LOGGER = "scitex_cards._currency"


@pytest.fixture(autouse=True)
def _fresh_currency_cache():
    """Clear the warn-once state around EVERY test in this module.

    That state is REAL module state, and the measurement behind it is taken at
    most once per process — so without this the first stale scenario would be
    the only verdict any later test in this interpreter could observe. Clearing
    on the way OUT as well keeps this module from silencing a test that runs
    after it.
    """
    reset_currency_cache()
    yield
    reset_currency_cache()


def _bare_host(ensure_current):
    """``check_currency`` arguments for the BARE-HOST rail, where it must RAISE.

    The branch is pinned by ARGUMENT rather than by rewriting the module, and
    that is not merely a style rule here: inside a container
    ``_running_over_overlay()`` answers True from ``$APPTAINER_CONTAINER``, so
    the raising rail is UNREACHABLE without this seam. These tests would
    otherwise pass or fail according to the filesystem the runner sits on.
    """
    return {
        "is_overlay": lambda: False,
        "load_ensure_current": lambda: ensure_current,
    }


def _overlay(ensure_current):
    """``check_currency`` arguments for the OVERLAY rail, where it must WARN."""
    return {
        "is_overlay": lambda: True,
        "load_ensure_current": lambda: ensure_current,
    }


#: ``check_currency`` / verdict arguments for an install with no scitex-dev.
_ABSENT_CHECKER = {"load_ensure_current": lambda: None}
_ABSENT_VERDICT = {"load_checker": lambda: None}


def _checker(ensure_current, stale_error=Exception):
    """Verdict-side arguments for a scitex-dev that IS present.

    ``stale_error`` is what that scitex-dev publishes as ``StalenessError``, so
    the verdict can tell a real staleness VERDICT apart from scitex-dev itself
    malfunctioning — the latter is ``unknown``, never ``stale``.
    """
    return {"load_checker": lambda: (ensure_current, stale_error)}


def _stale_fake(message="scitex-cards 0.17.7 is behind latest 0.17.9"):
    """A scitex-dev whose ``ensure_current`` refuses with ``message``.

    Returns ``(message, kwargs)`` — the message so a caller can assert it is
    quoted verbatim, the kwargs to splat into the verb under test.
    """

    class _FakeStalenessError(RuntimeError):
        pass

    def _fake_ensure_current(dist_name):
        raise _FakeStalenessError(message)

    return message, _checker(_fake_ensure_current, _FakeStalenessError)


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
def test_check_currency_no_ops_when_scitex_dev_lacks_the_staleness_module():
    # Arrange — a loader that reports scitex-dev ABSENT, which is exactly what
    # the real one returns when the optional import fails. Injected rather than
    # forced through `sys.modules`, so the result does not depend on whether
    # scitex-dev happens to be installed in this environment.

    # Act
    returned = check_currency(**_ABSENT_CHECKER)

    # Assert — reaching this at all is the no-raise evidence; scitex-cards
    # stays standalone.
    assert returned is None


# --------------------------------------------------------------------------- #
# (b) scitex-dev present + current -> passes through                         #
# --------------------------------------------------------------------------- #
def test_check_currency_passes_through_when_the_install_is_current():
    # Arrange — a fake `ensure_current` that behaves like a fresh, intact install.
    calls = []

    # Act
    check_currency(**_bare_host(calls.append))

    # Assert — the gate delegates to scitex-dev, naming THIS distribution.
    assert calls == ["scitex-cards"]


# --------------------------------------------------------------------------- #
# (c) scitex-dev present + stale -> raises, message carries the remedy       #
# --------------------------------------------------------------------------- #
def test_check_currency_raises_when_the_install_is_stale():
    # Arrange — a fake `ensure_current` that raises like a stale install.

    class _FakeStalenessError(RuntimeError):
        pass

    def _fake_ensure_current(dist_name):
        raise _FakeStalenessError(f"{dist_name} is stale")

    # Act
    # Assert
    with pytest.raises(RuntimeError):
        check_currency(**_bare_host(_fake_ensure_current))


#: The exact upgrade remedy scitex-dev would give a real caller. ON A BARE HOST
#: that command IS the repair, so it must reach the reader untouched; the
#: overlay rail scrubs it precisely because there it is not one.
_REMEDY = "pip install -U scitex-cards"


@pytest.fixture
def bare_host_refusal() -> str:
    """The message a caller sees when the gate refuses on a bare host.

    The `pytest.raises` block lives HERE rather than in the test body: it
    counts as an assertion, so keeping it inline would make the test below two
    assertions in one (STX-TQ007).
    """

    def _fake_ensure_current(dist_name):
        raise RuntimeError(f"{dist_name} is stale — run: {_REMEDY}")

    with pytest.raises(RuntimeError) as exc_info:
        check_currency(**_bare_host(_fake_ensure_current))
    return str(exc_info.value)


def test_check_currency_stale_error_message_carries_the_remedy_command(
    bare_host_refusal: str,
):
    # Arrange (fixture)
    # Act
    message = bare_host_refusal
    # Assert — the remedy text is not swallowed; it propagates verbatim.
    assert _REMEDY in message


def test_check_currency_broken_payload_error_also_propagates():
    """The gate also covers the broken-payload incident class (ambiguous
    dist-info / missing RECORD files) — any `ensure_current` raise must
    propagate, not just a plain version-staleness one."""
    # Arrange
    def _fake_ensure_current(dist_name):
        raise RuntimeError(f"{dist_name} has an ambiguous dist-info install")

    # Act
    # Assert
    with pytest.raises(RuntimeError, match="ambiguous dist-info"):
        check_currency(**_bare_host(_fake_ensure_current))


# --------------------------------------------------------------------------- #
# (d) currency_verdict(**gate) — the NON-RAISING sibling, three-valued              #
#                                                                             #
# The Python rail is ungated ON PURPOSE (taking the last working rail from an #
# agent whose CLI already refuses is strictly worse than the bug). It reads   #
# the same measurement through this verdict instead. The constitution's       #
# three-valued rule is what these tests pin: absent tooling is UNKNOWN, never #
# "current".                                                                  #
# --------------------------------------------------------------------------- #
def test_currency_verdict_reports_unknown_when_scitex_dev_is_absent():
    # Arrange
    gate = _ABSENT_VERDICT

    # Act
    verdict = currency_verdict(**gate)

    # Assert — NOT "current": absent tooling is not evidence of currency.
    assert verdict.state == "unknown"


def test_currency_verdict_does_not_claim_a_check_when_scitex_dev_is_absent(
):
    # Arrange
    gate = _ABSENT_VERDICT

    # Act
    verdict = currency_verdict(**gate)

    # Assert — the separate named signal for "we did not measure".
    assert verdict.checked is False


def test_currency_verdict_reports_current_when_the_install_is_fresh():
    # Arrange
    gate = _checker(lambda dist_name: None)

    # Act
    verdict = currency_verdict(**gate)

    # Assert
    assert verdict.state == "current"


def test_currency_verdict_reports_stale_when_scitex_dev_refuses():
    # Arrange
    _, gate = _stale_fake()

    # Act
    verdict = currency_verdict(**gate)

    # Assert
    assert verdict.state == "stale"


def test_currency_verdict_carries_scitex_devs_message_verbatim():
    # Arrange
    message, gate = _stale_fake()

    # Act
    verdict = currency_verdict(**gate)

    # Assert — verbatim: the reader needs the versions scitex-dev computed.
    assert verdict.detail == message


def test_currency_verdict_is_unknown_when_scitex_dev_itself_malfunctions():
    # Arrange — not a staleness verdict, a broken scitex-dev.
    class _FakeStalenessError(RuntimeError):
        pass

    def _fake_ensure_current(dist_name):
        raise TypeError("ensure_current() got an unexpected keyword argument")

    gate = _checker(_fake_ensure_current, _FakeStalenessError)

    # Act
    verdict = currency_verdict(**gate)

    # Assert — degrades to UNKNOWN, not collapsed into either pole.
    assert verdict.state == "unknown"


# --------------------------------------------------------------------------- #
# (e) warn_if_stale_once(**gate) — the Python rail's notice                        #
# --------------------------------------------------------------------------- #
def test_warn_if_stale_once_does_not_raise_when_the_install_is_stale():
    """THE WHOLE POINT: the rail that still works must keep working. Reaching
    the assert at all is the no-raise evidence."""
    # Arrange
    _, gate = _stale_fake()

    # Act
    verdict = warn_if_stale_once(**gate)

    # Assert
    assert verdict.state == "stale"


def test_warn_if_stale_once_emits_a_warning_when_the_install_is_stale(
    caplog
):
    # Arrange
    _, gate = _stale_fake()
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once(**gate)

    # Assert
    assert len(_currency_warnings(caplog)) == 1


def test_warn_if_stale_once_warns_exactly_once_across_repeated_calls(
    caplog
):
    """Every dm_send calls this; repeating the notice per message would be
    noise against the operator's standing minimum-noise instruction."""
    # Arrange
    _, gate = _stale_fake()
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once(**gate)
    warn_if_stale_once(**gate)
    warn_if_stale_once(**gate)

    # Assert
    assert len(_currency_warnings(caplog)) == 1


def test_warn_if_stale_once_warning_names_the_sibling_cli_command(caplog):
    """A warning that does not tell the reader WHICH rail is down fails its
    job — the reader is an agent whose Python call just succeeded."""
    # Arrange
    _, gate = _stale_fake()
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once(**gate)

    # Assert
    assert "scitex-cards list-tasks" in _currency_warnings(caplog)[0].getMessage()


# REMOVED: test_warn_if_stale_once_warning_names_both_console_script_forms.
#
# It existed because pyproject shipped TWO console scripts onto the same
# `_cli:main`, and the one that actually refused in the 2026-07-29 incident was
# the pre-rename name — so the warning had to name both forms or a reader would
# not recognise it as being about the command they had typed. Only one console
# script is shipped now, which left the test asserting the SAME string is
# present twice: `all(form in message for form in (x, x))`, a tautology over a
# one-element set that the test immediately above already covers.


def test_warn_if_stale_once_warning_names_the_cli_and_mcp_rail(caplog):
    # Arrange
    _, gate = _stale_fake()
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once(**gate)

    # Assert
    assert "CLI/MCP" in _currency_warnings(caplog)[0].getMessage()


def test_warn_if_stale_once_warning_quotes_scitex_devs_message_verbatim(
    caplog
):
    # Arrange
    message, gate = _stale_fake()
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once(**gate)

    # Assert
    assert message in _currency_warnings(caplog)[0].getMessage()


def test_warn_if_stale_once_is_silent_when_scitex_dev_is_absent(caplog):
    # Arrange
    gate = _ABSENT_VERDICT
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    warn_if_stale_once(**gate)

    # Assert — nothing was measured, so there is nothing to report.
    assert _currency_warnings(caplog) == []


def test_warn_if_stale_once_reports_unknown_when_scitex_dev_is_absent():
    # Arrange
    gate = _ABSENT_VERDICT

    # Act
    verdict = warn_if_stale_once(**gate)

    # Assert
    assert verdict.state == "unknown"


def test_warn_if_stale_once_does_not_raise_when_scitex_dev_malfunctions():
    # Arrange

    class _FakeStalenessError(RuntimeError):
        pass

    def _fake_ensure_current(dist_name):
        raise TypeError("ensure_current() got an unexpected keyword argument")

    gate = _checker(_fake_ensure_current, _FakeStalenessError)

    # Act
    verdict = warn_if_stale_once(**gate)

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
def _raising_fake(exc):
    """A scitex-dev whose ``ensure_current`` raises ``exc``.

    A normal ``StalenessError`` is published alongside, so ``exc`` is
    unambiguously NOT the staleness verdict. Identical setup for both halves of
    the split, so the exception CLASS is the only variable between the two
    tests. Returns the kwargs to splat into the verb under test.
    """

    class _FakeStalenessError(RuntimeError):
        pass

    def _fake_ensure_current(dist_name):
        raise exc

    return _checker(_fake_ensure_current, _FakeStalenessError)


def test_warn_if_stale_once_swallows_a_system_exit_from_the_currency_path():
    """A third-party diagnostic helper calling `sys.exit()` is a LIBRARY BUG;
    absorbing it is correct. Reaching the assert at all is the did-not-escape
    evidence — before the fix, this call terminated the caller instead."""
    # Arrange
    gate = _raising_fake(SystemExit("scitex-dev called sys.exit()"))

    # Act
    verdict = warn_if_stale_once(**gate)

    # Assert
    assert verdict.state == "unknown"


def test_warn_if_stale_once_lets_a_keyboard_interrupt_propagate():
    """DELIBERATE, and pinned so nobody "hardens" the guard to BaseException.
    Ctrl-C is the operator's INTENT, not a malfunction to absorb. (The other
    direction — "simplifying" back to plain Exception — is pinned by the
    SystemExit test above; both wrong edits now go red.)"""
    # Arrange
    gate = _raising_fake(KeyboardInterrupt())

    # Act
    # Assert
    with pytest.raises(KeyboardInterrupt):
        warn_if_stale_once(**gate)


def test_currency_verdict_is_unknown_when_scitex_dev_exits_the_process():
    # Arrange
    gate = _raising_fake(SystemExit("scitex-dev called sys.exit()"))

    # Act
    verdict = currency_verdict(**gate)

    # Assert — a process exit is not a verdict about us.
    assert verdict.state == "unknown"


def test_currency_verdict_lets_a_keyboard_interrupt_propagate():
    # Arrange
    gate = _raising_fake(KeyboardInterrupt())

    # Act
    # Assert
    with pytest.raises(KeyboardInterrupt):
        currency_verdict(**gate)


def test_currency_verdict_keeps_the_stale_verdict_when_the_message_cannot_render(
):
    """The detail may degrade; the VERDICT may not. Losing a true "your CLI
    rail is down" because its `__str__` misbehaved is the worst outcome."""
    # Arrange

    class _UnrenderableStalenessError(RuntimeError):
        def __str__(self):
            raise SystemExit("__str__ called sys.exit()")

    def _fake_ensure_current(dist_name):
        raise _UnrenderableStalenessError()

    gate = _checker(_fake_ensure_current, _UnrenderableStalenessError)

    # Act
    verdict = currency_verdict(**gate)

    # Assert
    assert verdict.state == "stale"


def test_currency_verdict_is_unknown_when_staleness_error_is_not_an_exception(
):
    """A changed scitex-dev exporting a non-exception under that name makes
    `except stale_error` raise TypeError while EVALUATING the clause — which
    that clause's siblings cannot catch. The outer guard is what covers it."""
    # Arrange

    def _fake_ensure_current(dist_name):
        raise RuntimeError("scitex-cards 0.17.7 is behind latest 0.17.9")

    gate = _checker(_fake_ensure_current, "not an exception class")

    # Act
    verdict = currency_verdict(**gate)

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
