#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`board start` must refuse an unconfigured cards database, like `gui serve`.

WHY A SECOND FILE RATHER THAN A LINE IN THE FIRST. On 2026-08-09 the operator's
board served a file frozen eight days earlier while the fleet wrote to
PostgreSQL, and `gui serve` was guarded that day because it was the surface that
burned him. `board start` is the OTHER door onto the same Django app and was
left unguarded, so whether the operator was protected depended on which verb he
typed. Measured 2026-08-12 while closing the remaining doors on
cards-store-resolution-falls-back-silently-instead-of-failing-loud-20260809.

The structure deliberately mirrors test__serve_refuses_an_unconfigured_store.py,
including the precondition control and the positive control, because the failure
that file caught -- a guard that refuses EVERYTHING, which satisfies every
refusal test ever written -- is available here in exactly the same way.

Real ``os.environ`` mutation with restore, no monkeypatch: the defect WAS an
environment state, and a test that patched the resolver would assert a belief
about the resolver, which was never the thing that was wrong.

EVERY TEST PASSES ``--dry-run``, INCLUDING THE REFUSALS, and that is a
correctness requirement rather than a speed optimisation. The first draft
invoked the bare verb; running the positive control against a REMOVED guard did
not fail, it HUNG -- with nothing to stop it, `board start` walked into Django's
runserver loop and bound a port, inside pytest. A test that hangs instead of
failing tells CI nothing and blocks the whole leg.

Passing ``--dry-run`` works only because the guard sits BEFORE the dry-run
branch, so the refusal is still reached. The awkward part is load-bearing: had
the guard been placed inside ``_board_run_server``, ``--dry-run`` would return
first, every test here would pass, and the guard could be deleted without one
of them noticing.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from scitex_cards._cli._board import board_start_cmd
from scitex_cards._store_target import TIER_DEFAULT, resolve_store_tier

#: Port 55432, never 5432 -- 5432 is never scitex, including in fixtures.
_DSN = "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards"
_TARGET_VARS = ("SCITEX_CARDS_DB", "SCITEX_CARDS_DB")


@pytest.fixture()
def unconfigured_store():
    """No env target -- the state the operator's board was actually in."""
    saved = {name: os.environ.pop(name, None) for name in _TARGET_VARS}
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture()
def configured_store():
    """A cards database chosen explicitly, via the real environment."""
    saved = {name: os.environ.get(name) for name in _TARGET_VARS}
    os.environ["SCITEX_CARDS_DB"] = _DSN
    yield _DSN
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def test_the_fixture_really_leaves_the_store_unconfigured(unconfigured_store):
    """Control: prove the precondition, or the refusals below prove nothing."""
    # Arrange
    expected = TIER_DEFAULT
    # Act
    tier = resolve_store_tier()
    # Assert
    assert tier == expected


def test_board_start_refuses_when_no_store_target_is_configured(unconfigured_store):
    """THE UNGUARDED DOOR: binding an invented filename must be impossible."""
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(board_start_cmd, ["--dry-run"])
    # Assert
    assert result.exit_code != 0


def test_the_refusal_names_the_variable_to_set(unconfigured_store):
    """An error that only states what broke is half-written."""
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(board_start_cmd, ["--dry-run"])
    # Assert
    assert "SCITEX_CARDS_DB" in result.output


def test_the_refusal_teaches_port_55432_and_not_5432(unconfigured_store):
    """Operator ruling: 5432 is never scitex. A refusal is read by someone who
    is already lost and looking for exactly this line to copy.

    ASSERTED POSITIVELY (":55432" is present) rather than negatively (":5432/"
    is absent), and the difference is not stylistic. The negative form PASSED
    when the guard was removed during the control run -- with no refusal
    emitted there is no wrong port to find, so the test reported success for a
    codebase with no guard in it at all. Its pass value was also its
    did-not-notice value, which is precisely the defect this whole card exists
    to answer, reproduced inside the test written to defend against it.

    The positive form can only pass if a refusal actually happened AND it names
    the right port. ":55432" contains no ":5432" substring at a boundary that
    could match loosely, so one assertion carries both halves honestly.
    """
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(board_start_cmd, ["--dry-run"])
    # Assert
    assert ":55432/" in result.output


def test_board_start_does_not_refuse_when_a_target_is_configured(configured_store):
    """POSITIVE CONTROL, and the reason this file is not three tests.

    A guard that refuses everything passes every test above. That is not
    hypothetical here: the `gui serve` guard's first draft was defined between
    the decorator stack and the command, which silently rebound every decorator
    onto the helper and would have UNREGISTERED the verb entirely -- and only
    the equivalent of this test failed.

    Asserts the refusal did not fire, NOT that the server started: starting it
    would bind a port and block. The message is the observable.
    """
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(board_start_cmd, ["--dry-run"])
    # Assert
    assert "REFUSING to serve" not in result.output


# EOF
