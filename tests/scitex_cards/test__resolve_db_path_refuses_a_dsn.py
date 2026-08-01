#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``resolve_db_path`` must REFUSE a PostgreSQL target, never coerce it.

THE FAILURE THIS PREVENTS WAS MEASURED ON THE LIVE SYSTEM, 2026-07-31, while
testing the cutover before performing it. With ``SCITEX_CARDS_DB`` pointed at a
PostgreSQL URL:

    list_tasks()              ->    0 cards      (SQLite target: 2960)
    resolve-store `exists`    -> True            the guard reported healthy
    on disk                   -> ./postgresql:/scitex_cards@127.0.0.1:5432/
                                    scitex_cards   a REAL, EMPTY, 217 KB SQLite
                                                   database, freshly created

``Path("postgresql://host/db")`` is not an error -- the ``//`` collapses and you
get the relative path ``postgresql:/host/db``. The store layer then created a
store there, initialised its schema, declared it present, and served nothing.

An empty board that reports itself healthy is the exact outage this package's
read-door and retirement guards exist to prevent; it previously took this store
from 2170 rows to 18. Had the cutover run, every agent would have seen zero
cards with ``resolve-store`` insisting the store existed.

SO THE LOAD-BEARING ASSERTION IS "NOTHING WAS CREATED", not merely "it raised".
A function that raises AFTER manufacturing an empty store has still done the
damage.
"""

import os

import pytest

from scitex_cards._db import ENV_DB, resolve_db_path
from scitex_cards._store_target import StoreTargetIsNotAPath

PG_URL = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
PG_KV = "host=127.0.0.1 port=5432 dbname=scitex_cards user=scitex_cards"


@pytest.fixture
def env_db(tmp_path):
    """Set ``$SCITEX_CARDS_DB`` for one test and restore it afterwards.

    A real environment variable rather than a patched lookup: the production
    code reads ``os.environ`` directly, and the bug lived in what it did with
    that value.
    """
    saved = os.environ.get(ENV_DB)

    def _set(value: str) -> None:
        os.environ[ENV_DB] = value

    try:
        yield _set
    finally:
        if saved is None:
            os.environ.pop(ENV_DB, None)
        else:
            os.environ[ENV_DB] = saved


@pytest.fixture
def empty_cwd(tmp_path):
    """Run in a real, empty working directory and restore the old one.

    A genuine chdir, not a patched one: the defect manufactured a store
    RELATIVE TO THE PROCESS CWD, so the working directory has to actually move
    for the test to be able to observe it.
    """
    previous = os.getcwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(previous)


def test_an_explicit_postgres_url_is_refused():
    # Arrange
    target = PG_URL

    # Act
    try:
        resolve_db_path(target)
        refused = False
    except StoreTargetIsNotAPath:
        refused = True

    # Assert
    assert refused


def test_an_explicit_libpq_conninfo_is_refused():
    """The keyword/value spelling reaches the resolver by a different route."""
    # Arrange
    target = PG_KV

    # Act
    try:
        resolve_db_path(target)
        refused = False
    except StoreTargetIsNotAPath:
        refused = True

    # Assert
    assert refused


def test_the_env_var_form_is_refused(env_db):
    # Arrange
    env_db(PG_URL)

    # Act
    try:
        resolve_db_path()
        refused = False
    except StoreTargetIsNotAPath:
        refused = True

    # Assert
    assert refused


def test_refusing_creates_nothing_on_disk(empty_cwd):
    """The assertion that actually matters: no store is manufactured.

    A relative DSN-shaped path lands in the WORKING DIRECTORY, which is where
    the real incident left its 217 KB stray store, so the test runs in a real
    empty directory and asserts it is still empty.
    """
    # Arrange
    before = set(empty_cwd.iterdir())

    # Act
    try:
        resolve_db_path(PG_URL)
    except StoreTargetIsNotAPath:
        pass

    # Assert
    assert set(empty_cwd.iterdir()) == before


def test_the_refusal_names_the_offending_target():
    # Arrange
    target = PG_URL

    # Act
    try:
        resolve_db_path(target)
        message = ""
    except StoreTargetIsNotAPath as exc:
        message = str(exc)

    # Assert
    assert PG_URL in message


def test_the_refusal_points_at_the_right_api():
    """An error that only says 'no' costs the reader the next hour."""
    # Arrange
    target = PG_URL

    # Act
    try:
        resolve_db_path(target)
        message = ""
    except StoreTargetIsNotAPath as exc:
        message = str(exc)

    # Assert
    assert "resolve_store_target" in message


def test_an_ordinary_path_still_resolves(tmp_path):
    """Positive control: the SQLite path is every deployment today."""
    # Arrange
    target = tmp_path / "cards.db"

    # Act
    resolved = resolve_db_path(target)

    # Assert
    assert resolved == target


# EOF
