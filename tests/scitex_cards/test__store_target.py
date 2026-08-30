#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The store target must survive resolution as written."""

from __future__ import annotations

from _banned import DRIVER, ENGINE  # noqa: F401

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
    ``exists: True``, and a real EMPTY 217 KB the retired engine database was created at
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
        """Positive control: refusing a DSN must not break the the retired engine path,
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


class TestTheRetiredEngineIsUnaffected:
    """The regression guard: the existing backend must not change."""

    def test_a_path_target_still_resolves_to_that_path(self, store_env, tmp_path):
        # Arrange
        db = tmp_path / "cards.db"
        store_env(str(db))

        # Act
        resolved = require_db_path()

        # Assert
        assert resolved == Path(str(db))

    def test_a_path_target_reports_the_retired_backend(self, store_env, tmp_path):
        # Arrange
        store_env(str(tmp_path / "cards.db"))

        # Act
        backend = resolve_store_backend()

        # Assert
        assert backend == ENGINE

    def test_the_two_resolvers_agree_for_paths(self, store_env, tmp_path):
        # Arrange
        store_env(str(tmp_path / "cards.db"))

        # Act
        old, new = resolve_db_path(), require_db_path()

        # Assert
        assert old == new


# EOF
