#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The last two daemons must refuse an unconfigured cards database.

`gui serve` was guarded on 2026-08-09, `board start` on 2026-08-12, each after
someone noticed the door was open. This file closes the remaining two named in
cards-store-resolution-falls-back-silently-instead-of-failing-loud-20260809's
own REMAINING WORK section -- the section that predicted the board incident and
then went unread for three days behind a blocker that had already cleared.

WHY THESE TWO ARE NOT THE SAME CASE, since a blanket sweep was explicitly
rejected on that card:

  serve   PINS the store at boot -- its own help says "requests can never
          retarget it" -- so a wrong target lasts the whole process lifetime and
          every authenticated client inherits it. Its banner prints
          `store=resolved-default`, announcing the silent fallback in the tone
          of a normal configuration.
  watch   never shows anyone anything. It watches the WRONG FILE and wakes
          nobody. Nothing renders, nothing errors, and the symptom is that
          agents stop being woken -- indistinguishable from a quiet board, which
          is what the fleet mostly is.

The guards therefore sit in different places relative to each verb's early
returns, and each placement is argued at its call site rather than here.

Real ``os.environ`` mutation with restore, no monkeypatch: the defect WAS an
environment state, so a test that patched the resolver would assert a belief
about the resolver, which was never the thing that was wrong.
"""

from __future__ import annotations

import contextlib
import os
import signal

import pytest
from click.testing import CliRunner

from scitex_cards._cli._loop import watch_cmd
from scitex_cards._cli._serve import serve_cmd
from scitex_cards._store_target import TIER_DEFAULT, resolve_store_tier

_TARGET_VARS = ("SCITEX_CARDS_DB", "SCITEX_TODO_DB")


class GuardDidNotFire(AssertionError):
    """`serve` reached serve_forever(), which only happens with no guard."""


@contextlib.contextmanager
def must_not_block(seconds: int = 5):
    """Turn "this test hangs" into "this test fails", for a blocking verb.

    `serve` has no --once and no --dry-run: with its guard in place the refusal
    returns immediately, and with the guard REMOVED the very same call walks
    into `serve_forever()` and blocks until the CI leg times out.

    MEASURED, not anticipated. Removing the guard to run the control did not
    turn this suite red -- it hung, and had to be killed by hand. That is worse
    than a plain failure in both directions: CI reports a timeout rather than
    the assertion that would have named the cause, and the whole leg is blocked
    behind it. The board suite hit the identical trap an hour earlier and could
    escape it via `--dry-run`; this verb has no such door, so the bound is made
    explicit here.

    SIGALRM rather than a thread or a subprocess: it interrupts the blocking
    accept() in the main thread, needs no extra process, and cannot itself hang.

    IT RE-RAISES ON EXIT, and that is not belt-and-braces. `CliRunner.invoke`
    CATCHES EVERY EXCEPTION and reports it as a non-zero exit code, so the
    alarm alone made `assert result.exit_code != 0` pass whether the guard
    refused or the timeout fired -- the test could no longer tell the two apart.
    Measured, in the control run for this very file: the mitigation for one
    unfalsifiable test quietly created another. Raising from __exit__ happens
    AFTER the runner has swallowed it, so a blocked command fails the test no
    matter what the assertion says.
    """
    fired: list[str] = []

    def _fire(signum, frame):  # noqa: ARG001 -- handler signature is fixed
        message = (
            f"the command was still running after {seconds}s, which means it "
            "reached serve_forever() instead of refusing -- the guard is gone"
        )
        fired.append(message)
        raise GuardDidNotFire(message)

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    if fired:
        raise GuardDidNotFire(fired[0])


@pytest.fixture()
def unconfigured_store():
    """No env target -- the state the operator's deployment was actually in."""
    saved = {name: os.environ.pop(name, None) for name in _TARGET_VARS}
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture()
def configured_store(tmp_path):
    """A cards database chosen explicitly, via the real environment.

    A TMP PATH, NOT THE PRODUCTION DSN, and the first draft got this wrong. It
    set SCITEX_CARDS_DB to the real 127.0.0.1:55432 board, and
    `test_watch_does_not_refuse_when_a_target_is_configured` duly CONNECTED TO
    THE LIVE FLEET BOARD and read real cards -- visible in the run's warnings,
    which named actual production card ids. Read-only and harmless in effect,
    but a test suite must not reach the live cards database at all, and in CI
    that address does not exist so the test would have been passing for the
    wrong reason.

    What this guard checks is the TIER -- did anybody choose a store -- not the
    backend, so any explicitly-chosen target exercises it exactly as well.
    """
    saved = {name: os.environ.get(name) for name in _TARGET_VARS}
    target = str(tmp_path / "cards.db")
    os.environ["SCITEX_CARDS_DB"] = target
    yield target
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def test_the_fixture_really_leaves_the_store_unconfigured(unconfigured_store):
    """Control: prove the precondition, or every refusal below proves nothing."""
    # Arrange
    expected = TIER_DEFAULT
    # Act
    tier = resolve_store_tier()
    # Assert
    assert tier == expected


def test_serve_refuses_when_no_store_target_is_configured(unconfigured_store):
    # Arrange
    runner = CliRunner()
    # Act
    with must_not_block():
        result = runner.invoke(serve_cmd, [])
    # Assert
    assert result.exit_code != 0


def test_the_serve_refusal_teaches_port_55432(unconfigured_store):
    """Asserted POSITIVELY, and that is not stylistic.

    The board suite's first draft asserted ":5432/" was ABSENT and PASSED with
    the guard removed -- no refusal emitted means no wrong port to find, so the
    test reported success for a codebase with no guard in it. Its pass value was
    also its did-not-notice value, which is the defect this card exists to
    answer. The positive form can only hold if a refusal actually happened.
    """
    # Arrange
    runner = CliRunner()
    # Act
    with must_not_block():
        result = runner.invoke(serve_cmd, [])
    # Assert
    assert ":55432/" in result.output


def test_serve_still_rotates_a_token_without_a_configured_store(unconfigured_store):
    """POSITIVE CONTROL, and a deliberate asymmetry with the board's guard.

    --rotate-token mints a credential and exits WITHOUT EVER OPENING A STORE.
    Refusing it would block a recovery step for an unrelated reason -- and
    rotating a token is exactly what an operator does while a deployment is
    already broken.
    """
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(serve_cmd, ["--rotate-token"])
    # Assert
    assert result.exit_code == 0


def test_serve_does_not_refuse_when_a_target_is_configured(configured_store):
    """POSITIVE CONTROL. A guard that refuses everything passes every refusal
    test ever written; only this one fails.

    Asserts the refusal did not fire, NOT that the server started -- starting it
    would bind a port and block forever. The message is the observable.
    """
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(serve_cmd, ["--rotate-token"])
    # Assert
    assert "REFUSING to serve" not in result.output


def test_watch_refuses_when_no_store_target_is_configured(unconfigured_store):
    """--once, because the forever loop would never return.

    That is not a convenience: the board suite's first draft invoked a bare
    daemon verb and, against a REMOVED guard, did not fail but HUNG -- binding a
    port inside the test run. A test that hangs instead of failing tells CI
    nothing and blocks the whole leg.
    """
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(watch_cmd, ["--once"])
    # Assert
    assert result.exit_code != 0


def test_the_watch_refusal_names_the_variable_to_set(unconfigured_store):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(watch_cmd, ["--once"])
    # Assert
    assert "SCITEX_CARDS_DB" in result.output


def test_watch_does_not_refuse_when_a_target_is_configured(configured_store):
    """POSITIVE CONTROL for the watcher door."""
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(watch_cmd, ["--once"])
    # Assert
    assert "REFUSING to serve" not in result.output


# EOF
