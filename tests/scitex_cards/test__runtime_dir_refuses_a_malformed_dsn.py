#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A malformed DSN must not become a DIRECTORY either.

WHERE THE DAMAGE ACTUALLY HAPPENS, which is not where I first guarded. #815 put
`reject_attempted_dsn` at the two doors that OPEN a store -- `_db.connect` and
`_backend_connect.connect` -- and I reported the class closed. It was not.
Measured on develop with #815 already merged:

    SCITEX_CARDS_DB='postgresql:/scitex_cards@127.0.0.1:55432/scitex_cards'
    inbox_db_path()
      -> postgresql:/scitex_cards@127.0.0.1:55432/runtime/cards.db
    and the directory tree was CREATED under the process's working directory.

The inbox rail never reaches either guarded door. It calls
`inbox_db_path(store)` -> `runtime_dir(store, create=True)`, and `create=True`
is a `mkdir(parents=True)` during PATH DERIVATION -- upstream of every connect.
A guard at the connect door is downstream of the damage and cannot see it.

I FOUND THE DOORS BY GREPPING FOR THE DRIVER'S `connect`, WHICH WAS THE WRONG
INSTRUMENT. The manufacture happens at mkdir, one layer above. Counting connect
sites gave a confident, complete-looking, wrong answer -- this package's own
recurring defect, applied to my search rather than to its code.

AND THE SITE ALREADY KNEW. `resolve_tasks_path` carries a comment naming the
2026-08-02 incident and the phantom store it produced. The fix written then was
`if is_postgres_url(explicit): return local_root` -- TWO-VALUED again, so it
catches the well-formed DSN and misses the mangled form the incident actually
produced. Fourth instance of the same shape, in the code that documents it.

THE SUITE IS TWO-SIDED. Refusing everything would pass every rejection test
here and break every deployment in existence, so the acceptance half -- a valid
DSN still redirects to the local root, a real path still resolves -- is the
positive control and is not optional.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import pytest

from scitex_cards._paths import resolve_tasks_path, runtime_dir
from scitex_cards._store_url import UnrecognisedStoreTarget

#: Every spelling that has reached this function and been turned into a path.
MALFORMED = [
    "postgresql:/scitex_cards@127.0.0.1:55432/scitex_cards",
    "postgres:/host/db",
    ":55432",
    "127.0.0.1:55432",
]

#: The env names that can supply a store target. This listed the current name
#: and its retired twin; the twin is gone, which left the same name twice.
_TARGET_VARS = ("SCITEX_CARDS_DB",)


@pytest.fixture()
def ambient(request):
    """Set $SCITEX_CARDS_DB for the ambient (explicit=None) path."""
    saved = {name: os.environ.get(name) for name in _TARGET_VARS}
    os.environ["SCITEX_CARDS_DB"] = request.param
    yield request.param
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class TestTheExplicitArgumentIsRefused:
    @pytest.mark.parametrize("target", MALFORMED)
    def test_resolve_tasks_path_refuses_a_malformed_dsn(self, target):
        # Arrange
        subject = target
        # Act
        expected = pytest.raises(UnrecognisedStoreTarget)
        # Assert
        with expected:
            resolve_tasks_path(subject)

    @pytest.mark.parametrize("target", MALFORMED)
    def test_runtime_dir_refuses_a_malformed_dsn(self, target):
        # Arrange
        subject = target
        # Act
        expected = pytest.raises(UnrecognisedStoreTarget)
        # Assert
        with expected:
            runtime_dir(subject, create=True)


class TestTheAmbientEnvironmentIsRefused:
    """A typo in $SCITEX_CARDS_DB is the commonest way to get here, and it
    arrives with explicit=None -- so guarding only the argument would leave the
    likeliest case on the unguarded path."""

    @pytest.mark.parametrize("ambient", MALFORMED, indirect=True)
    def test_runtime_dir_refuses_a_malformed_ambient_target(self, ambient):
        # Arrange
        _ = ambient
        # Act
        expected = pytest.raises(UnrecognisedStoreTarget)
        # Assert
        with expected:
            runtime_dir(None, create=True)


class TestNoDirectoryIsManufactured:
    """The point of the change, asserted against the FILESYSTEM. Every test
    above could pass while the tree still gets built."""

    def test_a_mangled_dsn_creates_no_tree_under_the_working_directory(
        self, monkeypatch_free_chdir
    ):
        # Arrange
        target = "postgresql:/scitex_cards@127.0.0.1:55432/scitex_cards"
        with contextlib.suppress(UnrecognisedStoreTarget):
            runtime_dir(target, create=True)
        # Act
        made = sorted(p.name for p in monkeypatch_free_chdir.iterdir())
        # Assert
        assert made == []


@pytest.fixture()
def monkeypatch_free_chdir(tmp_path):
    """chdir into tmp_path and back, without monkeypatch (banned ecosystem-wide).

    The defect materialises RELATIVE TO THE WORKING DIRECTORY -- that is why the
    artifacts landed in repository roots rather than anywhere a store belongs --
    so the test has to own the cwd rather than patch something.
    """
    previous = Path.cwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(previous)


class TestRealTargetsStillWork:
    """POSITIVE CONTROL. A guard that refuses everything passes every test above
    and takes every deployment with it."""

    def test_a_valid_dsn_still_redirects_to_the_local_root(self):
        # Arrange
        target = "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards"
        # Act
        resolved = resolve_tasks_path(target)
        # Assert
        assert resolved.name == "tasks.yaml"

    def test_an_absolute_path_still_resolves(self, tmp_path):
        # Arrange
        target = tmp_path / "cards.db"
        # Act
        resolved = resolve_tasks_path(str(target))
        # Assert
        assert resolved == target

    def test_runtime_dir_still_creates_a_real_runtime_directory(self, tmp_path):
        # Arrange
        target = str(tmp_path / "cards.db")
        # Act
        d = runtime_dir(target, create=True)
        # Assert
        assert d.is_dir()


# EOF
