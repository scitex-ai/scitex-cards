#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The SQLite inbox backend must use SQL that OLD SQLite can parse.

``IS NOT DISTINCT FROM`` is standard SQL and means exactly what SQLite's ``IS``
means. SQLite only learned that spelling in **3.39** (2022-06). On anything
older, every statement using it raises ``near "DISTINCT": syntax error``.

WHY THAT WAS INVISIBLE. ``enqueue`` is called from
``_threads_mirror.dispatch_to_inbox``, which is deliberately fail-soft — the DM
is already committed, so a failed enqueue costs a push, not a message. The
syntax error was therefore caught and logged, never raised. Measured on the live
host 2026-08-01 (SQLite 3.37.2): the operator's DM landed in ``dm_messages`` and
NO notification was ever created, so it never reached the agent's session. The
board reported success. Nothing was red.

WHY CI DID NOT CATCH IT. CI ran a newer SQLite, which parses the standard
spelling happily. A purely behavioural test is therefore GREEN on any modern
library no matter which spelling the source uses — it pins the SQLite version,
not the SQL. So the guard below reads the statements the module actually hands
to ``execute()`` and fails on the non-portable spelling REGARDLESS of the local
library version. That is the test that would have caught this.

It extracts those statements via AST rather than scanning the file for a
substring: this module's own docstrings now discuss ``IS NOT DISTINCT FROM`` by
name, and a substring scan would match the prose describing the bug and fail
forever. Only SQL passed to ``.execute()`` is examined.
"""

from __future__ import annotations

import ast
import inspect
import os
import sqlite3

import pytest

from scitex_cards import _inbox_sqlite

_MANAGED = ("SCITEX_TODO_AGENT_ID", "SCITEX_CARDS_DB", "HOME", "SCITEX_DIR")

#: The spelling SQLite < 3.39 cannot parse. Built from fragments so this
#: constant is not itself a literal occurrence the guard would trip over.
_NON_PORTABLE = " ".join(("DISTINCT", "FROM"))


def _executed_sql(module) -> list[str]:
    """Every string literal this module passes to a ``.execute(...)`` call.

    Adjacent string literals are folded into one ``Constant`` by the parser, so
    the multi-line implicit concatenations in the source arrive here whole.
    """
    tree = ast.parse(inspect.getsource(module))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "execute":
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                found.append(value)
    return found


@pytest.fixture
def sqlite_inbox_store(tmp_path):
    """A real store whose inbox sidecar this backend will create on demand."""
    saved_env = {name: os.environ.get(name) for name in _MANAGED}
    saved_cwd = os.getcwd()

    os.environ.pop("SCITEX_DIR", None)
    os.environ["HOME"] = str(tmp_path)
    os.environ["SCITEX_TODO_AGENT_ID"] = "test-agent"
    (tmp_path / ".scitex" / "cards").mkdir(parents=True)
    store = tmp_path / "cards.db"
    os.environ["SCITEX_CARDS_DB"] = str(store)
    os.chdir(tmp_path)

    yield str(store)

    os.chdir(saved_cwd)
    for name, value in saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class TestTheSqlIsPortableToOldSqlite:
    def test_the_guard_actually_finds_the_executed_statements(self):
        # Arrange
        statements = _executed_sql(_inbox_sqlite)

        # Act
        selects = [s for s in statements if "SELECT" in s.upper()]

        # Assert
        assert selects, "positive control: the AST guard found no SQL to check"

    def test_no_executed_statement_uses_the_non_portable_spelling(self):
        # Arrange
        statements = _executed_sql(_inbox_sqlite)

        # Act
        offenders = [s for s in statements if _NON_PORTABLE in s.upper()]

        # Assert
        assert offenders == [], (
            f"SQLite < 3.39 cannot parse these: {offenders}. Use `IS ?`, which "
            "is null-safe in every SQLite that ships this module."
        )

    def test_the_local_sqlite_would_reject_the_non_portable_spelling(self):
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE probe(a)")
        rejected = False

        # Act
        try:
            conn.execute(f"SELECT 1 FROM probe WHERE a IS NOT {_NON_PORTABLE} ?", (1,))
        except sqlite3.OperationalError:
            rejected = True

        # Assert
        assert rejected == (sqlite3.sqlite_version_info < (3, 39, 0))

    def test_the_portable_spelling_parses_on_the_local_sqlite(self):
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE probe(a)")

        # Act
        rows = conn.execute("SELECT 1 FROM probe WHERE a IS ?", (1,)).fetchall()

        # Assert
        assert rows == []


class TestEnqueueActuallyDelivers:
    def test_a_notification_is_created(self, sqlite_inbox_store):
        # Arrange
        store = sqlite_inbox_store

        # Act
        record = _inbox_sqlite.enqueue(
            "test-agent",
            event_type="dm",
            card_id="dm:operator::test-agent",
            body="moshimoshi",
            actor="operator",
            ts="2026-08-01T17:14:55Z",
            msg_id="m_58d9821e220d",
            store=store,
        )

        # Assert
        assert record is not None

    def test_the_same_message_id_is_not_enqueued_twice(self, sqlite_inbox_store):
        # Arrange
        store = sqlite_inbox_store
        _inbox_sqlite.enqueue(
            "test-agent",
            event_type="dm",
            card_id="dm:operator::test-agent",
            body="moshimoshi",
            actor="operator",
            ts="2026-08-01T17:14:55Z",
            msg_id="m_58d9821e220d",
            store=store,
        )

        # Act
        again = _inbox_sqlite.enqueue(
            "test-agent",
            event_type="dm",
            card_id="dm:operator::test-agent",
            body="moshimoshi",
            actor="operator",
            ts="2026-08-01T17:14:55Z",
            msg_id="m_58d9821e220d",
            store=store,
        )

        # Assert
        assert again is None

    def test_a_null_actor_dedups_correctly(self, sqlite_inbox_store):
        # Arrange
        store = sqlite_inbox_store
        _inbox_sqlite.enqueue(
            "test-agent",
            event_type="digest",
            card_id="card-1",
            body="nightly",
            actor=None,
            ts="2026-08-01T18:00:00Z",
            store=store,
        )

        # Act
        again = _inbox_sqlite.enqueue(
            "test-agent",
            event_type="digest",
            card_id="card-1",
            body="nightly",
            actor=None,
            ts="2026-08-01T18:00:00Z",
            store=store,
        )

        # Assert
        assert again is None

    def test_a_different_message_is_still_enqueued(self, sqlite_inbox_store):
        # Arrange
        store = sqlite_inbox_store
        _inbox_sqlite.enqueue(
            "test-agent",
            event_type="dm",
            card_id="dm:operator::test-agent",
            body="first",
            actor="operator",
            ts="2026-08-01T17:14:55Z",
            msg_id="m_aaaaaaaaaaaa",
            store=store,
        )

        # Act
        second = _inbox_sqlite.enqueue(
            "test-agent",
            event_type="dm",
            card_id="dm:operator::test-agent",
            body="second",
            actor="operator",
            ts="2026-08-01T17:14:55Z",
            msg_id="m_bbbbbbbbbbbb",
            store=store,
        )

        # Assert
        assert second is not None


# EOF
