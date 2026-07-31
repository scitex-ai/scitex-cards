#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The store target must survive resolution as written.

WHY THIS FILE EXISTS. ``_db.resolve_db_path`` is typed ``-> Path``, so a
``postgresql://`` URL cannot be represented and is coerced instead. Measured
before the fix:

    SCITEX_CARDS_DB=postgresql://h/db  ->  Path('postgresql:/h/db')

A RELATIVE path, silently, with no error -- one slash lost. A caller then
creates an empty SQLite file at that name and reports a healthy, empty board,
which is the two-stores-both-look-healthy failure this package already has scar
tissue from. The control class below pins the coercion itself, so the fix is
measured against the real defect rather than against a description of it.

The env fixture sets and restores the REAL ``os.environ`` rather than patching
it: the thing under test reads the process environment, so the test should too.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scitex_cards._db import resolve_db_path
from scitex_cards._store_target import (
    StoreTargetIsNotAPath,
    require_db_path,
    resolve_store_backend,
    resolve_store_target,
)

PG_URL = "postgresql://user@host:5432/scitex_cards"
ENV = "SCITEX_CARDS_DB"


@pytest.fixture
def store_env():
    """Set ``$SCITEX_CARDS_DB`` for one test and restore the real value after."""
    saved = os.environ.get(ENV)

    def _set(value: str) -> None:
        os.environ[ENV] = value

    yield _set

    if saved is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = saved


class TestAUrlSurvivesResolution:
    def test_a_postgres_url_is_returned_unchanged(self, store_env):
        # Arrange
        store_env(PG_URL)

        # Act
        target = resolve_store_target()

        # Assert
        assert target == PG_URL

    def test_the_backend_is_reported_as_postgres(self, store_env):
        # Arrange
        store_env(PG_URL)

        # Act
        backend = resolve_store_backend()

        # Assert
        assert backend == "postgresql"

    def test_an_explicit_url_argument_also_survives(self, store_env):
        # Arrange
        store_env("/tmp/ignored/cards.db")

        # Act
        target = resolve_store_target(PG_URL)

        # Assert
        assert target == PG_URL


class TestTheOldResolverStillMangles:
    """POSITIVE CONTROL for the defect this module exists to route around.

    If either test here fails, ``resolve_db_path`` has changed and the new
    resolver's reason for existing must be re-derived rather than assumed.
    """

    def test_it_coerces_a_url_to_a_relative_path(self, store_env):
        # Arrange
        store_env(PG_URL)

        # Act
        coerced = resolve_db_path()

        # Assert
        assert not coerced.is_absolute()

    def test_the_coerced_path_loses_a_slash(self, store_env):
        # Arrange
        store_env(PG_URL)

        # Act
        coerced = str(resolve_db_path())

        # Assert
        assert coerced.startswith("postgresql:/user")


class TestRequireDbPathRefusesRatherThanMangles:
    def test_it_raises_on_a_url(self, store_env):
        # Arrange
        store_env(PG_URL)

        # Act
        raised = pytest.raises(StoreTargetIsNotAPath)

        # Assert
        with raised:
            require_db_path()

    def test_the_refusal_names_the_remedy(self, store_env):
        """A refusal that does not say what to do instead just moves the stall."""
        # Arrange
        store_env(PG_URL)
        message = ""

        # Act
        try:
            require_db_path()
        except StoreTargetIsNotAPath as exc:
            message = str(exc)

        # Assert
        assert "resolve_store_target" in message


class TestSqliteIsUnaffected:
    """The regression guard: the existing backend must not change."""

    def test_a_path_target_still_resolves_to_that_path(self, store_env, tmp_path):
        # Arrange
        db = tmp_path / "cards.db"
        store_env(str(db))

        # Act
        resolved = require_db_path()

        # Assert
        assert resolved == Path(str(db))

    def test_a_path_target_reports_the_sqlite_backend(self, store_env, tmp_path):
        # Arrange
        store_env(str(tmp_path / "cards.db"))

        # Act
        backend = resolve_store_backend()

        # Assert
        assert backend == "sqlite"

    def test_the_two_resolvers_agree_for_paths(self, store_env, tmp_path):
        # Arrange
        store_env(str(tmp_path / "cards.db"))

        # Act
        old, new = resolve_db_path(), require_db_path()

        # Assert
        assert old == new


# EOF
