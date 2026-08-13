#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""An UNCONFIGURED store must RAISE. There is no zero-config SQLite default.

THE TIER THIS DELETES, and why it could not be guarded door by door.

Until 2026-08-13 both resolvers ended the same way::

    from scitex_config._ecosystem import local_state
    return local_state.user_path("cards", "cards.db")

-- a filename nobody chose, returned with the same type, at the same call sites,
as a target somebody did choose. ``require_configured_store_target`` existed to
refuse exactly that, and the policy was to wire it one door at a time. Measured
on the day this landed: it had reached 1 of 31 production call sites.

WHAT MADE IT REACHABLE IN PRODUCTION RATHER THAN THEORETICAL. On compute-04
``~/.bashrc`` exports ``$SCITEX_CARDS_DB`` at line 124, BELOW the
non-interactive early return at line 8. So an interactive shell saw the DSN and
every cron job, systemd unit and script on that host saw it EMPTY -- entered
this tier -- and resolved a database that does not exist. The package's own read
door states the consequence: "the exporter answers a missing database with an
empty document, and this value is written back as the WHOLE store -- every card
replaced by nothing."

THE OPERATOR'S RULING, repeated and final: SQLite is abolished fleet-wide, and
the error-prone option is better off not existing at all -- fewer choices is the
feature, not a limitation. This file pins that the tier is GONE rather than
merely guarded: a guard has to be remembered at every new call site, and the
missing one is always found in production.

TWO ASSERTIONS CARRY THIS FILE, and neither is "it raised". First, that BOTH
resolvers refuse -- one closed and one open is the original fallback with an
extra hop. Second, that nothing is MANUFACTURED on disk: a refusal issued after
creating an empty board has already done the damage the refusal exists to
prevent.

NO ``monkeypatch`` OF PRODUCTION INTERNALS, per the ecosystem rule and for the
same reason as the neighbouring files: the defect WAS an environment state, so
these tests move the real environment, write real files, and restore both on
teardown. A test that patched the resolver would assert a belief about the
resolver; the resolver was never wrong, the environment was.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from scitex_cards._cli._admin import resolve_store_cmd
from scitex_cards._db import ENV_DB, ENV_DB_DEPRECATED, resolve_db_path
from scitex_cards._paths import resolve_tasks_path
from scitex_cards._store_target import (
    TIER_DEFAULT,
    StoreTargetNotConfigured,
    require_configured_store_target,
    resolve_store_backend,
    resolve_store_target,
    resolve_store_tier,
)

DSN = "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards"
_MANAGED = (ENV_DB, ENV_DB_DEPRECATED, "HOME", "SCITEX_DIR")


@pytest.fixture
def unconfigured_store(tmp_path):
    """A real HOME with no store env, no config file, and no enclosing repo.

    Same shape as ``test__store_target_config_tier.config_home``, which is the
    fixture that proved this state is reachable. ``chdir`` into ``tmp_path``
    matters: it is outside any git repo, so the project-scope config layer
    contributes nothing and cannot accidentally configure the store the test
    needs unconfigured.

    Yields the path the abolished tier WOULD have invented, so tests can assert
    on the artefact rather than on the absence of an exception.
    """
    saved_env = {name: os.environ.get(name) for name in _MANAGED}
    saved_cwd = os.getcwd()

    for name in (ENV_DB, ENV_DB_DEPRECATED, "SCITEX_DIR"):
        os.environ.pop(name, None)
    os.environ["HOME"] = str(tmp_path)
    (tmp_path / ".scitex" / "cards").mkdir(parents=True)
    os.chdir(tmp_path)

    yield tmp_path / ".scitex" / "cards" / "cards.db"

    os.chdir(saved_cwd)
    for name, value in saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture
def env_store(tmp_path):
    """A store CHOSEN through the real environment, restored on teardown."""
    saved = {name: os.environ.get(name) for name in (ENV_DB, ENV_DB_DEPRECATED)}

    def _set(value: str) -> str:
        os.environ[ENV_DB] = value
        return value

    try:
        yield _set
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class TestThePreconditionIsReal:
    """Control. Without this, every refusal below could pass for a wrong reason."""

    def test_the_fixture_really_leaves_the_store_unconfigured(
        self, unconfigured_store
    ):
        # Arrange
        # Act
        tier = resolve_store_tier()

        # Assert
        assert tier == TIER_DEFAULT

    def test_the_would_be_default_does_not_exist_before_the_act(
        self, unconfigured_store
    ):
        """The tier used to name THIS file. Nothing may create it."""
        # Arrange
        # Act
        exists = unconfigured_store.exists()

        # Assert
        assert not exists


class TestBothResolversRefuse:
    """One closed and one open is the same fallback with an extra hop."""

    def test_resolve_store_target_raises_instead_of_naming_a_sqlite_file(
        self, unconfigured_store
    ):
        # Arrange
        returned = None

        # Act
        try:
            returned = resolve_store_target(None)
        except StoreTargetNotConfigured:
            returned = "<refused>"

        # Assert -- the failure mode is a RETURNED PATH, so assert on the value
        # and not merely on pytest.raises: a regression here returns, it does
        # not raise something else.
        assert returned == "<refused>"

    def test_resolve_db_path_raises_instead_of_naming_a_sqlite_file(
        self, unconfigured_store
    ):
        """``_db.resolve_db_path`` promises to mirror the other's precedence."""
        # Arrange
        returned = None

        # Act
        try:
            returned = resolve_db_path(None)
        except StoreTargetNotConfigured:
            returned = "<refused>"

        # Assert
        assert returned == "<refused>"

    def test_the_backend_cannot_be_reported_as_sqlite_by_default(
        self, unconfigured_store
    ):
        """`backend_of` is TOTAL -- anything non-Postgres answers 'sqlite'.

        So a resolver that still returned the invented filename would report a
        confident ``sqlite`` for a store nobody configured. Nothing to classify
        is the only correct answer.
        """
        # Arrange
        reported = None

        # Act
        try:
            reported = resolve_store_backend(None)
        except StoreTargetNotConfigured:
            reported = "<refused>"

        # Assert
        assert reported == "<refused>"

    def test_the_server_guard_still_refuses(self, unconfigured_store):
        """The named requirement for servers survives the tier's abolition."""
        # Arrange
        returned = None

        # Act
        try:
            returned = require_configured_store_target()
        except StoreTargetNotConfigured:
            returned = "<refused>"

        # Assert
        assert returned == "<refused>"


class TestRefusingManufacturesNothing:
    """A refusal issued after creating the board has already done the damage."""

    def test_no_database_is_created_at_the_would_be_default(
        self, unconfigured_store
    ):
        # Arrange
        before = sorted(p.name for p in unconfigured_store.parent.iterdir())

        # Act
        for resolve in (resolve_store_target, resolve_db_path):
            try:
                resolve(None)
            except StoreTargetNotConfigured:
                pass

        # Assert -- on the filesystem, not on the exception.
        assert not unconfigured_store.exists()
        assert sorted(p.name for p in unconfigured_store.parent.iterdir()) == before

    def test_a_write_does_not_manufacture_a_board(self, unconfigured_store):
        """End to end: the door the 2026-07-20 incident came through."""
        # Arrange
        import scitex_cards

        # Act
        with pytest.raises(RuntimeError):
            scitex_cards.add_task(
                id="decoy-card",
                title="written to a store nobody configured",
                assignee="scitex-cards",
                agent="scitex-cards",
            )

        # Assert
        assert not unconfigured_store.exists()


class TestTheRefusalIsActionable:
    """The reader is already lost; the message is the only thing they have."""

    def _message(self) -> str:
        try:
            resolve_store_target(None)
        except StoreTargetNotConfigured as exc:
            return str(exc)
        return ""

    def test_it_names_the_variable_to_set(self, unconfigured_store):
        # Arrange
        # Act
        message = self._message()

        # Assert
        assert ENV_DB in message

    def test_it_names_the_file_that_would_have_been_served(
        self, unconfigured_store
    ):
        """Identifying the decoy is how a reader recognises the store they have
        been unknowingly reading for a week."""
        # Arrange
        # Act
        message = self._message()

        # Assert
        assert str(unconfigured_store) in message

    def test_the_example_dsn_carries_the_fleet_port(self, unconfigured_store):
        """55432, never 5432. An example inside a refusal gets copied verbatim."""
        # Arrange
        # Act
        message = self._message()

        # Assert
        assert "127.0.0.1:55432" in message
        assert "127.0.0.1:5432" not in message

    def test_it_names_the_config_key_path_not_just_the_section(
        self, unconfigured_store
    ):
        """``store`` alone sends the reader to write ``{"store": "<dsn>"}``,
        which the fail-soft config branch discards in silence."""
        # Arrange
        # Act
        message = self._message()

        # Assert
        assert "store.target" in message


class TestTheRemedyTheRefusalNamesStillWorks:
    """The message ends "Run ``scitex-cards resolve-store``". So that verb must
    survive the state the message is about.

    On 2026-07-31 this same verb was the ONE that crashed mid-cutover, while
    ``list-tasks`` served 2973 cards -- a diagnostic that dies on the case it
    diagnoses reads as "the store is broken" and costs the reader the hour they
    came here to save. Sending them to it and then crashing would be worse than
    not naming it.
    """

    def test_resolve_store_reports_the_remedy_instead_of_a_traceback(
        self, unconfigured_store
    ):
        # Arrange
        runner = CliRunner()

        # Act
        result = runner.invoke(resolve_store_cmd, [])

        # Assert -- it FAILS (there is genuinely no store) but it EXPLAINS.
        assert result.exit_code != 0
        assert ENV_DB in result.output
        assert "Traceback" not in result.output

    def test_resolve_store_still_reports_a_configured_store(self, env_store):
        """POSITIVE CONTROL: the diagnostic did not become a refusal machine."""
        # Arrange
        expected = env_store(DSN)
        runner = CliRunner()

        # Act
        result = runner.invoke(resolve_store_cmd, [])

        # Assert
        assert result.exit_code == 0
        assert expected in result.output


class TestConfiguredStoresAreUntouched:
    """POSITIVE CONTROLS. A guard that refuses everything also 'refuses when
    unconfigured' -- and would take the whole fleet down for the opposite
    reason. These are the tests that make the ones above mean something."""

    def test_an_env_dsn_still_resolves(self, env_store):
        # Arrange
        expected = env_store(DSN)

        # Act
        resolved = resolve_store_target(None)

        # Assert
        assert resolved == expected

    def test_an_env_sqlite_path_still_resolves(self, env_store, tmp_path):
        """The abolition is of the INVENTED default, not of an explicit path.

        A caller who names a SQLite file has made a decision, and this package
        is not the place that overrules it -- migrating the fleet off SQLite is
        a deployment change, not a resolver change.
        """
        # Arrange
        expected = env_store(str(tmp_path / "chosen.db"))

        # Act
        resolved = resolve_db_path(None)

        # Assert
        assert str(resolved) == expected

    def test_an_explicit_argument_still_resolves(self, unconfigured_store, tmp_path):
        """Even with nothing configured: naming a target IS configuring it."""
        # Arrange
        named = str(tmp_path / "explicit.db")

        # Act
        resolved = resolve_store_target(named)

        # Assert
        assert resolved == named


class TestLocalStateSurvivesAnUnconfiguredStore:
    """The store axis and the local-state axis are independent, and stay so.

    ``resolve_tasks_path`` derives the directory holding pidfiles, the delivery
    ledger, reminder state and the users/groups sidecar. Welding it to the store
    identity is what made the whole query side raise on 2026-07-31 when the
    fleet was pointed at PostgreSQL. An unconfigured store must not repeat that:
    there is no board, but there is still a machine.
    """

    def test_the_local_state_dir_is_still_a_real_local_directory(
        self, unconfigured_store
    ):
        # Arrange
        # Act
        resolved = resolve_tasks_path(None)

        # Assert
        assert resolved.name == "tasks.yaml"
        assert resolved.is_absolute()

    def test_the_local_state_dir_is_not_a_database(self, unconfigured_store):
        """It must never become the fallback wearing a different hat."""
        # Arrange
        # Act
        resolved = resolve_tasks_path(None)

        # Assert
        assert resolved.suffix != ".db"
        assert not unconfigured_store.exists()


# EOF
