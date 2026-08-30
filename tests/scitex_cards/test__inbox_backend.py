#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``_inbox_backend.backend()`` selects the inbox rail — the retired engine is RETIRED.

PR #938 (step one, 2026-08-23): the retired engine stopped being *selectable* as a
fallback for an unshared store. Step two (2026-08-28) deleted the the retired engine
inbox implementation outright (``_inbox_sqlite.py`` / ``_inbox_sqlite_schema.py``
/ ``_inbox_receipt.py``'s the retired engine half), so this file is the direct test
coverage for the seam neither draft PR carried its own dedicated suite for:
real environment variables (the code under test reads ``os.environ``, so the
test should too), no mocks.

Every case here was previously reachable and silent (a store misconfiguration
became an invisible the retired engine inbox nobody polled). Now every one of them raises
:class:`~scitex_cards._store_errors.StoreUnavailableError` instead, and this
file pins exactly which cases do and which one legitimate case (a real shared
Postgres store) still works.
"""

from __future__ import annotations

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


class TestExplicitSqliteIsRefused:
    """Selecting the retired engine by name is a config error now, not a legal choice."""

    def test_explicit_sqlite_raises(self, env):
        # Arrange
        env.set("SCITEX_CARDS_INBOX_BACKEND", "sqlite")

        # Act
        def select_the_backend():
            return backend()

        # Assert
        with pytest.raises(StoreUnavailableError):
            select_the_backend()

    def test_explicit_sqlite_names_the_variable_that_caused_it(self, env):
        # Arrange
        env.set("SCITEX_CARDS_INBOX_BACKEND", "sqlite")

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
