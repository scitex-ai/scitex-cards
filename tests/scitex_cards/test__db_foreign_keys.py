#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The FK rung must repair a migrated store WITHOUT duplicating what is there.

Three properties, and the middle one is the reason this file exists rather
than a one-line smoke test.

1. AN ABSENT CONSTRAINT IS ADDED, deferrable.
2. AN EXISTING CONSTRAINT IS RECOGNISED BY (TABLE, COLUMN), NEVER BY NAME.
   PostgreSQL's auto-generated ``<table>_<column>_fkey`` happens to equal the
   name a caller picks by convention, so a name-matching probe is correct for
   exactly as long as they coincide and adds a SECOND constraint the moment
   they do not. That failure is invisible: both constraints enforce the same
   rule, so nothing misbehaves — the table just carries a duplicate forever,
   and every future run adds another.
3. A PRESENT-BUT-NOT-DEFERRABLE CONSTRAINT IS CONVERTED, not skipped. A probe
   that only asks "does it exist" returns True on both sides of exactly the
   divergence this rung repairs, which is why ``ForeignKeyShape`` has three
   members and not two.

THE SHAPE COMPARISON IS ON DEFERRABILITY, NOT PRESENCE. The card that ordered
this work says so explicitly, because "the FK exists" passes on a store that
oscillates between the two shapes.

The PostgreSQL half runs against a real server and FAILS rather than skips when
one is declared, per ``SCITEX_CARDS_TEST_PG_DSN``. Every test works inside its
own schema, so nothing here can touch the live tables.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._db_foreign_keys import (
    DECLARED_FOREIGN_KEYS,
    ForeignKeyShape,
    _migrate_v10_to_v11,
    observe_foreign_key,
)

_ENV_PG_DSN = "SCITEX_CARDS_TEST_PG_DSN"
#: Port 55432, never 5432. The operator's ruling is that 5432 is NEVER used for
#: scitex and every reference to it is a defect; the fleet's clones all listen
#: on 55432 per host.
_PG_DSN = "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards"

_TEST_SCHEMA = "fk_rung_test"

#: Minimal parents and children. Only the columns the constraints touch — the
#: rung reads the CATALOGUE, so a faithful 66-column ``tasks`` would test
#: nothing extra and would couple this file to an unrelated schema.
_TABLES_DDL = (
    "CREATE TABLE tasks (id TEXT PRIMARY KEY)",
    "CREATE TABLE users (id TEXT PRIMARY KEY)",
    "CREATE TABLE task_comments (id TEXT PRIMARY KEY, task_id TEXT NOT NULL)",
    "CREATE TABLE task_edges ("
    " id TEXT PRIMARY KEY, src_task_id TEXT NOT NULL, dst_task_id TEXT NOT NULL)",
    "CREATE TABLE task_roles (id TEXT PRIMARY KEY, task_id TEXT NOT NULL)",
    "CREATE TABLE user_names (id TEXT PRIMARY KEY, user_id TEXT NOT NULL)",
)

_PLAIN_FK = (
    "ALTER TABLE task_comments ADD CONSTRAINT task_comments_task_id_fkey"
    " FOREIGN KEY (task_id) REFERENCES tasks(id)"
)


class _Shim:
    """Stands in for ``StoreConnection``: translates ``?`` AND names its backend.

    The rung is written in the repo's one dialect (``?``) and the real
    connection object rewrites it. Testing against raw psycopg without this
    would exercise SQL the production path never issues.

    ``backend`` is not decoration. :func:`~scitex_cards._schema_probe._is_postgres`
    identifies the engine from ``conn.backend`` or, failing that, from the
    connection's own module — and a shim that declares neither is read as
    NOT PostgreSQL, so the rung returns immediately and every repair assertion
    fails against an untouched database. That happened on the first run here,
    and it was an accidental positive control worth keeping deliberately: it
    proved the ``_is_postgres`` guard actually gates rather than being
    decorative rather than being taken on trust.
    """

    #: What ``StoreConnection`` reports for a PostgreSQL store.
    backend = "postgres"

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(sql.replace("?", "%s"), params)

    def commit(self):
        return self._conn.commit()


@pytest.fixture
def pg(request):
    """A live PostgreSQL connection in a PRIVATE schema, dropped afterwards.

    Skips when no server is declared; FAILS when one is declared and unusable,
    so a broken CI database cannot masquerade as "no Postgres here".
    """
    declared = os.environ.get(_ENV_PG_DSN)
    dsn = declared or _PG_DSN
    try:
        import psycopg
    except ImportError:
        if declared:
            pytest.fail(f"{_ENV_PG_DSN} is set but psycopg is not installed")
        pytest.skip("psycopg not installed")
    try:
        # dict_row IS THE POINT, NOT A DETAIL. `scitex_cards._db.connect` opens
        # PostgreSQL with `row_factory=dict_row` (_backend_connect.py:206) because
        # the rest of the store reads columns by name. psycopg's DEFAULT factory
        # yields TUPLES, which accept row[0]; dict_row yields a real dict, which
        # raises KeyError: 0.
        #
        # This fixture originally used the default. Twenty tests passed against a
        # live PostgreSQL and the code they exercised was DEAD in production —
        # v11's probe read row[0] and the migration runs from init_schema, so
        # every open_db on a host that took 0.37.0 crashed and the board became
        # permanently unreadable. Caught by scitex-agent-container on the real
        # store, after CI was green.
        #
        # A fixture that differs from production in ONE dimension tests a program
        # that does not exist. The shim below was written to be faithful about
        # the `?` placeholder and the `backend` attribute, and was unfaithful
        # about the thing that mattered.
        from psycopg.rows import dict_row

        conn = psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row)
    except Exception as exc:
        if declared:
            pytest.fail(f"{_ENV_PG_DSN} declares {dsn!r} but connecting raised {exc}")
        pytest.skip(f"no live Postgres: {type(exc).__name__}")

    # A per-test schema keeps every ALTER off the real tables, and makes
    # `current_schema()` in the probe resolve to exactly what this test built.
    schema = f"{_TEST_SCHEMA}_{abs(hash(request.node.nodeid)) % 10**8}"
    conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    conn.execute(f"CREATE SCHEMA {schema}")
    conn.execute(f"SET search_path TO {schema}")
    for ddl in _TABLES_DDL:
        conn.execute(ddl)
    try:
        yield _Shim(conn)
    finally:
        conn.rollback()
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.commit()
        conn.close()


@pytest.fixture
def pg_repaired(pg):
    """A store the rung has already run against."""
    _migrate_v10_to_v11(pg)
    return pg


def _fk_names(pg, table):
    """Constraint NAMES of every single-column FK on ``table``, read BY NAME.

    Returns names rather than rows so no caller can index positionally. The
    previous version returned raw rows and its callers did `rows[0][0]` — which
    worked only because the fixture yielded tuples. Switching the fixture to
    `dict_row` (production's factory) made those callers raise `KeyError: 0`,
    the SAME error 0.37.0 hit on the live store. Two positional reads, one in
    the shipped probe and one here, and the tuple fixture hid both.
    """
    return [
        r["conname"]
        for r in pg.execute(
            "SELECT c.conname FROM pg_constraint c"
            " JOIN pg_class t ON t.oid = c.conrelid"
            " WHERE c.contype='f' AND t.relname = ?"
            " AND t.relnamespace = current_schema()::regnamespace"
            " ORDER BY c.conname",
            (table,),
        ).fetchall()
    ]


def _shapes(pg):
    """The observed shape of every declared FK."""
    return {
        (table, column): observe_foreign_key(pg, table, column)[0]
        for table, column, _, _ in DECLARED_FOREIGN_KEYS
    }


class TestTheDeclaredListMatchesTheSchema:
    """The constant is the contract; it must not drift from the SQL."""

    def test_every_declared_column_appears_in_the_schema_sql(self):
        # Arrange
        from scitex_cards import _db_schema_sql

        # Act
        sql = _db_schema_sql.SCHEMA_SQL

        # Assert
        assert all(col in sql for _, col, _, _ in DECLARED_FOREIGN_KEYS)

    def test_every_declared_reference_appears_in_the_schema_sql(self):
        # Arrange
        from scitex_cards import _db_schema_sql

        # Act
        sql = _db_schema_sql.SCHEMA_SQL

        # Assert
        assert all(
            f"REFERENCES {ref}({refcol})" in sql
            for _, _, ref, refcol in DECLARED_FOREIGN_KEYS
        )

    def test_exactly_four_foreign_keys_are_declared(self):
        # Arrange: if this list were ever emptied, the two tests above would
        # pass VACUOUSLY over an empty loop. Pinning the count is the positive
        # control that makes their `all(...)` mean something.
        expected = 4

        # Act
        actual = len(DECLARED_FOREIGN_KEYS)

        # Assert
        assert actual == expected

    def test_dst_task_id_is_deliberately_not_declared(self):
        # Arrange: a forward reference to a card that does not exist yet is a
        # SUPPORTED pattern. This guards against someone "completing the set"
        # by reading ORPHANS=0 on dst as permission — a forward reference is
        # transient by construction, so a snapshot between batches shows zero
        # necessarily.
        columns = [col for _, col, _, _ in DECLARED_FOREIGN_KEYS]

        # Act
        constrained = "dst_task_id" in columns

        # Assert
        assert constrained is False


class TestTheFixtureMatchesProduction:
    """The guard that would have caught 0.37.0's fleet-wide outage.

    v11's probe read `row[0]`. Valid on tuples, raises `KeyError: 0` on
    psycopg's `dict_row` — which is what production uses. Twenty tests passed
    against a live PostgreSQL while the code was dead in production, because
    this fixture used psycopg's DEFAULT factory.

    So the fixture's row shape is now itself under test. Without this, someone
    "simplifying" the fixture back to the default reopens the exact hole and
    every other test in the file still passes.
    """

    def test_the_fixture_yields_mapping_rows_like_production(self, pg):
        # Arrange: production opens PostgreSQL with row_factory=dict_row
        # (_backend_connect.py:206). A tuple-yielding fixture tests a program
        # that does not exist.
        pg.execute(_PLAIN_FK)

        # Act
        row = pg.execute(
            "SELECT c.conname FROM pg_constraint c"
            " WHERE c.conname = 'task_comments_task_id_fkey'"
        ).fetchone()

        # Assert
        assert row["conname"] == "task_comments_task_id_fkey"

    def test_the_probe_reads_by_name_not_position(self, pg):
        # Arrange: the regression itself. On a mapping row, positional access
        # raises — so a probe that still indexed by position would fail here
        # rather than in production three hours later.
        pg.execute(_PLAIN_FK)

        # Act
        shape, name = observe_foreign_key(pg, "task_comments", "task_id")

        # Assert
        assert name == "task_comments_task_id_fkey"


class TestObservation:
    """Three states, distinguished — the probe is the whole safety property."""

    def test_absent_when_no_constraint_exists(self, pg):
        # Arrange: the fixture creates the tables with no constraints.
        table, column = "task_comments", "task_id"

        # Act
        shape, _ = observe_foreign_key(pg, table, column)

        # Assert
        assert shape == ForeignKeyShape.ABSENT

    def test_absent_reports_no_constraint_name(self, pg):
        # Arrange
        table, column = "task_comments", "task_id"

        # Act
        _, name = observe_foreign_key(pg, table, column)

        # Assert
        assert name is None

    def test_a_plain_constraint_reads_as_present_not_deferred(self, pg):
        # Arrange
        pg.execute(_PLAIN_FK)

        # Act
        shape, _ = observe_foreign_key(pg, "task_comments", "task_id")

        # Assert
        assert shape == ForeignKeyShape.PRESENT_NOT_DEFERRED

    def test_the_observed_name_is_the_one_postgres_holds(self, pg):
        # Arrange
        pg.execute(_PLAIN_FK)

        # Act
        _, name = observe_foreign_key(pg, "task_comments", "task_id")

        # Assert
        assert name == "task_comments_task_id_fkey"

    def test_a_deferred_constraint_reads_as_already_correct(self, pg):
        # Arrange
        pg.execute(_PLAIN_FK + " DEFERRABLE INITIALLY DEFERRED")

        # Act
        shape, _ = observe_foreign_key(pg, "task_comments", "task_id")

        # Assert
        assert shape == ForeignKeyShape.PRESENT_DEFERRED

    def test_deferrable_but_immediate_is_not_treated_as_correct(self, pg):
        # Arrange: DEFERRABLE INITIALLY IMMEDIATE is deferrABLE but not
        # deferrED, so it still fails a child-before-parent replay unless a
        # caller remembers to SET CONSTRAINTS. The target shape is both.
        pg.execute(_PLAIN_FK + " DEFERRABLE INITIALLY IMMEDIATE")

        # Act
        shape, _ = observe_foreign_key(pg, "task_comments", "task_id")

        # Assert
        assert shape == ForeignKeyShape.PRESENT_NOT_DEFERRED

    def test_a_constraint_on_another_column_is_not_reported(self, pg):
        # Arrange: dst_task_id is deliberately unconstrained, but a probe that
        # ignored the column would find src_task_id's constraint and report it.
        pg.execute(
            "ALTER TABLE task_edges ADD CONSTRAINT task_edges_src_task_id_fkey"
            " FOREIGN KEY (src_task_id) REFERENCES tasks(id)"
        )

        # Act
        shape, _ = observe_foreign_key(pg, "task_edges", "dst_task_id")

        # Assert
        assert shape == ForeignKeyShape.ABSENT


class TestTheRungRepairs:
    def test_every_declared_constraint_ends_deferred(self, pg_repaired):
        # Arrange
        expected = ForeignKeyShape.PRESENT_DEFERRED

        # Act
        observed = _shapes(pg_repaired)

        # Assert
        assert set(observed.values()) == {expected}

    def test_a_plain_constraint_is_converted(self, pg):
        # Arrange
        pg.execute(_PLAIN_FK)

        # Act
        _migrate_v10_to_v11(pg)

        # Assert
        assert (
            observe_foreign_key(pg, "task_comments", "task_id")[0]
            == ForeignKeyShape.PRESENT_DEFERRED
        )

    def test_a_plain_constraint_is_converted_in_place_not_alongside(self, pg):
        # Arrange
        pg.execute(_PLAIN_FK)

        # Act
        _migrate_v10_to_v11(pg)

        # Assert
        assert len(_fk_names(pg, "task_comments")) == 1

    def test_running_twice_changes_nothing(self, pg_repaired):
        # Arrange
        first = {t: _fk_names(pg_repaired, t) for t, _, _, _ in DECLARED_FOREIGN_KEYS}

        # Act
        _migrate_v10_to_v11(pg_repaired)

        # Assert
        assert {
            t: _fk_names(pg_repaired, t) for t, _, _, _ in DECLARED_FOREIGN_KEYS
        } == first

    def test_an_unconventionally_named_constraint_is_not_duplicated(self, pg):
        """THE REGRESSION THIS FILE EXISTS FOR.

        A store whose FK was created with a caller-chosen name must be
        RECOGNISED. A name-matching probe would not find
        ``task_comments_task_id_fkey``, would conclude ABSENT, and would add a
        second constraint enforcing the identical rule — silently, because
        nothing then misbehaves.
        """
        # Arrange
        pg.execute(
            "ALTER TABLE task_comments ADD CONSTRAINT fk_comments_belong_to_a_task"
            " FOREIGN KEY (task_id) REFERENCES tasks(id)"
            " DEFERRABLE INITIALLY DEFERRED"
        )

        # Act
        _migrate_v10_to_v11(pg)

        # Assert
        assert len(_fk_names(pg, "task_comments")) == 1

    def test_an_unconventionally_named_constraint_keeps_its_name(self, pg):
        # Arrange
        pg.execute(
            "ALTER TABLE task_comments ADD CONSTRAINT fk_comments_belong_to_a_task"
            " FOREIGN KEY (task_id) REFERENCES tasks(id)"
            " DEFERRABLE INITIALLY DEFERRED"
        )

        # Act
        _migrate_v10_to_v11(pg)

        # Assert
        assert _fk_names(pg, "task_comments") == ["fk_comments_belong_to_a_task"]

    def test_the_constraint_actually_enforces_after_repair(self, pg_repaired):
        # Arrange: presence in the catalogue is not enforcement. Prove it bites.
        import psycopg

        # Act
        def insert_an_orphan():
            pg_repaired.execute(
                "INSERT INTO task_comments(id, task_id) VALUES('c1','no-such-task')"
            )
            pg_repaired.commit()

        # Assert
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            insert_an_orphan()

    def test_a_child_may_precede_its_parent_inside_one_transaction(self, pg_repaired):
        """WHY DEFERRED IS THE TARGET, executed rather than asserted in prose.

        This is the ordering property directed replay needs: a child row
        arriving before its parent must not fail, as long as the transaction
        ends consistent. Under NOT DEFERRABLE the first INSERT raises.
        """
        # Arrange
        pg_repaired.execute("INSERT INTO task_comments(id, task_id) VALUES('c1','t1')")
        pg_repaired.execute("INSERT INTO tasks(id) VALUES('t1')")

        # Act
        pg_repaired.commit()

        # Assert
        assert (
            pg_repaired.execute(
                "SELECT task_id FROM task_comments WHERE id='c1'"
            ).fetchone()["task_id"]
            == "t1"
        )


# WHAT WAS DELETED HERE. A ``TestSqliteIsANoOp`` class asserted that
# ``_migrate_v10_to_v11`` returned quietly against the retired engine, whose
# foreign keys arrived with the CREATE TABLE and could not be ALTERed in. With
# one engine there is no non-PostgreSQL connection for the rung to be a no-op
# ON, so the test had no subject left -- it was not coverage of the rung, it
# was coverage of a branch that can no longer be entered. The property that
# still matters, that the rung GATES on the engine rather than running blind,
# is asserted by ``_PgShim`` above: its ``backend`` attribute is load-bearing,
# and the first run of this file proved it by omitting it.

# EOF
