#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``tasks.revision`` — the column the optimistic lock will assert on.

Half 1 of the P0 (row-level writes) and half 2 (the lock) are independent, and
the trap recorded on the card is that having half 1 makes half 2 LOOK done. This
file covers only the column and its migration; it deliberately does NOT claim the
lock works. The lock's own acceptance test — two writers read revision N, both
write, the SECOND must fail loudly — lands with the write path.

WHY A PLAIN ``rowcount`` CHECK IS NOT THE LOCK, recorded because the obvious
model to copy is the wrong one: sac's state-db already does row-level
``UPDATE ... WHERE pk=?`` with ``rowcount`` checked in eight places and has no
revision column. Their own summary of it — every one of those checks asks "did a
row match?", not "did I overwrite someone else's version?". ``rowcount == 1``
confirms the row EXISTS; it is silent on whether another writer changed it
between my read and my write, which is the only thing a lost update needs.

THE FIXTURES BUILD REAL STORES, and each one is a throwaway PostgreSQL schema
rather than a scratch filename. That is not cosmetic: the shape questions below
are asked through ``table_columns``, which reads ``information_schema``, and the
version stamp is ``schema_meta.schema_version`` because the engine has no
``PRAGMA`` to hold a second one.
"""

from __future__ import annotations

import pytest

from scitex_cards import _db
from scitex_cards._schema_shape import observed_version


def _stamped(conn) -> int | None:
    """The version the store SAYS it is, or None when it says nothing."""
    return observed_version(conn).stamped_meta


@pytest.fixture
def fresh_db(new_store):
    """A brand-new store at the current schema version."""
    conn = _db.connect(new_store("cards_rev_fresh", bootstrap=False))
    _db.init_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def pre_v6_db(new_store):
    """A store shaped like one written before ``revision`` existed.

    Built by initialising current schema then DROPPING the column, rather than
    by hand-writing an old CREATE TABLE — so it stays a real store (indices,
    triggers, DM tables) and cannot drift from the v5 shape as the schema moves.

    The v7 trigger is dropped FIRST, and that is faithfulness rather than a
    workaround: ``tasks_bump_revision``'s ``WHEN`` clause references
    ``NEW.revision``, so the engine records a dependency and refuses to drop a
    column a trigger is built on. A genuine pre-v6 database never had that
    trigger either — v7 introduced it — so removing both is what a real v5 store
    looks like. Dropping only the column would be an impossible state.

    THE STAMP IS REWRITTEN AS A DELETE + INSERT, NOT AN UPDATE, and that spelling
    is the point rather than a trick. ``schema_meta_version_floor`` refuses an
    UPDATE that lowers ``schema_version`` — by design, because a stale client
    knocking the stamp backwards is a measured live incident. A store genuinely
    stamped 5 PREDATES that guard, so reconstructing one cannot go through the
    path the guard sits on without asserting the guard is broken.
    """
    conn = _db.connect(new_store("cards_rev_prev6", bootstrap=False))
    _db.init_schema(conn)
    conn.execute("DROP TRIGGER IF EXISTS tasks_bump_revision ON tasks")
    conn.execute("ALTER TABLE tasks DROP COLUMN revision")
    conn.execute("DELETE FROM schema_meta WHERE key='schema_version'")
    conn.execute("INSERT INTO schema_meta(key, value) VALUES('schema_version', '5')")
    conn.execute(
        "INSERT INTO tasks(id, title, status) VALUES('pre-existing', 't', 'blocked')"
    )
    conn.commit()
    yield conn
    conn.close()


def test_a_fresh_store_has_the_revision_column(fresh_db):
    # Arrange
    table = "tasks"

    # Act
    cols = _db.table_columns(fresh_db, table)

    # Assert
    assert "revision" in cols


def test_a_fresh_store_stamps_the_current_schema_version(fresh_db):
    # Arrange
    expected = _db.SCHEMA_VERSION

    # Act
    got = _stamped(fresh_db)

    # Assert
    assert got == expected


def test_a_new_card_starts_at_revision_zero(fresh_db):
    """Zero, not NULL — an unwritten card has observed zero writes."""
    # Arrange
    fresh_db.execute("INSERT INTO tasks(id, title, status) VALUES('c', 't', 'blocked')")

    # Act
    got = fresh_db.execute("select revision from tasks where id='c'").fetchone()

    # Assert
    assert got["revision"] == 0


def test_the_fixture_really_lacks_the_column(pre_v6_db):
    """Positive control: without this, the migration tests below prove nothing.

    A fixture that silently still HAD the column would make
    ``test_migrating_adds_the_column`` pass against a no-op migration.
    """
    # Arrange
    table = "tasks"

    # Act
    cols = _db.table_columns(pre_v6_db, table)

    # Assert
    assert "revision" not in cols


def test_migrating_adds_the_column(pre_v6_db):
    """The precondition (column genuinely absent) is its own test above."""
    # Arrange
    table = "tasks"

    # Act
    _db.init_schema(pre_v6_db)

    # Assert
    assert "revision" in _db.table_columns(pre_v6_db, table)


def test_migrating_backfills_existing_rows_with_zero(pre_v6_db):
    """The back-fill is CORRECT, not a placeholder.

    Unlike ``card_json``'s load-bearing NULLs, 0 is the true answer for a card
    that predates the counter: no write has been observed under the new model.
    A NULL here would force every reader to handle "unknown revision", and the
    obvious handling — treat NULL as matching — is a silently disabled lock.
    """
    # Arrange
    _db.init_schema(pre_v6_db)

    # Act
    got = pre_v6_db.execute(
        "select revision from tasks where id='pre-existing'"
    ).fetchone()

    # Assert
    assert got["revision"] == 0


def test_migrating_bumps_the_version_stamp(pre_v6_db):
    """A TRANSITION, because the end state alone cannot fail.

    Every store this harness hands out is stamped at the current version, so
    "the stamp reads SCHEMA_VERSION afterwards" is satisfied by a store that was
    never migrated at all. The fixture puts a genuine 5 on it; what is asserted
    is the pair.
    """
    # Arrange
    before = _stamped(pre_v6_db)

    # Act
    _db.init_schema(pre_v6_db)

    # Assert
    assert (before, _stamped(pre_v6_db)) == (5, _db.SCHEMA_VERSION)


def test_migrating_twice_does_not_raise(pre_v6_db):
    """Idempotent: ~90 agents share this store and each opens it repeatedly."""
    # Arrange
    _db.init_schema(pre_v6_db)

    # Act
    _db.init_schema(pre_v6_db)

    # Assert
    assert "revision" in _db.table_columns(pre_v6_db, "tasks")


def test_a_row_update_never_moves_the_counter_backwards(fresh_db):
    """A plain column write must never RESET the counter. Monotonic, not fixed.

    SUPERSEDES an assertion of mine that pinned the value UNCHANGED (== 7). That
    was the correct contract at v6, when nothing incremented the column; v7's
    ``tasks_bump_revision`` makes an ordinary UPDATE bump it to 8 deliberately,
    so the old assertion reported a defect for behaviour that is now the point.

    The property worth pinning is the one that holds under both: revision never
    goes BACKWARDS. A counter that resets under load would make every lock built
    on it compare against a recycled value — worse than no lock, because it would
    look present. `>=` catches that; `==` only caught the version it was written
    against. See test__revision_trigger.py for the increment's own tests.
    """
    # Arrange
    fresh_db.execute("INSERT INTO tasks(id, title, status) VALUES('c', 't', 'blocked')")
    fresh_db.execute("UPDATE tasks SET revision = 7 WHERE id='c'")

    # Act
    fresh_db.execute("UPDATE tasks SET title = 'renamed' WHERE id='c'")

    # Assert
    row = fresh_db.execute("select revision from tasks where id='c'").fetchone()
    assert row["revision"] >= 7


# EOF
