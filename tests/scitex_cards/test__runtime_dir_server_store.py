#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""An EXPLICIT server store must resolve to the local root, like an ambient one.

``resolve_tasks_path`` has two branches and they disagreed. The ambient branch
already asked ``is_postgres_url`` and returned the local root. The explicit
branch fell straight through to ``Path(explicit)``, which does not reject a DSN
-- it COLLAPSES it::

    Path("postgresql://scitex_cards@127.0.0.1:5432/scitex_cards")
    -> PosixPath("postgresql:/scitex_cards@127.0.0.1:5432/scitex_cards")

That is a RELATIVE path, so everything derived from it resolved against the
writer's current directory. ``runtime_dir`` then yielded
``postgresql:/scitex_cards@127.0.0.1:5432/runtime`` and ``inbox_db_path`` put
``cards.db`` inside it.

THE FAILURE WAS A SILENT SUCCESS, which is why it survived. Measured 2026-08-02:
``enqueue(store=<DSN>)`` RETURNED a notification id and created a phantom store
under the caller's CWD. Nothing raised, so the fail-soft caller in
``_threads_mirror.dispatch_to_inbox`` logged nothing at all, and the
notification was unreachable because nobody polls a directory named after a DSN.

The fix resolves an explicit DSN to the same local root the ambient branch
already used, rather than raising. Every caller of this function wants a LOCAL
directory -- pidfiles, the delivery ledger, reminder state, the inbox sidecar --
and wants one just as much when the cards live on a server; that is the
function's stated contract. Raising would break the board, which legitimately
threads its store through to the inbox rail.

``TestThePathHazardIsReal`` is the positive control: without it these tests
would pass just as happily against a ``Path`` that rejected DSNs outright, and
the reason this branch exists would be a claim rather than a measurement.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scitex_cards import _paths

_DSN = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
_MANAGED = ("SCITEX_CARDS_DB", "HOME", "SCITEX_DIR")


@pytest.fixture
def isolated_home(tmp_path):
    """A private HOME and CWD, so a leaked relative path is visible and contained."""
    saved_env = {name: os.environ.get(name) for name in _MANAGED}
    saved_cwd = os.getcwd()

    os.environ.pop("SCITEX_DIR", None)
    os.environ.pop("SCITEX_CARDS_DB", None)
    os.environ["HOME"] = str(tmp_path)
    work = tmp_path / "cwd"
    work.mkdir()
    os.chdir(work)

    yield work

    os.chdir(saved_cwd)
    for name, value in saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class TestThePathHazardIsReal:
    """POSITIVE CONTROL -- Path() coerces a DSN instead of rejecting it."""

    def test_path_collapses_a_dsn_to_a_relative_path(self):
        # Arrange
        dsn = _DSN

        # Act
        coerced = Path(dsn)

        # Assert
        assert not coerced.is_absolute()


class TestAnExplicitServerStoreResolvesLocally:
    def test_tasks_path_is_absolute(self, isolated_home):
        # Arrange
        store = _DSN

        # Act
        resolved = _paths.resolve_tasks_path(store)

        # Assert
        assert resolved.is_absolute()

    def test_tasks_path_carries_no_dsn_fragment(self, isolated_home):
        """The mangled path was recognisable by the scheme surviving in it."""
        # Arrange
        store = _DSN

        # Act
        resolved = _paths.resolve_tasks_path(store)

        # Assert
        assert "postgresql" not in str(resolved)

    def test_it_agrees_with_the_ambient_branch(self, isolated_home):
        """The two branches disagreeing IS the defect, so pin them together."""
        # Arrange
        os.environ["SCITEX_CARDS_DB"] = _DSN

        # Act
        ambient = _paths.resolve_tasks_path(None)

        # Assert
        assert _paths.resolve_tasks_path(_DSN) == ambient

    def test_runtime_dir_is_absolute(self, isolated_home):
        # Arrange
        store = _DSN

        # Act
        resolved = _paths.runtime_dir(store, create=False)

        # Assert
        assert resolved.is_absolute()

    def test_no_phantom_tree_is_created_under_the_cwd(self, isolated_home):
        """``create=True`` is the step that actually minted the phantom store."""
        # Arrange
        work = isolated_home

        # Act
        _paths.runtime_dir(_DSN, create=True)

        # Assert
        assert list(work.iterdir()) == []


class TestAnExplicitFilePathIsUnaffected:
    def test_a_real_path_is_still_honoured(self, isolated_home):
        # Arrange
        target = isolated_home / "elsewhere" / "cards.db"

        # Act
        resolved = _paths.resolve_tasks_path(target)

        # Assert
        assert resolved == target

    def test_a_tilde_path_is_still_expanded(self, isolated_home):
        # Arrange
        target = "~/cards.db"

        # Act
        resolved = _paths.resolve_tasks_path(target)

        # Assert
        assert "~" not in str(resolved)


# EOF
