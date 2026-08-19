#!/usr/bin/env python3
"""The index door must classify its target before it creates a database.

``open_connection`` does ``target.parent.mkdir(parents=True)`` then
``sqlite3.connect(target)``. Until 2026-08-19 it did so with NO classification
of the target at all — the module contained zero references to ``reject_*``,
``backend_of`` or ``_store_url`` — while ``index_path()`` honours the
``SCITEX_CARDS_INDEX_PATH`` env override verbatim.

That made this one door reproduce EVERY store-target incident the package has
already been fixed for. Measured through ``index_path()`` before the guard:

    ${SCITEX_CARDS_DB}          ->  ${SCITEX_CARDS_DB}
    postgresql://...:55432/...  ->  postgresql:/...:55432/...
    :55432                      ->  :55432
    host=127.0.0.1 port=55432   ->  host=127.0.0.1 port=55432

Each becomes a FILENAME on the next ``open_connection``. The first is the
incident 0.47.0 shipped a fix for; the second is the phantom tree found
untracked in the source repo ten days after it was created; the third and
fourth are the 2026-08-12 and 2026-07-31 incidents. The store doors were
guarded each time. This one was never touched.

A SERVER TARGET IS REFUSED EVEN WHEN IT IS WELL-FORMED, which is the one place
this guard is stricter than the store's. The store may legitimately BE a
PostgreSQL server; the index is a derived local file by construction, so any
target naming a server is a configuration error rather than a backend choice.
``reject_attempted_dsn`` alone would not catch it — a valid URL is not
"attempting and failing" — so ``backend_of`` is consulted as well.
"""

import os
import sqlite3
from contextlib import suppress

import pytest

from scitex_cards._index import ENV_INDEX_PATH, open_connection
from scitex_cards._store_url import UnrecognisedStoreTarget


@pytest.fixture
def index_env(tmp_path):
    """Set the real ``SCITEX_CARDS_INDEX_PATH``, restoring it on teardown.

    A real environment variable rather than a patched one: the override is read
    by production code through ``os.environ`` at call time, and that lookup is
    the thing under test. Yields a setter so each test names its own value.

    ALSO RUNS THE TEST FROM A TMP DIRECTORY, and that is not tidiness. The
    values under test are UNANCHORED — ``${SCITEX_CARDS_DB}``, ``:55432`` — so
    against unguarded code they resolve RELATIVE TO THE WORKING DIRECTORY and
    the database lands wherever pytest was started. That is not hypothetical:
    the first control run of this suite left a 4 KB, zero-table SQLite file
    named ``${SCITEX_CARDS_DB}`` in the repository root, untracked and not
    ignored — one ``git add -A`` from being committed, which is precisely the
    2026-08-02 incident this guard exists to prevent.

    The guard makes that unreachable, but a bisect, a revert, or anyone running
    this file against an older tree would reproduce it. A test that demonstrates
    a defect must not be able to inflict it.
    """
    saved_env = os.environ.get(ENV_INDEX_PATH)
    saved_cwd = os.getcwd()
    os.chdir(tmp_path)

    def _set(value: str) -> None:
        os.environ[ENV_INDEX_PATH] = value

    yield _set
    os.chdir(saved_cwd)
    if saved_env is None:
        os.environ.pop(ENV_INDEX_PATH, None)
    else:
        os.environ[ENV_INDEX_PATH] = saved_env


class TestIndexDoorRefusesANonFileTarget:
    def test_a_plain_path_still_opens_and_creates(self, tmp_path):
        """The door's REASON: first run must still work, parent dirs and all."""
        # Arrange
        target = tmp_path / "nested" / ".tasks.index.sqlite"
        # Act
        with open_connection(target) as conn:
            kind = type(conn)
        # Assert
        assert kind is sqlite3.Connection

    def test_an_unexpanded_variable_is_refused(self, index_env):
        """The 0.47.0 incident, in the module that never received its fix."""
        # Arrange
        index_env("${SCITEX_CARDS_DB}")
        # Act
        # Assert
        with pytest.raises(UnrecognisedStoreTarget):
            with open_connection():
                pass

    def test_a_well_formed_server_url_is_refused(self, index_env):
        """An index is a file by construction, so a server is always wrong."""
        # Arrange
        index_env("postgresql://scitex_cards@127.0.0.1:55432/scitex_cards")
        # Act
        # Assert
        with pytest.raises(UnrecognisedStoreTarget):
            with open_connection():
                pass

    def test_a_libpq_keyword_dsn_is_refused(self, index_env):
        """Carries no scheme, so only ``backend_of`` recognises it."""
        # Arrange
        index_env("host=127.0.0.1 port=55432 dbname=scitex_cards")
        # Act
        # Assert
        with pytest.raises(UnrecognisedStoreTarget):
            with open_connection():
                pass

    def test_a_bare_host_port_is_refused(self, index_env):
        """The 2026-08-12 incident: ready to create a file named ``:55432``."""
        # Arrange
        index_env(":55432")
        # Act
        # Assert
        with pytest.raises(UnrecognisedStoreTarget):
            with open_connection():
                pass

    def test_a_refused_target_creates_no_directory(self, tmp_path, index_env):
        """Refusing is worth nothing if the tree was already built.

        ``mkdir(parents=True)`` runs BEFORE ``connect``, so a guard placed even
        one line too late still leaves the directory the phantom store was made
        of — which is exactly what was found untracked in the source repo.
        """
        # Arrange
        index_env(str(tmp_path / "${SCITEX_CARDS_DB}" / "i.db"))
        # Act
        with suppress(UnrecognisedStoreTarget):
            with open_connection():
                pass
        # Assert
        assert list(tmp_path.iterdir()) == []
