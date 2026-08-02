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
"""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile

import pytest

from scitex_cards import _db

TRIGGER = "tasks_bump_revision"


@pytest.fixture
def store():
    """A fresh canonical store at the current schema version."""
    d = pathlib.Path(tempfile.mkdtemp())
    conn = _db.connect(d / "t.db")
    _db.init_schema(conn)
    conn.execute("INSERT INTO tasks(id, title, status) VALUES('c', 't', 'blocked')")
    conn.commit()
    yield conn
    conn.close()


def test_the_trigger_exists_on_a_fresh_store(store):
    """Positive control: without this, every behaviour test below is vacuous."""
    # Arrange
    q = "select name from sqlite_master where type='trigger' and name=?"

    # Act
    found = [r[0] for r in store.execute(q, (TRIGGER,))]

    # Assert
    assert found == [TRIGGER]


def test_a_new_card_starts_at_revision_zero(store):
    # Arrange
    card = "c"

    # Act
    got = store.execute("select revision from tasks where id=?", (card,)).fetchone()[0]

    # Assert
    assert got == 0


def test_an_old_writer_still_bumps_the_revision(store):
    """The whole reason this is DB-side: a writer ignorant of the column bumps it.

    Simulates a 0.13.5 client — it updates a field and never mentions revision.
    If this returned 0, an old writer could leave a current writer's stale lock
    matching, and the lock would report success while losing the write.
    """
    # Arrange
    store.execute("UPDATE tasks SET title='renamed' WHERE id='c'")

    # Act
    got = store.execute("select revision from tasks where id='c'").fetchone()[0]

    # Assert
    assert got == 1


def test_an_old_writers_update_is_not_rejected(store):
    """Assign, not reject: the old writer's change must actually land.

    This is the test that would fail under REJECT semantics, and failing it would
    mean the fleet's non-current writers stop working.
    """
    # Arrange
    store.execute("UPDATE tasks SET title='renamed' WHERE id='c'")

    # Act
    got = store.execute("select title from tasks where id='c'").fetchone()[0]

    # Assert
    assert got == "renamed"


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
    assert (
        store.execute("select title from tasks where id='c'").fetchone()[0] == "first"
    )


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
    got = store.execute("select revision from tasks where id='c'").fetchone()[0]

    # Assert
    assert got == 1


def test_the_bump_does_not_recurse_with_recursive_triggers_on(store):
    """Correctness must not rest on a PRAGMA default someone can flip.

    ``recursive_triggers`` defaults to OFF, which alone would make the nested
    UPDATE safe — but a default is not a guarantee. The ``WHEN`` guard is the real
    protection: the nested write changes ``revision``, so on a re-fire the
    condition no longer holds.
    """
    # Arrange
    store.execute("pragma recursive_triggers=1")

    # Act
    store.execute("UPDATE tasks SET title='deep' WHERE id='c'")

    # Assert
    assert store.execute("select revision from tasks where id='c'").fetchone()[0] == 1


def test_installing_the_trigger_twice_does_not_raise(store):
    """~90 containers open this store repeatedly; init_schema must be idempotent."""
    # Arrange
    before = _db.SCHEMA_VERSION

    # Act
    _db.init_schema(store)

    # Assert
    assert store.execute("pragma user_version").fetchone()[0] == before


def test_a_pre_v7_store_gains_the_trigger_on_open():
    """The existing population: an older file must acquire the enforcement.

    Built by dropping the trigger from a current store rather than hand-writing an
    old schema, so the fixture cannot drift from the real v6 shape.
    """
    # Arrange
    d = pathlib.Path(tempfile.mkdtemp())
    conn = _db.connect(d / "old.db")
    _db.init_schema(conn)
    conn.execute(f"DROP TRIGGER {TRIGGER}")
    conn.execute("PRAGMA user_version=6")
    conn.commit()

    # Act
    _db.init_schema(conn)

    # Assert
    q = "select name from sqlite_master where type='trigger' and name=?"
    assert [r[0] for r in conn.execute(q, (TRIGGER,))] == [TRIGGER]


def test_the_pre_v7_fixture_really_lacks_the_trigger():
    """Positive control for the migration test above.

    A fixture that silently still HAD the trigger would let a no-op migration
    pass ``test_a_pre_v7_store_gains_the_trigger_on_open``.
    """
    # Arrange
    d = pathlib.Path(tempfile.mkdtemp())
    conn = _db.connect(d / "old.db")
    _db.init_schema(conn)

    # Act
    conn.execute(f"DROP TRIGGER {TRIGGER}")

    # Assert
    q = "select name from sqlite_master where type='trigger' and name=?"
    assert [r[0] for r in conn.execute(q, (TRIGGER,))] == []


def test_a_physical_delete_of_a_card_is_still_possible_here():
    """Documents what this trigger does NOT do, so nobody assumes coverage.

    ``tasks`` has no no-delete trigger — the append-only ruling is enforced for
    cards by the application (tombstones via ``deleted_at``) and by the five
    ``dm_*`` triggers for DMs. The revision trigger is a LOCK, not a delete guard.
    Asserting the gap keeps it a known gap rather than a surprise; if `tasks` ever
    gains its own no-delete trigger, this test should fail and be updated.
    """
    # Arrange
    d = pathlib.Path(tempfile.mkdtemp())
    conn = _db.connect(d / "t.db")
    _db.init_schema(conn)
    conn.execute("INSERT INTO tasks(id, title, status) VALUES('gone', 't', 'blocked')")

    # Act
    conn.execute("DELETE FROM tasks WHERE id='gone'")

    # Assert
    assert conn.execute("select count(*) from tasks where id='gone'").fetchone()[0] == 0


def test_the_trigger_sql_is_a_single_source_for_translation():
    """scitex-db translates this to PL/pgSQL, so it must live in ONE place.

    Their migration tool drops triggers unless it carries them explicitly, and
    they gate on it in preflight now. A constant they can read beats a string
    duplicated between the fresh-schema path and the migration.
    """
    # Arrange
    from scitex_cards import _db_migrations

    # Act
    sql = _db_migrations.REVISION_TRIGGER_SQL

    # Assert
    assert "NEW.revision = OLD.revision" in sql


# EOF
