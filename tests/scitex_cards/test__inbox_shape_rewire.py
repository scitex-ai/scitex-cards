#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The inbox rail's table, recipient column and ordering, pinned per backend.

:mod:`scitex_cards._inbox_shape` gives the rail ONE place to say where its rows
live -- ``inbox``/``recipient``/``rowid`` on SQLite, ``notifications``/
``recipient_id``/``seq`` in the canonical store -- so the query sites stop
hardcoding three facts that must move together.

WHAT THIS FILE CAN AND CANNOT ESTABLISH, said plainly rather than implied.

It pins the SQLite spelling (so the "this step changes no SQL" claim has a
guard) and it pins that the two shapes disagree on all three names (so a later
edit cannot make the SQLite test pass by quietly renaming the PostgreSQL side
into agreement). It also runs the real rail against a real SQLite file, which is
what catches a rewire that broke enqueue/poll/ack/supersede outright.

It does NOT prove that every query site reads the seam. That needs a connection
answering "PostgreSQL", and faking one would mean rewriting production internals
from a test -- green-bar theatre exactly where the risk lives. Two real
instruments cover it instead: the SQL-diff measurement recorded in the PR (mutate
one field of ``SQLITE_SHAPE``, count how many emitted statements change -- a site
that ignored the seam does not move), and the PostgreSQL CI leg added in #740,
where a missed site fails against a real server rather than a simulated one.
"""

from __future__ import annotations

import ast
import inspect
import os
import sqlite3
from pathlib import Path

import pytest

from scitex_cards import _inbox_shape, _inbox_sqlite

#: The rail's three public entry points. Every statement they run must take its
#: table / recipient column / ordering from the shape, never from a literal.
_QUERY_FUNCTIONS = ("enqueue", "poll_inbox", "ack")

#: Names that only make sense on SQLite. A literal carrying one of these inside
#: a query function is a site the seam does not reach.
_SQLITE_ONLY_NAMES = ("inbox", "recipient", "rowid")


def _literals_in(function_name: str) -> list[str]:
    """Every string literal inside one function, f-string parts included.

    Reads the module's own source, so it observes what the code SAYS rather
    than what one execution happened to run -- a branch the tests never take is
    policed exactly like the hot path. The docstring is dropped because prose
    legitimately names ``inbox`` and ``recipient``, and a scan that matched
    prose would be answering a different question than the one asked.
    """
    tree = ast.parse(inspect.getsource(_inbox_sqlite))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        found = []
        for statement in body:
            for inner in ast.walk(statement):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    found.append(inner.value)
        return found
    raise AssertionError(f"no function named {function_name!r} in _inbox_sqlite")


@pytest.fixture
def rail_db(tmp_path):
    """Point the rail at a private DB file for the duration of one test.

    Sets the real environment variable production reads and restores whatever
    was there before, so nothing here can touch the shared store.
    """
    previous = os.environ.get(_inbox_sqlite.ENV_INBOX_DB)
    db = tmp_path / "rail.db"
    os.environ[_inbox_sqlite.ENV_INBOX_DB] = str(db)
    try:
        yield db
    finally:
        if previous is None:
            os.environ.pop(_inbox_sqlite.ENV_INBOX_DB, None)
        else:
            os.environ[_inbox_sqlite.ENV_INBOX_DB] = previous


@pytest.fixture
def three_pending(rail_db, tmp_path):
    """Three notifications on the rail, enqueued oldest-first."""
    store = tmp_path / "cards.db"
    for index in range(3):
        _inbox_sqlite.enqueue(
            "agent-a",
            event_type="dm",
            card_id=f"c{index}",
            body=f"b{index}",
            actor=None,
            msg_id=f"m{index}",
            store=str(store),
        )
    return rail_db


def _rows(db: Path, sql: str):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


class TestTheSqliteSpellingIsPinned:
    """These three strings ARE the "this step changes no SQL" claim."""

    def test_table_is_inbox(self):
        # Arrange
        shape = _inbox_shape.SQLITE_SHAPE

        # Act
        table = shape.table

        # Assert
        assert table == "inbox"

    def test_recipient_column_is_recipient(self):
        # Arrange
        shape = _inbox_shape.SQLITE_SHAPE

        # Act
        recipient = shape.recipient

        # Assert
        assert recipient == "recipient"

    def test_arrival_order_is_rowid(self):
        # Arrange
        shape = _inbox_shape.SQLITE_SHAPE

        # Act
        clause = shape.order()

        # Assert
        assert clause == "ORDER BY rowid"


class TestThePostgresSpellingIsPinned:
    """``seq`` is schema v9's arrival-order column; ``rowid`` has no twin."""

    def test_table_is_notifications(self):
        # Arrange
        shape = _inbox_shape.POSTGRES_SHAPE

        # Act
        table = shape.table

        # Assert
        assert table == "notifications"

    def test_recipient_column_is_recipient_id(self):
        # Arrange
        shape = _inbox_shape.POSTGRES_SHAPE

        # Act
        recipient = shape.recipient

        # Assert
        assert recipient == "recipient_id"

    def test_arrival_order_is_seq(self):
        # Arrange
        shape = _inbox_shape.POSTGRES_SHAPE

        # Act
        clause = shape.order()

        # Assert
        assert clause == "ORDER BY seq"


class TestTheShapesCannotSilentlyAgree:
    """A shape that agreed would let a rename pass both suites at once.

    The ordering case is the one worth stating: ``rowid`` and ``seq`` are not a
    rename, they are a replacement, and SQL that names ``rowid`` is valid on
    neither engine's terms once the rows live in PostgreSQL. A future edit that
    "simplified" the two shapes into one ordering expression would break
    delivery order silently -- these tests are what stops that being quiet.
    """

    def test_the_two_shapes_name_different_tables(self):
        # Arrange
        sqlite_shape, postgres_shape = (
            _inbox_shape.SQLITE_SHAPE,
            _inbox_shape.POSTGRES_SHAPE,
        )

        # Act
        agree = sqlite_shape.table == postgres_shape.table

        # Assert
        assert agree is False

    def test_recipient_columns_differ(self):
        # Arrange
        sqlite_shape, postgres_shape = (
            _inbox_shape.SQLITE_SHAPE,
            _inbox_shape.POSTGRES_SHAPE,
        )

        # Act
        agree = sqlite_shape.recipient == postgres_shape.recipient

        # Assert
        assert agree is False

    def test_the_two_shapes_order_by_different_expressions(self):
        # Arrange
        sqlite_shape, postgres_shape = (
            _inbox_shape.SQLITE_SHAPE,
            _inbox_shape.POSTGRES_SHAPE,
        )

        # Act
        agree = sqlite_shape.order_by == postgres_shape.order_by

        # Assert
        assert agree is False


class TestNoQuerySiteStillSpellsItsOwnNames:
    """The coverage question: does the seam reach EVERY site, or just most.

    A site left hardcoded is invisible in a diff and invisible in a SQLite test
    run, because on SQLite the hardcoded spelling is the correct one. It only
    surfaces when the rows move -- which is exactly too late. So the check runs
    against the source: no literal inside a query function may carry a name that
    only makes sense on SQLite.

    The rail's DDL is deliberately out of scope. ``init_schema`` and
    ``_ensure_msg_id_column`` create the SQLite table and stay SQLite-only until
    this module's file backend is retired; scoping to the query functions is
    what keeps this check honest rather than aspirational.
    """

    @pytest.mark.parametrize("function_name", _QUERY_FUNCTIONS)
    def test_query_function_has_literals_the_scan_can_see(self, function_name):
        # Arrange -- a scan that silently found nothing would pass the check
        # below for the worst possible reason.
        literals = _literals_in(function_name)

        # Act
        count = len(literals)

        # Assert
        assert count > 0

    @pytest.mark.parametrize("function_name", _QUERY_FUNCTIONS)
    def test_query_function_names_no_sqlite_only_table_or_column(self, function_name):
        # Arrange
        literals = _literals_in(function_name)

        # Act
        leaked = [
            literal
            for literal in literals
            if any(name in literal for name in _SQLITE_ONLY_NAMES)
        ]

        # Assert
        assert leaked == []


class TestTheRailStillWorksAfterTheRewire:
    """The rewire is only safe if the rail behaves exactly as before."""

    def test_enqueue_writes_every_notification(self, three_pending):
        # Arrange
        db = three_pending

        # Act
        rows = _rows(db, "SELECT COUNT(*) FROM inbox")

        # Assert
        assert rows[0][0] == 3

    def test_poll_returns_them_oldest_first(self, three_pending, tmp_path):
        # Arrange
        store = tmp_path / "cards.db"

        # Act
        records = _inbox_sqlite.poll_inbox(
            "agent-a", unseen_only=True, store=str(store)
        )

        # Assert
        assert [record["body"] for record in records] == ["b0", "b1", "b2"]

    def test_ack_leaves_only_the_unacked_notification(self, three_pending, tmp_path):
        # Arrange
        store = tmp_path / "cards.db"
        pending = _inbox_sqlite.poll_inbox(
            "agent-a", unseen_only=True, store=str(store)
        )

        # Act
        _inbox_sqlite.ack(
            "agent-a", [record["id"] for record in pending[:2]], store=str(store)
        )

        # Assert
        remaining = _inbox_sqlite.poll_inbox(
            "agent-a", unseen_only=True, store=str(store)
        )
        assert [record["body"] for record in remaining] == ["b2"]

    def test_supersede_keeps_at_most_one_pending_digest(self, rail_db, tmp_path):
        # Arrange
        store = tmp_path / "cards.db"
        _inbox_sqlite.enqueue(
            "agent-a",
            event_type="digest",
            card_id="d",
            body="first",
            actor=None,
            store=str(store),
        )

        # Act
        _inbox_sqlite.enqueue(
            "agent-a",
            event_type="digest",
            card_id="d",
            body="second",
            actor=None,
            supersede=True,
            store=str(store),
        )

        # Assert
        pending = _inbox_sqlite.poll_inbox(
            "agent-a", unseen_only=True, store=str(store)
        )
        assert [record["body"] for record in pending] == ["second"]

    def test_poll_still_orders_by_arrival_not_by_id(self, three_pending, tmp_path):
        # Arrange -- ids are random hex, so an id-ordered read would scramble
        # these; this is the assertion that would catch ORDER BY going missing.
        store = tmp_path / "cards.db"
        records = _inbox_sqlite.poll_inbox(
            "agent-a", unseen_only=False, store=str(store)
        )

        # Act
        bodies = [record["body"] for record in records]

        # Assert
        assert bodies == sorted(bodies)


# EOF
