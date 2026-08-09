#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A SERVER must refuse to start rather than invent a store. No silent fallback.

MEASURED INCIDENT, 2026-08-09. The operator's board (`scitex-cards gui serve`,
127.0.0.1:8051) ran with NO ``SCITEX_CARDS_DB`` in its environment. Its only
store-related variables pointed at ``~/.scitex/cards/tasks.yaml`` -- A FILE THAT
DOES NOT EXIST. Resolution therefore fell to the zero-config default and served
``~/.scitex/cards/cards.db``, whose mtime was 2026-08-02 22:47.

For eight days he watched a board that rendered perfectly and showed nothing
new, while the fleet wrote to PostgreSQL (516 messages in his own DM thread,
latest that same morning). Nothing raised. Nothing warned. The only instrument
that detected it was the operator saying 「普通に届いてねえよ」.

His ruling, in capitals and repeated: NO SILENT FALLBACKS -- "it is always the
cause of troubles". It is also already the constitution's rule (fail fast, fail
loud, no silent fallbacks, no surprises) and already ADR-0016 clause 4 ("A
configured-but-unreachable Postgres FAILS the process; it must not fall back to
SQLite"). The rule existed; this door did not enforce it.

WHY THE GUARD IS ON THE SERVER AND NOT ON THE RESOLVER. A one-shot CLI landing
on the zero-config default is a fresh install behaving correctly. A BOARD
landing there is a deployment that lost its target and will now serve whatever
sits at that filename, to everyone, for as long as it runs. Same value,
different consequence -- so the refusal belongs where the consequence is.

NO ``monkeypatch`` ANYWHERE IN THIS FILE, per the ecosystem rule: these tests
move the REAL environment and restore it, because the defect WAS an environment
state. A test that patched the resolver would have asserted my belief about the
resolver, and the resolver was never wrong -- the environment was.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from scitex_cards._cli._gui import gui_serve_cmd
from scitex_cards._store_target import (
    TIER_DEFAULT,
    TIER_ENV,
    TIER_EXPLICIT,
    resolve_store_target,
    resolve_store_tier,
)

_DSN = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
_TARGET_VARS = ("SCITEX_CARDS_DB", "SCITEX_TODO_DB")


@pytest.fixture()
def unconfigured_store():
    """No env target — the state the operator's board was actually in.

    Real ``os.environ`` mutation with restore on teardown. The config tier is
    left to whatever the host really has; if a config file exists this fixture
    does NOT reach the default tier, and the control test below fails loudly
    rather than the refusal tests passing for the wrong reason.
    """
    saved = {name: os.environ.pop(name, None) for name in _TARGET_VARS}
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture()
def configured_store():
    """A store target chosen explicitly, via the real environment."""
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
    # Act
    tier = resolve_store_tier()

    # Assert
    assert tier == TIER_DEFAULT


def test_serve_refuses_when_no_store_target_is_configured(unconfigured_store):
    """THE REPORTED CASE: serving an invented filename must be impossible."""
    # Arrange
    runner = CliRunner()

    # Act
    result = runner.invoke(gui_serve_cmd, [])

    # Assert
    assert result.exit_code != 0


def test_the_refusal_names_the_variable_to_set(unconfigured_store):
    """An error that only states what broke is half-written.

    The operator hit this at 20:48 on a Sunday from a laptop. The message has to
    carry the remedy, not just the diagnosis.
    """
    # Arrange
    runner = CliRunner()

    # Act
    result = runner.invoke(gui_serve_cmd, [])

    # Assert
    assert "SCITEX_CARDS_DB" in result.output


def test_serve_does_not_refuse_when_a_target_is_configured(configured_store):
    """POSITIVE CONTROL, and the reason this file is not two tests.

    A guard that refuses EVERYTHING also satisfies "it refuses when
    unconfigured". Without this, the safest-looking possible bug — refuse
    always — ships green and takes the board down for the opposite reason.
    """
    # Arrange
    runner = CliRunner()

    # Act
    result = runner.invoke(gui_serve_cmd, ["--dry-run"])

    # Assert
    assert result.exit_code == 0


def test_the_tier_and_the_target_agree(configured_store):
    """`resolve_store_tier` MIRRORS `resolve_store_target`'s precedence.

    They are deliberately separate functions, so they can drift. This pins the
    pair: the tier reported must be the one that actually supplied the value.
    Checked at the ENV tier because that is the one the incident turned on —
    the board had no env var and nothing could tell anyone.
    """
    # Arrange
    expected = (TIER_ENV, configured_store)

    # Act
    actual = (resolve_store_tier(), resolve_store_target())

    # Assert
    assert actual == expected


def test_an_explicit_argument_outranks_the_environment(configured_store):
    """The caller-supplied target wins and is reported as such."""
    # Arrange
    elsewhere = "/tmp/somewhere/else.db"

    # Act
    tier = resolve_store_tier(elsewhere)

    # Assert
    assert tier == TIER_EXPLICIT

# EOF
