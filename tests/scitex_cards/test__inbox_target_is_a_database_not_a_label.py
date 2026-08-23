#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The inbox target must be a DATABASE, never the store's display label.

WHY THIS FILE EXISTS. ``inbox_target`` returned ``resolve_store_target(store)``,
and that function returns an explicit argument AS WRITTEN. So a caller handing
it the ``…/tasks.yaml`` DISPLAY LABEL got the label straight back as the path to
open as SQLite. ``_cli/_inbox.py`` does exactly that::

    store = resolve_tasks_path(None)     # a LABEL, not a file
    db    = inbox_target(store)          # -> the same path

Measured on the live deployment 2026-08-20: ``/home/agent/.scitex/cards/
tasks.yaml`` was 122880 bytes of ``SQLite format 3``, holding an ``inbox`` table
of 150 rows. A file whose extension says YAML and whose contents are a database.

WHAT THE HARM ACTUALLY IS, measured rather than assumed — the first answer was
wrong and is recorded here so nobody re-derives it::

    target file state          sqlite3 CREATE TABLE on it     file after
    absent                     CREATED TABLE                  12288 b  <- phantom
    empty (0 bytes)            CREATED TABLE                  12288 b  <- phantom
    real YAML (1800 bytes)     DatabaseError: not a database  1800 b, intact

A POPULATED store is never destroyed; SQLite's header check refuses it. The harm
is a PHANTOM database on an absent or empty path — silent in exactly the way
data loss is not.

THE CONTROL CLASS AT THE BOTTOM IS THE POINT OF THIS FILE. It pins that
``resolve_store_target`` STILL returns an explicit argument as written, because
that behaviour is correct and deliberate. If someone "fixes" the defect by
changing the resolver instead of the inbox, that control goes red and says so —
the guard belongs at the layer that knows it wants a database.

The env fixture sets and restores the REAL ``os.environ``: the code under test
reads the process environment, so the test should too.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scitex_cards._db_users import _db_target
from scitex_cards._inbox_sqlite_schema import (
    ENV_INBOX_DB,
    InboxTargetIsADocument,
    inbox_target,
    refuse_document_as_database,
)
from scitex_cards._store_target import resolve_store_target

PG_URL = "postgresql://user@host:5432/scitex_cards"
LABEL = "/tmp/scitex-cards-probe/tasks.yaml"
DATABASE = "/tmp/scitex-cards-probe/cards.db"


@pytest.fixture
def inbox_db_env():
    """Set ``$SCITEX_CARDS_INBOX_DB`` for one test and restore the real value."""
    saved = os.environ.get(ENV_INBOX_DB)

    def _set(value: str) -> None:
        os.environ[ENV_INBOX_DB] = value

    yield _set

    if saved is None:
        os.environ.pop(ENV_INBOX_DB, None)
    else:
        os.environ[ENV_INBOX_DB] = saved


@pytest.fixture
def no_inbox_db_env():
    """Remove ``$SCITEX_CARDS_INBOX_DB`` for one test, then restore it."""
    saved = os.environ.pop(ENV_INBOX_DB, None)

    yield

    if saved is not None:
        os.environ[ENV_INBOX_DB] = saved


class TestALabelIsInvertedToItsDatabase:
    def test_a_yaml_label_resolves_to_the_sibling_database(self, no_inbox_db_env):
        # Arrange
        expected = Path(DATABASE)

        # Act
        resolved = inbox_target(LABEL)

        # Assert
        assert Path(resolved) == expected

    def test_it_agrees_with_the_user_registry_resolver(self, no_inbox_db_env):
        # Arrange
        registry_answer = _db_target(LABEL)

        # Act
        inbox_answer = inbox_target(LABEL)

        # Assert
        assert Path(inbox_answer) == Path(registry_answer)

    def test_a_postgres_url_is_returned_unchanged(self, no_inbox_db_env):
        # Arrange
        # A DSN is already a database; inverting it would be the real bug.

        # Act
        resolved = inbox_target(PG_URL)

        # Assert
        assert str(resolved) == PG_URL

    def test_a_database_path_passes_through(self, no_inbox_db_env):
        # Arrange
        # Only a document SUFFIX is inverted, not every path.

        # Act
        resolved = inbox_target(DATABASE)

        # Assert
        assert Path(resolved) == Path(DATABASE)


class TestTheOverrideMayNotNameADocument:
    def test_an_override_naming_a_document_is_refused(self, inbox_db_env):
        # Arrange
        inbox_db_env(LABEL)

        # Act
        def resolve_the_inbox():
            return inbox_target(None)

        # Assert
        with pytest.raises(InboxTargetIsADocument):
            resolve_the_inbox()

    def test_an_override_naming_a_database_still_wins(self, inbox_db_env):
        # Arrange
        inbox_db_env(DATABASE)

        # Act
        resolved = inbox_target(None)

        # Assert
        assert Path(resolved) == Path(DATABASE)

    def test_the_refusal_names_the_variable_that_caused_it(self):
        # Arrange
        # An error that does not name the knob leaves the reader hunting.

        # Act
        def check_the_label():
            return refuse_document_as_database(LABEL)

        # Assert
        with pytest.raises(InboxTargetIsADocument, match=ENV_INBOX_DB):
            check_the_label()

    def test_a_database_target_is_allowed_through_the_guard(self):
        # Arrange
        # The guard must be silent on the normal case, including the in-place
        # migration where source and destination are the SAME cards.db.

        # Act
        result = refuse_document_as_database(DATABASE)

        # Assert
        assert result is None


class TestTheResolverItselfIsUnchanged:
    """CONTROL: the guard belongs to the inbox, not to store-target resolution."""

    def test_resolve_store_target_still_returns_an_argument_as_written(self):
        # Arrange
        # Returning the argument verbatim is CORRECT here and is relied on by
        # callers that pass a real path. Fixing the inbox defect by changing
        # this would break them, so this control fails if anyone tries.

        # Act
        resolved = resolve_store_target(LABEL)

        # Assert
        assert resolved == LABEL


# EOF
