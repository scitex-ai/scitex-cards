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


class TestTheOldResolverNoLongerMangles:
    """THE DEFECT THIS MODULE ROUTED AROUND IS NOW FIXED AT SOURCE.

    This class used to be a positive control asserting that
    ``resolve_db_path`` MANGLED a DSN into a relative path, and its docstring
    instructed that if it ever failed, this module's reason for existing must
    be RE-DERIVED rather than assumed. It failed on 2026-07-31. Here is the
    re-derivation.

    WHY IT CHANGED. The mangling was not merely untidy. Measured on the live
    system: with ``$SCITEX_CARDS_DB`` set to a PostgreSQL URL, ``list_tasks``
    returned 0 cards against a real board of 2960, ``resolve-store`` reported
    ``exists: True``, and a real EMPTY 217 KB SQLite database was created at
    the mangled path. An empty board reporting itself healthy is the outage
    this package's guards exist to prevent, so ``resolve_db_path`` now REFUSES
    a non-path target instead of coercing one.

    DOES ``_store_target`` STILL HAVE A REASON TO EXIST? Yes, and a clearer one
    than before. The two resolvers now answer different questions:

        resolve_db_path()        -> a filesystem Path, or REFUSES
        resolve_store_target()   -> the target, WHATEVER KIND it is

    Callers that need a file (backup, vacuum, inode checks) want the first and
    should fail loudly on a server. Callers that must route to whichever
    backend is configured want the second. Before this change the distinction
    was "one of them is broken"; now it is a genuine division of labour, which
    is a better foundation than the one this module was built on.
    """

    def test_it_refuses_a_url_instead_of_coercing_it(self, store_env):
        # Arrange
        store_env(PG_URL)

        # Act
        try:
            resolve_db_path()
            refused = False
        except StoreTargetIsNotAPath:
            refused = True

        # Assert
        assert refused

    def test_the_refusal_explains_the_empty_board_hazard(self, store_env):
        """The message must carry WHY, or the next reader re-coerces it."""
        # Arrange
        store_env(PG_URL)

        # Act
        try:
            resolve_db_path()
            message = ""
        except StoreTargetIsNotAPath as exc:
            message = str(exc)

        # Assert
        assert "0 cards" in message

    def test_a_real_path_still_resolves(self, tmp_path):
        """Positive control: refusing a DSN must not break the SQLite path,
        which is what every deployment uses today."""
        # Arrange
        target = tmp_path / "cards.db"

        # Act
        resolved = resolve_db_path(target)

        # Assert
        assert resolved == target


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
