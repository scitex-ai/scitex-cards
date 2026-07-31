#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the enforcement probe.

Two of these reproduce REAL incidents from 2026-07-30 rather than imagined
ones, and both are the same trap reached independently by two agents hours
apart. That is why the barrier is mechanical instead of documented.
"""

import sqlite3

import pytest

from scitex_cards._enforcement_probe import (
    Enforcement,
    EnforcementVerdict,
    VacuousProbe,
    probe_enforcement,
)

GUARD_SQL = """
CREATE TRIGGER things_no_delete
BEFORE DELETE ON things BEGIN
    SELECT RAISE(ABORT, 'things is append-only: never DELETE');
END;
"""


@pytest.fixture
def guarded(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE things (id TEXT PRIMARY KEY, body TEXT)")
    conn.executemany(
        "INSERT INTO things(id, body) VALUES(?, ?)", [("a", "x"), ("b", "y")]
    )
    conn.executescript(GUARD_SQL)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def unguarded(tmp_path):
    conn = sqlite3.connect(tmp_path / "u.db")
    conn.execute("CREATE TABLE things (id TEXT PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO things(id, body) VALUES('a', 'x')")
    conn.commit()
    yield conn
    conn.close()


class TestARealGuardIsProven:
    def test_a_real_row_gets_refused(self, guarded):
        # Arrange
        sql = "DELETE FROM things WHERE id = ?"
        # Act
        v = probe_enforcement(
            guarded,
            table="things",
            where="id = ?",
            statement=sql,
            params=("a",),
            expect_refusal_containing="append-only",
        )
        # Assert
        assert v.outcome is Enforcement.ENFORCED

    def test_the_verdict_carries_the_refusal_message(self, guarded):
        # Arrange
        sql = "DELETE FROM things WHERE id = ?"
        # Act
        v = probe_enforcement(
            guarded,
            table="things",
            where="id = ?",
            statement=sql,
            params=("a",),
            expect_refusal_containing="append-only",
        )
        # Assert
        assert "append-only" in v.refusal_message

    def test_it_reports_how_many_rows_were_really_at_stake(self, guarded):
        # Arrange
        sql = "DELETE FROM things WHERE id = ?"
        # Act
        v = probe_enforcement(
            guarded,
            table="things",
            where="id = ?",
            statement=sql,
            params=("a",),
            expect_refusal_containing="append-only",
        )
        # Assert
        assert v.rows_touched == 1


class TestTheVacuousProbeIsUnconstructible:
    """MEASURED TWICE on 2026-07-30, by two agents, hours apart.

    A per-row trigger cannot fire on zero rows, so a statement matching
    nothing succeeds and reads exactly like an absent guard.
    """

    def test_a_nonexistent_row_refuses_to_produce_a_verdict(self, guarded):
        # Arrange: this is my own test from that afternoon, verbatim in shape.
        sql = "DELETE FROM things WHERE id = ?"
        # Act
        raised = pytest.raises(VacuousProbe)
        # Assert
        with raised:
            probe_enforcement(
                guarded,
                table="things",
                where="id = ?",
                statement=sql,
                params=("nonexistent-id",),
                expect_refusal_containing="append-only",
            )

    def test_the_refusal_explains_how_to_fix_it(self, guarded):
        # Arrange: an error that only states what broke is half-written.
        message = ""
        try:
            probe_enforcement(
                guarded,
                table="things",
                where="id = ?",
                statement="DELETE FROM things WHERE id = ?",
                params=("nope",),
                expect_refusal_containing="append-only",
            )
        except VacuousProbe as exc:
            message = str(exc)
        # Act
        found = message
        # Assert
        assert "Manufacture the precondition" in found

    def test_it_is_vacuous_even_on_an_unguarded_table(self, unguarded):
        # Arrange: the point is that zero rows tells you nothing EITHER WAY.
        sql = "DELETE FROM things WHERE id = ?"
        # Act
        raised = pytest.raises(VacuousProbe)
        # Assert
        with raised:
            probe_enforcement(
                unguarded,
                table="things",
                where="id = ?",
                statement=sql,
                params=("nope",),
                expect_refusal_containing="append-only",
            )


class TestAnAbsentGuardIsCaught:
    def test_an_unguarded_table_reports_not_enforced(self, unguarded):
        # Arrange
        sql = "DELETE FROM things WHERE id = ?"
        # Act
        v = probe_enforcement(
            unguarded,
            table="things",
            where="id = ?",
            statement=sql,
            params=("a",),
            expect_refusal_containing="append-only",
        )
        # Assert
        assert v.outcome is Enforcement.NOT_ENFORCED

    def test_not_enforced_is_not_proven(self, unguarded):
        # Arrange
        sql = "DELETE FROM things WHERE id = ?"
        # Act
        v = probe_enforcement(
            unguarded,
            table="things",
            where="id = ?",
            statement=sql,
            params=("a",),
            expect_refusal_containing="append-only",
        )
        # Assert
        assert v.proven is False


class TestAMessageFragmentIsRequired:
    """scitex-db's contribution: a read-only connection raises, a typo raises,
    a lock raises. Catching any exception cannot tell those from a guard.
    """

    def test_an_empty_fragment_is_refused(self, guarded):
        # Arrange
        conn = guarded
        # Act
        raised = pytest.raises(ValueError)
        # Assert
        with raised:
            probe_enforcement(
                conn,
                table="things",
                where="id = ?",
                statement="DELETE FROM things WHERE id = ?",
                params=("a",),
                expect_refusal_containing="",
            )

    def test_a_whitespace_fragment_is_refused(self, guarded):
        # Arrange
        conn = guarded
        # Act
        raised = pytest.raises(ValueError)
        # Assert
        with raised:
            probe_enforcement(
                conn,
                table="things",
                where="id = ?",
                statement="DELETE FROM things WHERE id = ?",
                params=("a",),
                expect_refusal_containing="   ",
            )

    def test_a_wrong_fragment_is_inconclusive_not_enforced(self, guarded):
        # Arrange: something refused it, but not demonstrably OUR guard.
        sql = "DELETE FROM things WHERE id = ?"
        # Act
        v = probe_enforcement(
            guarded,
            table="things",
            where="id = ?",
            statement=sql,
            params=("a",),
            expect_refusal_containing="totally-different-text",
        )
        # Assert
        assert v.outcome is Enforcement.INCONCLUSIVE

    def test_inconclusive_is_not_proven(self, guarded):
        # Arrange: INCONCLUSIVE must never read as a pass.
        sql = "DELETE FROM things WHERE id = ?"
        # Act
        v = probe_enforcement(
            guarded,
            table="things",
            where="id = ?",
            statement=sql,
            params=("a",),
            expect_refusal_containing="totally-different-text",
        )
        # Assert
        assert v.proven is False


class TestTheVerdictShapeIsValidated:
    """The constitution: give the dataclass a validator so a malformed answer
    fails where it is built, not three layers downstream.
    """

    def test_enforced_without_a_message_is_rejected(self):
        # Arrange
        kwargs = dict(outcome=Enforcement.ENFORCED, rows_touched=1)
        # Act
        raised = pytest.raises(ValueError)
        # Assert
        with raised:
            EnforcementVerdict(**kwargs)

    def test_a_conclusive_verdict_on_zero_rows_is_rejected(self):
        # Arrange: the vacuity rule again, enforced at the type boundary so it
        # cannot be smuggled past probe_enforcement by constructing directly.
        kwargs = dict(outcome=Enforcement.NOT_ENFORCED, rows_touched=0)
        # Act
        raised = pytest.raises(ValueError)
        # Assert
        with raised:
            EnforcementVerdict(**kwargs)

    def test_inconclusive_on_zero_rows_is_permitted(self):
        # Arrange: "I could not tell" is the one honest zero-row answer.
        v = EnforcementVerdict(outcome=Enforcement.INCONCLUSIVE, rows_touched=0)
        # Act
        outcome = v.outcome
        # Assert
        assert outcome is Enforcement.INCONCLUSIVE

    def test_a_bare_bool_outcome_is_rejected(self):
        # Arrange: never a bare bool -- that is the shape this replaces.
        kwargs = dict(outcome=True, rows_touched=1)
        # Act
        raised = pytest.raises(TypeError)
        # Assert
        with raised:
            EnforcementVerdict(**kwargs)

    def test_negative_rows_are_rejected(self):
        # Arrange
        kwargs = dict(outcome=Enforcement.INCONCLUSIVE, rows_touched=-1)
        # Act
        raised = pytest.raises(ValueError)
        # Assert
        with raised:
            EnforcementVerdict(**kwargs)


class TestTheRetirementCaseThatMotivatedThis:
    """scitex-db's cutover pre-check, reproduced. The store has no
    store_status row, so the forbidden UPDATE matches nothing.
    """

    def test_the_cutover_precheck_as_specified_is_vacuous(self, tmp_path):
        # Arrange: a guarded store that has never been retired.
        from scitex_cards._store_retirement import RETIREMENT_TRIGGER_SQL

        conn = sqlite3.connect(tmp_path / "s.db")
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO schema_meta(key,value) VALUES('store_uuid','u')")
        conn.executescript(RETIREMENT_TRIGGER_SQL)
        conn.commit()
        # Act
        raised = pytest.raises(VacuousProbe)
        # Assert
        with raised:
            probe_enforcement(
                conn,
                table="schema_meta",
                where="key = 'store_status'",
                statement="UPDATE schema_meta SET value='current' WHERE key='store_status'",
                expect_refusal_containing="one-way",
            )
        conn.close()

    def test_with_the_precondition_manufactured_it_proves_enforcement(self, tmp_path):
        # Arrange: insert retired INSIDE the transaction, probe, roll back.
        from scitex_cards._store_retirement import RETIREMENT_TRIGGER_SQL

        conn = sqlite3.connect(tmp_path / "s2.db", isolation_level=None)
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.executescript(RETIREMENT_TRIGGER_SQL)
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO schema_meta(key,value) VALUES('store_status','retired')"
        )
        # Act
        v = probe_enforcement(
            conn,
            table="schema_meta",
            where="key = 'store_status'",
            statement="UPDATE schema_meta SET value='current' WHERE key='store_status'",
            expect_refusal_containing="one-way",
        )
        conn.execute("ROLLBACK")
        # Assert
        assert v.outcome is Enforcement.ENFORCED
        conn.close()


# EOF
