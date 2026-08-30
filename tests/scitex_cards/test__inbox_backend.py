#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``_inbox_backend.backend()`` selects the inbox rail, or raises.

Every case here was once reachable and SILENT: a store misconfiguration became
an invisible inbox nobody polled. Each one now raises
:class:`~scitex_cards._store_errors.StoreUnavailableError` instead, and these
are the direct coverage for that seam -- real environment variables, because
the code under test reads ``os.environ``, and no mocks.
"""

from __future__ import annotations

from _banned import DRIVER, ENGINE  # noqa: F401

import pytest

from scitex_cards._inbox_backend import (
    POSTGRES,
    YAML,
    backend,
    store_is_shared,
)
from scitex_cards._store_errors import StoreUnavailableError

_PG_DSN = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"


class TestAPostgresStoreSelectsPostgres:
    def test_a_shared_store_selects_postgres(self, env):
        # Arrange
        env.delete("SCITEX_CARDS_INBOX_BACKEND")
        env.set("SCITEX_CARDS_DB", _PG_DSN)

        # Act
        active = backend()

        # Assert
        assert active == POSTGRES

    def test_a_shared_store_reports_shared(self, env):
        # Arrange
        env.delete("SCITEX_CARDS_INBOX_BACKEND")
        env.set("SCITEX_CARDS_DB", _PG_DSN)

        # Act
        shared = store_is_shared()

        # Assert
        assert shared is True


class TestAnUnsharedStoreHasNoBackend:
    """No fallback: an unshared store used to select the retired engine silently."""

    def test_an_unshared_store_raises(self, env, tmp_path):
        # Arrange
        env.delete("SCITEX_CARDS_INBOX_BACKEND")
        env.set("SCITEX_CARDS_DB", str(tmp_path / "cards.db"))

        # Act
        def select_the_backend():
            return backend()

        # Assert
        with pytest.raises(StoreUnavailableError):
            select_the_backend()

    def test_no_store_configured_at_all_raises(self, env):
        # Arrange
        env.delete("SCITEX_CARDS_INBOX_BACKEND")
        env.delete("SCITEX_CARDS_DB")
        env.delete("SCITEX_CARDS_INBOX_DSN")

        # Act
        def select_the_backend():
            return backend()

        # Assert
        with pytest.raises(StoreUnavailableError):
            select_the_backend()


class TestExplicitRetiredEngineIsRefused:
    """Selecting the retired engine by name is a config error now, not a legal choice."""

    def test_explicit_retired_engine_raises(self, env):
        # Arrange
        env.set("SCITEX_CARDS_INBOX_BACKEND", ENGINE)

        # Act
        def select_the_backend():
            return backend()

        # Assert
        with pytest.raises(StoreUnavailableError):
            select_the_backend()

    def test_explicit_retired_engine_names_the_variable_that_caused_it(self, env):
        # Arrange
        env.set("SCITEX_CARDS_INBOX_BACKEND", ENGINE)

        # Act
        def select_the_backend():
            return backend()

        # Assert
        with pytest.raises(StoreUnavailableError, match="SCITEX_CARDS_INBOX_BACKEND"):
            select_the_backend()


class TestExplicitOverridesStillWork:
    """The retirement removed a fallback; it did not remove the real knobs."""

    def test_explicit_postgres_wins_over_an_unshared_store(self, env, tmp_path):
        # Arrange
        env.set("SCITEX_CARDS_INBOX_BACKEND", "postgres")
        env.set("SCITEX_CARDS_DB", str(tmp_path / "cards.db"))

        # Act
        active = backend()

        # Assert
        assert active == POSTGRES

    def test_explicit_yaml_wins_over_a_shared_store(self, env):
        # Arrange
        env.set("SCITEX_CARDS_INBOX_BACKEND", "yaml")
        env.set("SCITEX_CARDS_DB", _PG_DSN)

        # Act
        active = backend()

        # Assert
        assert active == YAML


# EOF
