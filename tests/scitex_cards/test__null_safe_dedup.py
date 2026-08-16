#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NULL-safe dedup in the SQLite inbox backend, spelled so old SQLite parses it.

The inbox dedups on ``(event_type, card_id, ts, actor)`` and every one of those
columns is genuinely nullable, so the comparison must be NULL-safe.

THE DANGEROUS FIX IS THE OBVIOUS ONE, and this has not changed: rewriting the
comparison to ``=`` parses cleanly on every engine and then silently stops
deduplicating. ``actor = NULL`` is UNKNOWN, never true, so a re-emitted
notification never matches an existing row and the recipient gets duplicates
forever -- and on the supersede path the "at most one pending digest per
recipient" invariant quietly dies too. ``TestTheNaiveRewriteIsMeasurablyWrong``
keeps that a measurement rather than a claim.

WHY THIS FILE REVERSED ITS OWN DECISION (2026-08-02). It previously required
``IS NOT DISTINCT FROM`` -- standard SQL, native on PostgreSQL, and the spelling
that would let this module's SQL survive a later move to PostgreSQL. SQLite only
accepts it from **3.39** (2022-06), and this file pinned that as a floor.

That floor was false where it mattered. The production host runs SQLite
**3.37.2**, so every enqueue raised ``near "DISTINCT": syntax error``. Because
``_threads_mirror.dispatch_to_inbox`` is deliberately fail-soft, the error was
caught and logged instead of raised: DMs committed to the store, no notification
row was ever written, and the board reported success. An operator DM sat in
``dm_messages`` and never reached the agent's session. Containers run SQLite
3.45.1 and parsed it happily, so agent-to-agent DMs kept working and the failure
looked environment-shaped rather than code-shaped.

The floor was also unenforceable by us -- ``the CI images are not something this
package controls`` was the original note, and neither is the host's system
python. A requirement the package cannot enforce is not a floor, it is a hope.

So the SQLite backend now uses SQLite's own null-safe ``IS ?``, which every
SQLite that ships this module accepts and which needs no floor at all.

A PARAGRAPH HERE USED TO ARGUE that this module could never be handed a
PostgreSQL connection, because it resolved ``inbox_db_path(store)`` and opened a
FILE -- so the standard spelling belonged in a separate PostgreSQL rail. True
when written, false now: ``open_connection`` goes through
:func:`scitex_cards._db.connect`, which dispatches a DSN to the PostgreSQL
backend and returns a ``StoreConnection``. There is no separate rail; it is this
one, pointed elsewhere. Which is exactly why the spelling is resolved per
connection by ``null_safe_eq_for`` rather than hardcoded either way.

The guard below reads the statements the module actually hands to ``execute()``
via AST, then asks the LOCAL SQLite to parse each one. That is version
independent: it fails on a new SQLite too, which is what the old behavioural
tests could not do -- they pinned the local library version, not the SQL.
It is deliberately not a substring scan of the file, because this module's
docstring now names the rejected spelling and a scan would match that prose.
"""

from __future__ import annotations

import ast
import inspect
import os
import sqlite3
from contextlib import contextmanager

import pytest

from scitex_cards import _inbox_sqlite, _inbox_sqlite_schema

#: The rail's SQL lives in TWO modules, so the scan has to follow it. The DDL
#: half moved to ``_inbox_sqlite_schema`` when the rail stopped hand-rolling
#: ``sqlite3.connect``; scanning only ``_inbox_sqlite`` afterwards left this
#: guard policing the query half alone. Its positive control went RED rather
#: than quietly passing over a narrower surface -- which is the whole reason
#: that control exists.
RAIL_MODULES = (_inbox_sqlite, _inbox_sqlite_schema)

_MANAGED = ("SCITEX_CARDS_AGENT_ID", "SCITEX_CARDS_DB", "HOME", "SCITEX_DIR")

#: The spelling SQLite < 3.39 rejects. Assembled from fragments so this constant
#: is not itself an occurrence the guards would trip over.
_REJECTED = " ".join(("DISTINCT", "FROM"))


def _executed_sql(module) -> list[str]:
    """Every string LITERAL this module passes to a ``.execute(...)`` call.

    Adjacent string literals are folded into one ``Constant`` by the parser, so
    the multi-line implicit concatenations in the source arrive here whole.

    LOWER BOUND, NOT A CENSUS. This reads only ``ast.Constant`` first-args, so
    any statement composed at runtime -- an f-string, a ``.format``, a joined
    fragment -- is INVISIBLE to it. That is not a hypothetical limitation: when
    the dedup comparisons moved behind ``null_safe_eq_for`` (so they could
    survive the move onto Postgres), six ``execute`` calls left this scan's
    reach in one commit, and the suite stayed green while checking strictly
    less. Use :func:`_recorded_sql` for anything that must actually be policed;
    keep this one for the statements that genuinely are literals.
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


def _unreadable_execute_calls(module) -> int:
    """How many ``.execute(...)`` calls :func:`_executed_sql` cannot see."""
    tree = ast.parse(inspect.getsource(module))
    blind = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "execute":
            continue
        first = node.args[0] if node.args else None
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            blind += 1
    return blind


def _recorded_sql(store) -> list[str]:
    """SQL actually EXECUTED by a real ``enqueue``, composed strings included.

    Wraps the connection the backend opens and records every statement it runs.
    Strictly stronger than the source scan: it observes the string that reached
    SQLite, so a comparison assembled at runtime is policed exactly like a
    literal one.
    """
    seen: list[str] = []
    real_open = _inbox_sqlite.open_connection

    class _Recorder:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args, **kwargs):
            seen.append(sql)
            return self._conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    @contextmanager
    def _wrapped(path=None):
        with real_open(path) as conn:
            yield _Recorder(conn)

    _inbox_sqlite.open_connection = _wrapped
    try:
        _inbox_sqlite.enqueue(
            "rec-agent",
            event_type="commented",
            card_id="card-1",
            body="b",
            actor=None,
            store=store,
        )
    finally:
        _inbox_sqlite.open_connection = real_open
    return seen


@pytest.fixture
def sqlite_inbox_store(tmp_path):
    """A real store whose inbox sidecar this backend creates on demand."""
    saved_env = {name: os.environ.get(name) for name in _MANAGED}
    saved_cwd = os.getcwd()

    os.environ.pop("SCITEX_DIR", None)
    os.environ["HOME"] = str(tmp_path)
    os.environ["SCITEX_CARDS_AGENT_ID"] = "test-agent"
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


class TestTheBackendSqlParsesOnThisSqlite:
    """Two guards with deliberately different reach -- do not conflate them.

    ``test_no_statement_uses_the_spelling_old_sqlite_rejects`` is the VERSION
    INDEPENDENT one and the reason this class exists: it fails on SQLite 3.45.1
    as loudly as on 3.37.2, so CI catches the regression on a library where the
    bug itself cannot reproduce. That is exactly what the previous behavioural
    tests could not do.

    ``test_every_statement_parses_on_the_local_sqlite`` is version DEPENDENT by
    construction -- it asks whatever SQLite is installed. On a new library it
    would not have caught this bug at all. It is kept as the general net for the
    NEXT non-portable construct, which nobody has thought to name a constant for
    yet, and it is the guard that goes red on the production host.
    """

    def test_the_guard_finds_the_executed_statements(self):
        """Positive control -- an empty scan must not read as a pass."""
        # Arrange
        statements = [s for m in RAIL_MODULES for s in _executed_sql(m)]

        # Act
        selects = [s for s in statements if "SELECT" in s.upper()]

        # Assert
        assert selects, "the AST guard found no SQL to check"

    def test_the_source_scan_admits_what_it_cannot_see(self):
        """The scan's blind spot must be VISIBLE, not merely true.

        Six execute calls left this scan's reach the moment the dedup
        comparisons moved behind ``null_safe_eq_for``, and the suite stayed
        green while policing strictly less. A silent narrowing is the failure
        mode; naming the number is the fix. If this count changes, someone has
        moved SQL across the readable/composed line and should confirm the
        runtime guard below still covers it.
        """
        # Arrange
        modules = RAIL_MODULES

        # Act
        blind = sum(_unreadable_execute_calls(m) for m in modules)

        # Assert -- not zero, and that is precisely the point
        assert blind > 0

    def test_no_statement_uses_the_spelling_old_sqlite_rejects(self):
        # Arrange
        statements = [s for m in RAIL_MODULES for s in _executed_sql(m)]

        # Act
        offenders = [s for s in statements if _REJECTED in s.upper()]

        # Assert
        assert offenders == [], (
            f"SQLite < 3.39 cannot parse these: {offenders}. The production host "
            "measured 3.37.2. Use `IS ?`, null-safe in every SQLite."
        )


class TestTheRuntimeGuardSeesComposedSql:
    """Policing what reached SQLite, not what a literal in the source says.

    This class exists because the source scan above went blind to the dedup
    comparisons the moment they were composed at runtime -- and went blind
    SILENTLY, with every test still green. Recording the executed statements
    restores the guarantee and cannot be defeated the same way: however the
    string is assembled, it is observed after assembly.
    """

    def test_the_recorder_captures_statements(self, sqlite_inbox_store):
        """Positive control -- zero captured statements must not read as a pass."""
        # Arrange
        expected_minimum = 1

        # Act
        recorded = _recorded_sql(sqlite_inbox_store)

        # Assert
        assert len(recorded) >= expected_minimum

    def test_the_recorder_captures_the_dedup_comparison(self, sqlite_inbox_store):
        """The specific statements the AST scan can no longer see."""
        # Arrange
        needle = " IS ?"

        # Act
        recorded = _recorded_sql(sqlite_inbox_store)

        # Assert
        assert any(needle in s for s in recorded)

    def test_no_executed_statement_uses_the_spelling_old_sqlite_rejects(
        self, sqlite_inbox_store
    ):
        """The guarantee that actually matters, on the composed strings."""
        # Arrange
        recorded = _recorded_sql(sqlite_inbox_store)

        # Act
        offenders = [s for s in recorded if _REJECTED in s.upper()]

        # Assert
        assert offenders == [], (
            f"SQLite < 3.39 cannot parse these EXECUTED statements: {offenders}. "
            "The production host measured 3.37.2."
        )

    def test_every_statement_parses_on_the_local_sqlite(self, sqlite_inbox_store):
        """Ask the engine, rather than reasoning about which constructs are new.

        Only SYNTAX errors count. ``EXPLAIN`` also runs semantic analysis, so an
        already-applied idempotent migration (``ALTER TABLE inbox ADD COLUMN
        msg_id``) reports ``duplicate column name`` here -- correct behaviour
        for the module, and not the class of failure this guards against.
        """
        # Arrange
        _inbox_sqlite.enqueue(
            "test-agent",
            event_type="dm",
            card_id="seed",
            body="seed",
            actor=None,
            store=sqlite_inbox_store,
        )
        path = _inbox_sqlite.inbox_db_path(sqlite_inbox_store)
        conn = sqlite3.connect(str(path))
        unparsable = []

        # Act
        for statement in [s for m in RAIL_MODULES for s in _executed_sql(m)]:
            try:
                conn.execute("EXPLAIN " + statement, (None,) * statement.count("?"))
            except sqlite3.OperationalError as exc:
                if "syntax error" in str(exc).lower():
                    unparsable.append((statement, str(exc)))
        conn.close()

        # Assert
        assert unparsable == []


class TestTheComparisonIsNullSafe:
    def test_it_matches_a_null_column_against_a_null_parameter(self):
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (a TEXT)")
        conn.execute("INSERT INTO t VALUES (NULL)")

        # Act
        rows = conn.execute("SELECT COUNT(*) FROM t WHERE a IS ?", (None,)).fetchone()[
            0
        ]

        # Assert
        conn.close()
        assert rows == 1

    def test_it_still_matches_a_real_value(self):
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (a TEXT)")
        conn.execute("INSERT INTO t VALUES ('x')")

        # Act
        rows = conn.execute("SELECT COUNT(*) FROM t WHERE a IS ?", ("x",)).fetchone()[0]

        # Assert
        conn.close()
        assert rows == 1

    def test_it_does_not_match_a_different_value(self):
        """The negative must be reachable, or the positives prove nothing."""
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (a TEXT)")
        conn.execute("INSERT INTO t VALUES ('x')")

        # Act
        rows = conn.execute("SELECT COUNT(*) FROM t WHERE a IS ?", ("y",)).fetchone()[0]

        # Assert
        conn.close()
        assert rows == 0


class TestTheNaiveRewriteIsMeasurablyWrong:
    """POSITIVE CONTROL for the whole change.

    Without this the suite would pass just as happily against ``=``, and the
    reason this spelling exists would be a claim rather than a measurement.
    """

    def test_equals_fails_to_match_null(self):
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (a TEXT)")
        conn.execute("INSERT INTO t VALUES (NULL)")

        # Act
        rows = conn.execute("SELECT COUNT(*) FROM t WHERE a = ?", (None,)).fetchone()[0]

        # Assert
        conn.close()
        assert rows == 0


class TestEnqueueActuallyDelivers:
    """The behaviour the syntax error silently removed."""

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
        """The NULL-safety above, exercised through the real dedup path."""
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
