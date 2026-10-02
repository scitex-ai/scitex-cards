#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`freshness-gc` refuses a bad argument BEFORE it opens the store.

WHY THIS FILE EXISTS, AND WHY IT IS NOT IN THE OFFLINE PRIMITIVE SUITE
---------------------------------------------------------------------
``tests/scitex_cards/test__freshness_gc_offline.py`` proves the SWEEP's protocol
against a fake connection. This file pins the other half of the same promise:
the VERB must not hand a malformed argument to that sweep at all. The two are
separate claims, and the second one is only observable at the CLI — the store
layer's guards fire too late to be a boundary.

THE ASSERTION IS THE ORDER, NOT THE MESSAGE
-------------------------------------------
A test that only checked "exit code 2" would pass against an implementation
that opened the store first and refused afterwards. So every case here runs with
``$SCITEX_STORE_DSN`` pointed at a port nothing serves, and the assertion is
that the refusal carries click's own ``Usage:`` block: a store that was actually
touched answers with a traceback and no usage line at all. That is what makes
these tests proof of the BOUNDARY rather than of the exit status.

Measured before the boundary checks existed (2026-09-21, against this tree):
``--days -5`` exited 1 with the store layer's ``ValueError: days must be
non-negative``, and ``--cutoff not-a-date`` was not refused by the verb at all —
it went on to resolve the store, so on a reachable store the malformed value
would have reached ``?::timestamptz`` INSIDE the advisory lock.

NO STORE IS EVER WRITTEN HERE, and none is required: the dead DSN is the point.
"""

from __future__ import annotations

from click.testing import CliRunner

from scitex_cards._cli import main

#: A DSN that cannot be reached (port 1, nothing listens). Every test below
#: either never gets here — which is the pass condition — or fails loudly
#: because the verb went to the store when it should not have.
_DEAD_DSN = "postgresql://127.0.0.1:1/none"

#: The spelling of click's own refusal, and the one thing a store failure never
#: prints. See THE ASSERTION IS THE ORDER in the module docstring.
_USAGE = "Usage:"


def test_a_negative_horizon_is_refused_as_a_usage_error(env):
    # Arrange — a store that answers nothing, so reaching it is observable.
    env.set("SCITEX_STORE_DSN", _DEAD_DSN)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["freshness-gc", "--days", "-5"])
    # Assert
    assert result.exit_code == 2, result.output


def test_the_negative_horizon_names_the_option_it_refuses(env):
    # Arrange
    env.set("SCITEX_STORE_DSN", _DEAD_DSN)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["freshness-gc", "--days", "-5"])
    # Assert — the same words the sibling verb `list-stale` uses.
    assert "--days must be non-negative" in result.output


def test_a_malformed_cutoff_is_refused_as_a_usage_error(env):
    # Arrange
    env.set("SCITEX_STORE_DSN", _DEAD_DSN)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["freshness-gc", "--cutoff", "not-a-date"])
    # Assert
    assert result.exit_code == 2, result.output


def test_a_malformed_cutoff_echoes_the_value_it_got(env):
    # Arrange — a caller reading a cron log must be able to see the typo.
    env.set("SCITEX_STORE_DSN", _DEAD_DSN)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["freshness-gc", "--cutoff", "not-a-date"])
    # Assert
    assert "not-a-date" in result.output


def test_the_refusal_happens_before_the_store_is_resolved(env):
    # Arrange — the discriminator: a touched store prints no usage block.
    env.set("SCITEX_STORE_DSN", _DEAD_DSN)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["freshness-gc", "--cutoff", "not-a-date"])
    # Assert
    assert _USAGE in result.output


def test_an_empty_cutoff_is_refused(env):
    # Arrange — `--cutoff ""` is what an unset shell variable expands to.
    env.set("SCITEX_STORE_DSN", _DEAD_DSN)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["freshness-gc", "--cutoff", ""])
    # Assert
    assert result.exit_code == 2, result.output


def test_a_postgres_only_spelling_is_refused(env):
    # Arrange — the server would take `now`; this verb declines to hand a
    # spelling it cannot name to a cast, and `--days` is the way to say it.
    env.set("SCITEX_STORE_DSN", _DEAD_DSN)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["freshness-gc", "--cutoff", "now"])
    # Assert
    assert result.exit_code == 2, result.output


def test_a_valid_horizon_is_not_refused_at_the_boundary(env):
    # Arrange — the negative control: a good value must reach the STORE, which
    # is where this run fails (exit 1), not the boundary (exit 2).
    env.set("SCITEX_STORE_DSN", _DEAD_DSN)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["freshness-gc", "--days", "30", "--dry-run"])
    # Assert
    assert result.exit_code != 2


def test_a_valid_cutoff_is_not_refused_at_the_boundary(env):
    # Arrange
    env.set("SCITEX_STORE_DSN", _DEAD_DSN)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["freshness-gc", "--cutoff", "2026-08-18T00:00:00Z", "--dry-run"]
    )
    # Assert
    assert result.exit_code != 2


def test_a_bare_date_cutoff_passes_the_boundary(env):
    # Arrange — a date is a legitimate spelling that casts to midnight, so the
    # boundary must not reject it while refusing `now`. `--dry-run` is not
    # decoration here: a test asserting a BOUNDARY property must not flip cards
    # as its side effect.
    env.set("SCITEX_STORE_DSN", _DEAD_DSN)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["freshness-gc", "--cutoff", "2026-08-18", "--dry-run"]
    )
    # Assert
    assert result.exit_code != 2
