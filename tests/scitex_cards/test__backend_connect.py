#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the store connection.

THESE RUN AGAINST A REAL SERVER, AND THEY DO NOT SKIP.

They are not mocked, because mocking is what would hide the defects this module
exists to avoid. On 2026-07-30 two fatal bugs in scitex-db's migration tool
passed 196 tests and appeared only against a live driver: a probe that aborted
the transaction so the first CREATE TABLE died, and indexes of excluded tables
counted as carried. Both looked like pure logic. A mock agrees with whatever you
tell it; a server does not.

WHAT CHANGED, AND WHY IT IS NOT A COSMETIC EDIT. This module used to reach a
server through a module-level constant::

    PG_URL = os.environ.get(
        "SCITEX_CARDS_TEST_PG", "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
    )

Every part of that default is now wrong: ``scitex_cards`` is a RETIRED database
(``CONNECT`` revoked), ``5432`` is not the port this fleet runs on (55432 is),
and every host's loopback instance is a READ-ONLY STANDBY regardless. So
``_postgres_reachable()`` answered False everywhere and ``requires_postgres``
skipped the entire PostgreSQL half of this file — silently, in a green run.
That is the exact failure the module docstring above claims to prevent: a suite
reporting success while touching no server at all.

The target is now the per-test throwaway schema the root ``conftest.py`` pins,
and an unavailable one is a FAILURE rather than a skip. A skipped storage test
and a passing storage test render identically in a summary line, which is how
this went unnoticed.

THE TESTS SEED THEIR OWN ROWS. The previous versions read whatever happened to
be in the operator's verified copy (``SELECT id FROM dm_messages LIMIT 1``,
``assert n > 0``), so they were assertions about the fleet's data rather than
about this module. Against an empty throwaway schema that is not available and
should not be: a test that needs production rows to pass is a monitor, not a
test, and it goes red whenever someone writes a card.
"""

import contextlib
import json
import os

import pytest

from scitex_cards._backend_connect import StoreConnection, connect
from scitex_cards._store_url import (
    BACKEND_POSTGRES,
    BACKEND_UNSUPPORTED,
    UnrecognisedStoreTarget,
    backend_of,
)


@pytest.fixture
def store_dsn() -> str:
    """The throwaway, schema-scoped store this test is pinned to.

    Read from the environment rather than taken as a fixture argument because
    that variable IS the contract: the root conftest points it at a uniquely
    named PostgreSQL schema per test, and the code under test resolves it the
    same way production does.

    FAILS rather than skips when the pin is missing. See the module docstring —
    a skip here is indistinguishable from a pass, and that is precisely how the
    old ``requires_postgres`` marker hid this whole file for months.
    """
    dsn = os.environ.get("SCITEX_CARDS_DB", "")
    if "search_path" not in dsn:
        pytest.fail(
            "the root conftest did not pin $SCITEX_CARDS_DB to a throwaway "
            f"PostgreSQL schema; it holds {dsn!r}. Without that pin this test "
            "would run against whatever store the ambient environment names, "
            "which is the live fleet board.",
            pytrace=False,
        )
    return dsn


@pytest.fixture
def seeded_tasks(store_dsn) -> str:
    """Three rows in ``tasks``, written through the connection under test."""
    with connect(store_dsn, read_only=False) as conn:
        conn.executemany(
            "INSERT INTO tasks(id, title, agent) VALUES(?, ?, ?)",
            [
                ("a", "first", "scitex-cards"),
                ("b", "is it done?", "scitex-cards"),
                ("c", "third", "other"),
            ],
        )
        conn.raw.commit()
    return store_dsn


class TestBackendSelection:
    def test_a_dsn_opens_as_postgres(self, store_dsn):
        # Arrange
        target = store_dsn
        # Act
        with connect(target) as conn:
            backend = conn.backend
        # Assert
        assert backend == BACKEND_POSTGRES

    def test_the_connection_is_wrapped(self, store_dsn):
        # Arrange
        target = store_dsn
        # Act
        with connect(target) as conn:
            wrapped = isinstance(conn, StoreConnection)
        # Assert
        assert wrapped is True

    def test_a_filesystem_path_names_no_store(self, tmp_path):
        # Arrange: there is one storage engine, so a filename is not a target
        # that merely selects a different one -- it names nothing openable.
        target = str(tmp_path / "cards.db")
        # Act
        backend = backend_of(target)
        # Assert
        assert backend == BACKEND_UNSUPPORTED

    def test_a_filesystem_path_is_refused(self, tmp_path):
        # Arrange
        target = str(tmp_path / "cards.db")
        # Act
        # Assert -- opening is the act and the refusal is the observation, so
        # they are one statement; the markers stay so the shape is still legible.
        with pytest.raises(UnrecognisedStoreTarget):
            connect(target)

    def test_the_refusal_creates_no_file(self, tmp_path):
        # Arrange: the surviving hazard was never the typo, it was a refusal
        # that came AFTER the mkdir -- which MANUFACTURED an empty cards
        # database that then answers queries. One was found in this repo's own
        # root on 2026-08-12: 24KB, created 2026-08-02, last opened 2026-08-09.
        target = tmp_path / "cards.db"
        # Act
        with contextlib.suppress(UnrecognisedStoreTarget):
            connect(str(target))
        # Assert
        assert target.exists() is False


class TestReadsWork:
    def test_a_parameterised_query_returns_the_right_count(self, seeded_tasks):
        # Arrange
        target = seeded_tasks
        # Act
        with connect(target) as conn:
            n = conn.fetchone(
                "SELECT count(*) FROM tasks WHERE agent = ?", ("scitex-cards",)
            )[0]
        # Assert
        assert n == 2

    def test_question_mark_paramstyle_sql_runs_unchanged(self, seeded_tasks):
        # Arrange: this is the whole design -- the caller writes "?" and never
        # writes the driver's own paramstyle.
        query = "SELECT count(*) FROM tasks WHERE agent = ?"
        # Act
        with connect(seeded_tasks) as conn:
            n = conn.fetchone(query, ("scitex-cards",))[0]
        # Assert
        assert n == 2

    def test_a_question_mark_inside_a_literal_survives(self, store_dsn):
        # Arrange: a naive replace corrupts this silently -- wrong data, no error.
        query = "SELECT 'is it done?'"
        # Act
        with connect(store_dsn) as conn:
            value = conn.fetchone(query)[0]
        # Assert
        assert value == "is it done?"

    def test_a_placeholder_and_a_literal_coexist(self, store_dsn):
        # Arrange
        query = "SELECT ?, 'is it done?'"
        # Act
        with connect(store_dsn) as conn:
            row = conn.fetchone(query, ("x",))
        # Assert
        assert tuple(row) == ("x", "is it done?")

    def test_a_literal_percent_is_not_a_format_specifier(self, seeded_tasks):
        # Arrange: a LIKE pattern must survive the rewrite to "%s" paramstyle,
        # or it raises at execution time rather than returning a wrong answer.
        query = "SELECT count(*) FROM tasks WHERE title LIKE '%ir%' AND agent = ?"
        # Act
        with connect(seeded_tasks) as conn:
            n = conn.fetchone(query, ("scitex-cards",))[0]
        # Assert
        assert n == 1


class TestTheAppendOnlyGuarantee:
    """A BEFORE DELETE trigger, exercised against a row that actually matches.

    The DELETE must MATCH A ROW or the guarantee is never exercised -- the
    trigger fires per row, so deleting zero rows succeeds trivially. An earlier
    version of this test used a nonexistent id "to be safe" and reported
    ``refused=False`` against a store where the trigger provably works. A test
    that cannot fail for the right reason cannot pass for it either.
    """

    @pytest.fixture
    def seeded_dm_message(self, store_dsn) -> tuple[str, str]:
        # A thread first: dm_messages.thread_id is a real foreign key.
        with connect(store_dsn, read_only=False) as conn:
            conn.execute(
                "INSERT INTO dm_threads"
                "(id, kind, created_at, origin_host, record_json)"
                " VALUES(?, ?, ?, ?, ?)",
                ("t1", "dm", "2026-08-30T00:00:00Z", "test-host", json.dumps({})),
            )
            conn.execute(
                "INSERT INTO dm_messages"
                "(id, thread_id, sender, body, ts, seq, origin_host, record_json)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "m1",
                    "t1",
                    "tester",
                    "hello",
                    "2026-08-30T00:00:01Z",
                    1,
                    "test-host",
                    json.dumps({}),
                ),
            )
            conn.raw.commit()
        return store_dsn, "m1"

    def test_deleting_a_matching_row_is_refused(self, seeded_dm_message):
        # Arrange
        import psycopg

        target, existing = seeded_dm_message
        # Act
        with connect(target, read_only=False) as conn:
            try:
                conn.execute("DELETE FROM dm_messages WHERE id = ?", (existing,))
                refused = False
            except psycopg.errors.RaiseException:
                refused = True
            finally:
                conn.raw.rollback()
        # Assert
        assert refused is True

    def test_the_row_the_delete_targeted_still_exists(self, seeded_dm_message):
        # Arrange: proves the refusal test rolled back cleanly rather than
        # quietly removing the row it was supposed to fail to remove.
        target, existing = seeded_dm_message
        with connect(target, read_only=False) as conn:
            try:
                conn.execute("DELETE FROM dm_messages WHERE id = ?", (existing,))
            except Exception:
                pass
            finally:
                conn.raw.rollback()
        # Act
        with connect(target) as conn:
            still_there = conn.fetchone(
                "SELECT count(*) FROM dm_messages WHERE id = ?", (existing,)
            )[0]
        # Assert
        assert still_there == 1


# EOF
