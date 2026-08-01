#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The doctor must name the engine, and must fail when the two rails differ.

Operator directive 2026-08-02: "fail fast, fail loud, no fallbacks", and asking
for a doctor that says whether this process is in sqlite mode or postgres mode.

TWO DEFECTS THIS PINS.

First, ``check_single_write_target`` reported the string "SQLite"
UNCONDITIONALLY. That was true when written and became a lie the day a store
could be a PostgreSQL server: the doctor answered "which engine am I on?" — the
exact question that line looks like it answers — with the wrong engine,
confidently, on every PostgreSQL deployment.

Second, nothing reported the NOTIFICATION rail's engine at all. The inbox is a
SQLite sidecar located from the store PATH, so pointing the store at a server
does not move it: cards go to PostgreSQL and notifications stay on SQLite. That
split is what let a DM commit to the store on 2026-08-01 while no notification
was ever created, with every card-side check green.

WHY A SPLIT IS A FAILURE, NOT AN INFO LINE. A check that merely printed both
modes would report the split as normal. It is not normal — it is the state in
which a green card-side doctor says nothing about whether notifications are
delivered. So the doctor stays red until the rails agree.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._health_backend_mode import POSTGRES, SQLITE, check_backend_mode
from scitex_cards._health_write_target import check_single_write_target

_DSN = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
_MANAGED = ("SCITEX_CARDS_DB", "HOME", "SCITEX_DIR", "SCITEX_TODO_INBOX_BACKEND")


@pytest.fixture
def sqlite_store(tmp_path):
    """A plain file store, so BOTH rails are legitimately on SQLite."""
    saved_env = {name: os.environ.get(name) for name in _MANAGED}
    saved_cwd = os.getcwd()

    for name in ("SCITEX_DIR", "SCITEX_TODO_INBOX_BACKEND"):
        os.environ.pop(name, None)
    os.environ["HOME"] = str(tmp_path)
    (tmp_path / ".scitex" / "cards").mkdir(parents=True)
    store = tmp_path / ".scitex" / "cards" / "cards.db"
    os.environ["SCITEX_CARDS_DB"] = str(store)
    os.chdir(tmp_path)

    yield str(store)

    os.chdir(saved_cwd)
    for name, value in saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class TestMatchingRailsPass:
    def test_a_file_store_reports_ok(self, sqlite_store):
        # Arrange
        store = sqlite_store

        # Act
        result = check_backend_mode(store)

        # Assert
        assert result["ok"] is True

    def test_it_names_the_engine(self, sqlite_store):
        # Arrange
        store = sqlite_store

        # Act
        result = check_backend_mode(store)

        # Assert
        assert SQLITE in result["detail"]


class TestASplitIsReportedAsFailure:
    def test_a_server_store_with_a_file_inbox_fails(self, sqlite_store):
        """The live shape: cards on PostgreSQL, notifications on a sidecar."""
        # Arrange
        store = _DSN

        # Act
        result = check_backend_mode(store)

        # Assert
        assert result["ok"] is False

    def test_the_detail_names_both_engines(self, sqlite_store):
        # Arrange
        store = _DSN

        # Act
        detail = check_backend_mode(store)["detail"]

        # Assert
        assert POSTGRES in detail and SQLITE in detail

    def test_the_hint_refuses_to_offer_a_toggle(self, sqlite_store):
        """A knob here would be a fallback wearing a switch -- say so instead."""
        # Arrange
        store = _DSN

        # Act
        hint = check_backend_mode(store)["hint"]

        # Assert
        assert "no setting" in hint.lower()

    def test_it_does_not_raise_on_a_nonsense_store(self, sqlite_store):
        """A doctor reports; it must not crash the caller asking for a report."""
        # Arrange
        store = "://///not-a-store"

        # Act
        result = check_backend_mode(store)

        # Assert
        assert isinstance(result["ok"], bool)


class TestItNamesWhichTierChoseTheTarget:
    """ "I edited the config and nothing changed" must be one line, not a hunt."""

    def test_an_explicit_argument_is_named(self, sqlite_store):
        # Arrange
        store = sqlite_store

        # Act
        detail = check_backend_mode(store)["detail"]

        # Assert
        assert "explicit argument" in detail

    def test_the_environment_variable_is_named_when_it_wins(self, sqlite_store):
        """The env var outranks the file -- that is the confusing case."""
        # Arrange
        os.environ["SCITEX_CARDS_DB"] = sqlite_store

        # Act
        detail = check_backend_mode(None)["detail"]

        # Assert
        assert "environment variable" in detail

    def test_the_config_file_is_named_when_no_env_var_is_set(self, sqlite_store):
        # Arrange
        os.environ.pop("SCITEX_CARDS_DB", None)

        # Act
        detail = check_backend_mode(None)["detail"]

        # Assert
        assert "chosen by" in detail


class TestTheWriteTargetNamesTheRealEngine:
    def test_it_no_longer_hardcodes_sqlite(self, sqlite_store):
        """Against a FILE store the honest answer really is sqlite."""
        # Arrange
        os.environ["SCITEX_CARDS_DB"] = sqlite_store

        # Act
        detail = check_single_write_target()["detail"]

        # Assert
        assert SQLITE in detail

    def test_it_reports_postgres_when_the_store_is_a_server(self, sqlite_store):
        """The regression: this line used to read SQLite here too."""
        # Arrange
        os.environ["SCITEX_CARDS_DB"] = _DSN

        # Act
        detail = check_single_write_target()["detail"]

        # Assert
        assert POSTGRES in detail


# EOF
