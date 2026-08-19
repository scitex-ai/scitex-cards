#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Naming the store must not be able to fail the command that names it.

WHY THIS FILE EXISTS. Measured 2026-08-03 against the live PostgreSQL board::

    $ scitex-cards list-tasks
    ...301 cards read from PostgreSQL, validated, in memory...
    StoreTargetIsNotAPath: $SCITEX_CARDS_DB names a PostgreSQL server

The read had ALREADY SUCCEEDED. What raised was the header line above the
results, which called ``resolve_db_path`` for one reason: to print where the
rows came from. ``resolve_db_path`` is typed ``-> Path`` and refuses a DSN --
correctly, that refusal is the guard against silently serving an empty store --
so a caption took down a working verb.

The class below that ends in ``ExecutesTheDefect`` drives the real CLI function
rather than asserting on ``store_label`` alone. A test that only exercised the
helper would have passed on the day of the outage, because the helper is not
where the bug was.

CREDENTIALS. ``store_label`` output is printed to stdout and lands in logs, so
these tests pin the stripping of BOTH places a DSN can carry a secret: the
userinfo password and the query string. They assert the secret is ABSENT rather
than asserting the exact rendered string, so a future change to the display
format cannot quietly turn a leak green.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._store_target import store_label

ENV = "SCITEX_CARDS_DB"
PG_URL = "postgresql://user@host:5432/scitex_cards"
PG_WITH_PASSWORD = "postgresql://user:sekret@host:5432/scitex_cards"
PG_WITH_QUERY = "postgresql://user@host:5432/scitex_cards?sslmode=require&password=zzz"


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


class TestADsnCanBeNamedWithoutRaising:
    def test_it_returns_a_label_for_a_dsn(self, store_env):
        # Arrange
        store_env(PG_URL)

        # Act
        label = store_label(None)

        # Assert
        assert label == PG_URL

    def test_an_explicit_dsn_argument_is_also_labelled(self):
        # Arrange
        explicit = PG_URL

        # Act
        label = store_label(explicit)

        # Assert
        assert label == PG_URL

    def test_the_double_slash_survives(self, store_env):
        """``Path`` collapses ``//`` to ``/``. That mangling is the whole reason
        a stale client built ``postgresql:/host/db`` as a real directory tree."""
        # Arrange
        store_env(PG_URL)

        # Act
        label = store_label(None)

        # Assert
        assert "://" in label


class TestTheLabelCarriesNoCredentials:
    def test_the_userinfo_password_is_stripped(self):
        # Arrange
        target = PG_WITH_PASSWORD

        # Act
        label = store_label(target)

        # Assert
        assert "sekret" not in label

    def test_the_user_is_kept_so_the_store_is_still_identifiable(self):
        """A fully-masked label answers "which store?" no better than none."""
        # Arrange
        target = PG_WITH_PASSWORD

        # Act
        label = store_label(target)

        # Assert
        assert "user@host:5432/scitex_cards" in label

    def test_the_query_string_is_dropped(self):
        # Arrange
        target = PG_WITH_QUERY

        # Act
        label = store_label(target)

        # Assert
        assert "zzz" not in label and "?" not in label


class TestSqliteIsUnaffected:
    """Positive control: the backend every deployment used before PostgreSQL."""

    def test_a_path_target_is_returned_as_written(self, store_env, tmp_path):
        # Arrange
        db = tmp_path / "cards.db"
        store_env(str(db))

        # Act
        label = store_label(None)

        # Assert
        assert label == str(db)

    def test_a_path_is_not_mistaken_for_a_dsn_and_stripped(self, store_env, tmp_path):
        """A filename may contain '@' or '?'. Neither may trigger DSN handling."""
        # Arrange
        db = tmp_path / "weird@name?x.db"
        store_env(str(db))

        # Act
        label = store_label(None)

        # Assert
        assert label == str(db)


def _no_rows(*args, **kwargs):
    """A real reader that finds nothing — `_store.list_tasks`' empty answer.

    The store is NOT what these tests are about. The defect was in the CAPTION
    printed after the read, and reaching that line needs a read that SUCCEEDS;
    a DSN pointing at a database this process cannot reach fails long before
    the caption runs. Passing this through the verbs' `lister` seam reproduces
    the defect's conditions — a Postgres DSN configured, rows in hand, caption
    still to print — without rebinding `_store.list_tasks` for the process.

    An empty result is the RIGHT arrangement here rather than a convenience:
    the caption is printed on every path, including the no-rows one, so an
    empty store exercises it with the least unrelated machinery in the way.
    """
    return []


class TestTheCliCaptionExecutesTheDefect:
    """Drive the real verb, because the helper is not where the bug was."""

    def test_list_tasks_filtered_does_not_raise_on_a_dsn(self, store_env, capsys):
        # Arrange
        from scitex_cards._cli import _admin

        store_env(PG_URL)

        # Act
        _admin.list_tasks_filtered(None, None, None, False, None, lister=_no_rows)

        # Assert
        assert PG_URL in capsys.readouterr().out

    def test_list_blocking_operator_does_not_raise_on_a_dsn(self, store_env, capsys):
        # Arrange
        from scitex_cards._cli import _admin

        store_env(PG_URL)

        # Act
        _admin.list_blocking_operator(None, as_json=False, lister=_no_rows)

        # Assert
        assert PG_URL in capsys.readouterr().out


# EOF
