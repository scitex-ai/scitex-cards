#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``tasks_bump_revision`` — the DB-side half of the optimistic lock (schema v7).

THE ACCEPTANCE CRITERION for this half, stated on
``cards-row-level-writes-with-revision-lock-20260730`` before any of it was
built: two writers read revision N, both write, and the SECOND must fail loudly.
``test_the_second_of_two_writers_is_rejected`` is that test. Everything else here
exists to stop it passing for the wrong reason.

WHY THE INCREMENT IS IN THE DATABASE. An application-side bump is honoured only
by processes running a version that has it, and on 2026-07-30 this store's
writers were simultaneously on 0.13.5 / 0.17.5 / 0.18.0 / 0.22.0. An old writer
that UPDATEs without bumping leaves a current writer's stale ``revision = N``
still matching — the lock is satisfied and the old write is lost silently. Unlike
the blocked-check clock, an optimistic lock has no safe direction to fail in.

WHY ASSIGN AND NOT REJECT. Rejecting a write whose revision is not
``OLD.revision + 1`` is symmetrical across engines, and it would ABORT every
UPDATE from a writer that does not know about the column — i.e. break the fleet
until every container is current, which is the condition that cannot be
established. Assign keeps old writers working and bumps on their behalf.

THESE TESTS RUN AGAINST THE ENGINE THAT SHIPS, and for this file that changed
what one of them measures rather than only how it is spelled — see
``test_the_bump_does_not_recurse``. The trigger installed on the store is the
plpgsql pair in ``_pg_triggers``, not the inline-body text the constant carries,
so a test against a different engine was exercising a trigger no store has.
"""

from __future__ import annotations

import pytest

from scitex_cards import _db
from scitex_cards._schema_probe import has_trigger
from scitex_cards._schema_shape import observed_version

TRIGGER = "tasks_bump_revision"


def _carded_store(new_store, prefix: str):
    """A fresh canonical store at the current schema version, holding one card."""
    conn = _db.connect(new_store(prefix, bootstrap=False))
    _db.init_schema(conn)
    conn.execute("INSERT INTO tasks(id, title, status) VALUES('c', 't', 'blocked')")
    conn.commit()
    return conn


@pytest.fixture
def store(new_store):
    """A fresh canonical store at the current schema version."""
    conn = _carded_store(new_store, "cards_revtrg")
    yield conn
    conn.close()


def test_the_trigger_exists_on_a_fresh_store(store):
    """Positive control: without this, every behaviour test below is vacuous."""
    # Arrange
    name = TRIGGER

    # Act
    found = has_trigger(store, name)

    # Assert
    assert found is True


def test_a_new_card_starts_at_revision_zero(store):
    # Arrange
    card = "c"

    # Act
    got = store.execute("select revision from tasks where id=?", (card,)).fetchone()

    # Assert
    assert got["revision"] == 0


def test_an_old_writer_still_bumps_the_revision(store):
    """The whole reason this is DB-side: a writer ignorant of the column bumps it.

    Simulates a 0.13.5 client — it updates a field and never mentions revision.
    If this returned 0, an old writer could leave a current writer's stale lock
    matching, and the lock would report success while losing the write.
    """
    # Arrange
    store.execute("UPDATE tasks SET title='renamed' WHERE id='c'")

    # Act
    got = store.execute("select revision from tasks where id='c'").fetchone()

    # Assert
    assert got["revision"] == 1


def test_an_old_writers_update_is_not_rejected(store):
    """Assign, not reject: the old writer's change must actually land.

    This is the test that would fail under REJECT semantics, and failing it would
    mean the fleet's non-current writers stop working.
    """
    # Arrange
    store.execute("UPDATE tasks SET title='renamed' WHERE id='c'")

    # Act
    got = store.execute("select title from tasks where id='c'").fetchone()

    # Assert
    assert got["title"] == "renamed"


def test_the_second_of_two_writers_is_rejected(store):
    """THE acceptance criterion. Both read revision 0; the second must not win.

    A bare row-level ``UPDATE ... WHERE id=?`` passes rowcount == 1 for BOTH
    writers — that check answers "does the row exist", not "did I overwrite
    someone else's version". The revision predicate is what separates them.
    """
    # Arrange — both writers observed revision 0.
    seen_by_both = 0
    store.execute(
        "UPDATE tasks SET title='first', revision=? WHERE id='c' AND revision=?",
        (seen_by_both + 1, seen_by_both),
    )

    # Act — the second writer, still holding the revision it read.
    loser = store.execute(
        "UPDATE tasks SET title='second', revision=? WHERE id='c' AND revision=?",
        (seen_by_both + 1, seen_by_both),
    )

    # Assert
    assert loser.rowcount == 0


def test_the_first_writers_value_survives_the_conflict(store):
    """Rejected means rejected — the loser must not have partially landed."""
    # Arrange
    store.execute(
        "UPDATE tasks SET title='first', revision=1 WHERE id='c' AND revision=0"
    )

    # Act
    store.execute(
        "UPDATE tasks SET title='second', revision=1 WHERE id='c' AND revision=0"
    )

    # Assert
    row = store.execute("select title from tasks where id='c'").fetchone()
    assert row["title"] == "first"


def test_an_explicitly_passed_revision_is_not_overwritten(store):
    """The ``WHEN`` guard's other job, and the subtle one.

    An unconditional assign would set revision = OLD + 1 even when the writer
    supplied its own value — so a lock-holding writer's intended revision would
    be replaced, and the counter would become unusable AS a lock. Here the writer
    passes 1 explicitly and must get 1, not 2.
    """
    # Arrange
    store.execute("UPDATE tasks SET title='x', revision=1 WHERE id='c' AND revision=0")

    # Act
    got = store.execute("select revision from tasks where id='c'").fetchone()

    # Assert
    assert got["revision"] == 1


def test_the_bump_does_not_recurse(store):
    """The WHEN guard, with no engine default underneath it.

    THIS TEST GOT STRONGER RATHER THAN WEAKER. It used to set
    ``PRAGMA recursive_triggers=1`` first, and said so: on the previous engine
    recursion was OFF by default, so the plain case proved nothing and the
    PRAGMA had to be flipped to remove the safety net. This engine has no such
    default to lean on — an AFTER UPDATE trigger whose body issues an UPDATE on
    the same table re-enters the trigger — so the ordinary case IS the case that
    used to require setting up. Exactly one bump means the ``WHEN`` guard, and
    only the ``WHEN`` guard, stopped the re-fire: the nested write changes
    ``revision``, so on re-entry the condition no longer holds.
    """
    # Arrange
    card = "c"

    # Act
    store.execute("UPDATE tasks SET title='deep' WHERE id='c'")

    # Assert
    got = store.execute("select revision from tasks where id=?", (card,)).fetchone()
    assert got["revision"] == 1


def test_installing_the_trigger_twice_does_not_raise(store):
    """~90 containers open this store repeatedly; init_schema must be idempotent."""
    # Arrange
    before = _db.SCHEMA_VERSION

    # Act
    _db.init_schema(store)

    # Assert
    assert observed_version(store).stamped_meta == before


def test_a_pre_v7_store_gains_the_trigger_on_open(new_store):
    """The existing population: an older store must acquire the enforcement.

    Built by dropping the trigger from a current store rather than hand-writing an
    old schema, so the fixture cannot drift from the real v6 shape. The version
    stamp is left alone deliberately: ``observed_version`` walks the PHYSICAL
    ladder and v7's rung IS this trigger, so removing it is what makes the store
    read as v6 — a stamp edit would only be a claim about it.
    """
    # Arrange
    conn = _db.connect(new_store("cards_revtrg_prev7", bootstrap=False))
    _db.init_schema(conn)
    conn.execute(f"DROP TRIGGER {TRIGGER} ON tasks")
    conn.commit()
    before = has_trigger(conn, TRIGGER)

    # Act
    _db.init_schema(conn)

    # Assert
    try:
        assert (before, has_trigger(conn, TRIGGER)) == (False, True)
    finally:
        conn.close()


def test_the_pre_v7_fixture_really_lacks_the_trigger(new_store):
    """Positive control for the migration test above.

    A fixture that silently still HAD the trigger would let a no-op migration
    pass ``test_a_pre_v7_store_gains_the_trigger_on_open``.
    """
    # Arrange
    conn = _db.connect(new_store("cards_revtrg_control", bootstrap=False))
    _db.init_schema(conn)

    # Act
    conn.execute(f"DROP TRIGGER {TRIGGER} ON tasks")

    # Assert
    try:
        assert has_trigger(conn, TRIGGER) is False
    finally:
        conn.close()


def test_a_physical_delete_of_a_card_is_still_possible_here(new_store):
    """Documents what this trigger does NOT do, so nobody assumes coverage.

    ``tasks`` has no no-delete trigger — the append-only ruling is enforced for
    cards by the application (tombstones via ``deleted_at``) and by the five
    ``dm_*`` triggers for DMs. The revision trigger is a LOCK, not a delete guard.
    Asserting the gap keeps it a known gap rather than a surprise; if `tasks` ever
    gains its own no-delete trigger, this test should fail and be updated.
    """
    # Arrange
    conn = _db.connect(new_store("cards_revtrg_delete", bootstrap=False))
    _db.init_schema(conn)
    conn.execute("INSERT INTO tasks(id, title, status) VALUES('gone', 't', 'blocked')")

    # Act
    conn.execute("DELETE FROM tasks WHERE id='gone'")

    # Assert
    try:
        row = conn.execute(
            "select count(*) AS n from tasks where id='gone'"
        ).fetchone()
        assert row["n"] == 0
    finally:
        conn.close()


def test_the_trigger_sql_is_a_single_source_for_translation():
    """The PostgreSQL pair is DERIVED from this constant, so it must live once.

    ``execute_ddl`` substitutes each inline-body ``CREATE TRIGGER`` for the
    plpgsql pair in ``_pg_triggers`` BY NAME, and raises on a name it does not
    recognise. So this constant is still the single declaration — what changed
    is that a second file has to agree with it, and the substitution's own
    refusal is what makes a disagreement loud.
    """
    # Arrange
    from scitex_cards import _db_migrations

    # Act
    sql = _db_migrations.REVISION_TRIGGER_SQL

    # Assert
    assert "NEW.revision = OLD.revision" in sql


def test_the_declared_trigger_has_a_postgres_pair():
    """The other half of the single source: the substitution can find it.

    Without this the constant above could name a trigger ``_pg_triggers`` has
    never heard of, and the failure would surface as a raise inside
    ``init_schema`` on a fresh store rather than here.
    """
    # Arrange
    from scitex_cards._pg_triggers import PG_TRIGGER_BY_NAME

    # Act
    pair = PG_TRIGGER_BY_NAME.get(TRIGGER)

    # Assert
    assert pair is not None


# EOF
