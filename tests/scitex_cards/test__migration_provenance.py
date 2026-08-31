#!/usr/bin/env python3
"""A schema upgrade of an existing store must name itself.

WHY. On 2026-07-30 the live store was found to have moved v5 -> v6 and nothing
recorded who did it. The only evidence was a test sentinel that diffs the store
around a run -- but ~90 fleet agents write that store continuously, so it
attributes their writes to whoever is running. I read it as mine and reported
that I had migrated production. I had not; the real culprit had to be inferred
from residue (revision column present, v7 trigger absent => a SCHEMA_VERSION=6
client). These tests exist so that inference is never needed again.

The discriminating cases are the NEGATIVE ones. Stamping every fresh database, or
re-stamping on every connection, would make the field useless for the question it
answers -- and ~90 containers open this store constantly, so "every connection"
is the common path, not an edge case.
"""

import pytest

from scitex_cards import _db
from scitex_cards._db import connect
from scitex_cards._db_migrations import record_migration_provenance

_META = "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)"


@pytest.fixture
def store(new_store):
    """A throwaway store carrying nothing but ``schema_meta``.

    BARE, not provisioned. ``record_migration_provenance`` is the unit under
    test and it reads and writes exactly one table; a full store would also
    carry the version-floor trigger, whose refusals belong to the tests at the
    bottom of this file and would otherwise silently arbitrate these.
    """
    conn = connect(new_store("cards_provenance", bootstrap=False))
    conn.execute(_META)
    conn.commit()
    yield conn
    conn.close()


def _meta(conn) -> dict:
    """``schema_meta`` as a plain dict, read BY COLUMN NAME.

    ``dict(cursor.fetchall())`` was the old spelling, and it relied on each row
    being a two-tuple. The store's rows are dict-shaped, so a pairwise ``dict()``
    over them builds ``{"key": "value"}`` out of the COLUMN NAMES -- a dict of
    exactly the right shape holding entirely the wrong thing, which is worse
    than an error.
    """
    rows = conn.execute("SELECT key, value FROM schema_meta").fetchall()
    return {row["key"]: row["value"] for row in rows}


# === it must fire on a real upgrade =======================================


def test_an_upgrade_is_recorded(store):
    # Arrange
    conn = store

    # Act
    recorded = record_migration_provenance(conn, 5, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    assert recorded is True


def test_the_record_names_the_version_that_did_it(store):
    """The field whose absence forced me to infer the culprit from residue."""
    # Arrange
    conn = store

    # Act
    record_migration_provenance(conn, 5, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    assert _meta(conn)["schema_migrated_by"] == "0.24.0"


def test_the_record_names_both_ends_of_the_move(store):
    # Arrange
    conn = store

    # Act
    record_migration_provenance(conn, 5, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    meta = _meta(conn)
    assert (meta["schema_migrated_from"], meta["schema_migrated_to"]) == ("5", "7")


def test_the_record_carries_a_timestamp(store):
    # Arrange
    conn = store

    # Act
    record_migration_provenance(conn, 5, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    assert _meta(conn)["schema_migrated_at"] == "2026-07-30T09:00:00Z"


def test_a_later_upgrade_replaces_the_earlier_record(store):
    """Last upgrade wins: the question is "who moved it to where it is now"."""
    # Arrange
    conn = store
    record_migration_provenance(conn, 5, 6, "2026-07-30T09:00:00Z", "0.23.0")

    # Act
    record_migration_provenance(conn, 6, 7, "2026-07-30T10:00:00Z", "0.24.0")

    # Assert
    assert _meta(conn)["schema_migrated_by"] == "0.24.0"


# === it must NOT fire otherwise ===========================================


def test_a_fresh_database_is_not_a_migration(store):
    """A prior version of 0 means the store is being CREATED. Stamping it would
    put a migration record on every new store and answer nobody's question."""
    # Arrange
    conn = store

    # Act
    recorded = record_migration_provenance(conn, 0, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    assert recorded is False


def test_a_fresh_database_gets_no_rows_at_all(store):
    # Arrange
    conn = store

    # Act
    record_migration_provenance(conn, 0, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    assert _meta(conn) == {}


def test_reopening_an_already_current_store_is_not_a_migration(store):
    """THE IMPORTANT NEGATIVE. ~90 containers open this store constantly and
    init_schema is idempotent by design. Recording those would rewrite the
    timestamp on every connection, destroying the only thing it is good for."""
    # Arrange
    conn = store

    # Act
    recorded = record_migration_provenance(conn, 7, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    assert recorded is False


def test_reopening_does_not_overwrite_an_existing_record(store):
    """The earlier upgrade's stamp must survive every subsequent connection."""
    # Arrange
    conn = store
    record_migration_provenance(conn, 5, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Act
    record_migration_provenance(conn, 7, 7, "2026-07-30T23:59:59Z", "0.24.0")

    # Assert
    assert _meta(conn)["schema_migrated_at"] == "2026-07-30T09:00:00Z"


# === end to end through init_schema ======================================


def _v5_shaped(new_store, prefix: str):
    """A store that is GENUINELY v5 -- artifacts removed, not just relabelled.

    THE FIXTURE REMOVES THE ARTIFACTS, NOT JUST THE LABELS, and that distinction
    is the whole point. It used to fake an old store by running init_schema and
    then writing a v5 stamp over the result -- a physically v7 store wearing a v5
    label. That is not an old store; it is exactly the corruption observed on the
    live store on 2026-07-31, where the stamp read 5 while ``tasks.revision`` and
    ``tasks_bump_revision`` were both present, and a current client re-migrated
    it every ~45s forever. A fixture that cannot tell the case it is named for
    from the bug we defend against has to get one of them wrong.

    THE STAMP IS LOWERED BY DELETE + INSERT, and the spelling is deliberate:
    ``schema_meta_version_floor`` refuses an UPDATE that lowers
    ``schema_version``. A store genuinely stamped 5 PREDATES that guard, so
    rebuilding one must not go through the path the guard sits on.
    """
    conn = connect(new_store(prefix, bootstrap=False))
    _db.init_schema(conn)
    conn.execute("DROP TRIGGER IF EXISTS tasks_bump_revision ON tasks")
    conn.execute("ALTER TABLE tasks DROP COLUMN revision")
    conn.execute("DELETE FROM schema_meta WHERE key='schema_version'")
    conn.execute("INSERT INTO schema_meta(key, value) VALUES('schema_version','5')")
    conn.commit()
    return conn


def test_init_schema_stamps_a_real_upgrade(new_store):
    """Through the production entry point, on a store that starts at an old
    version -- so the wiring in ``_db`` is exercised, not just the helper."""
    # Arrange
    conn = _v5_shaped(new_store, "cards_prov_v5")

    # Act
    _db.init_schema(conn)

    # Assert
    try:
        assert _meta(conn).get("schema_migrated_from") == "5"
    finally:
        conn.close()


def test_init_schema_leaves_a_fresh_database_unstamped(new_store):
    """The common path for a new store must stay clean."""
    # Arrange
    conn = connect(new_store("cards_prov_fresh", bootstrap=False))

    # Act
    _db.init_schema(conn)

    # Assert
    try:
        assert "schema_migrated_from" not in _meta(conn)
    finally:
        conn.close()


# === the stamp is a floor ==================================================
#
# Measured on the live store 2026-07-30: four read-only connections seconds
# apart reported the version as 6, 6, 6, then 5 -- while the revision column and
# bump trigger were present in ALL of them. An older client had stamped a newer
# store as older. These tests pin the direction.
#
# THERE USED TO BE TWO OF THEM, one per stamp: ``PRAGMA user_version`` and the
# ``schema_meta`` row, "because both stamps must agree, or readers pick whichever
# suits them". The engine that carried the second stamp is gone --
# ``_read_stamps`` returns ``stamped_pragma=None`` here by design -- so the two
# tests became one test written twice, and the duplicate is deleted rather than
# converted into a second copy of its twin.


def test_an_older_client_cannot_lower_the_stamp(new_store):
    """THE REGRESSION TEST. A v5-era client opening a v7 store must not say 5."""
    # Arrange
    conn = connect(new_store("cards_prov_high", bootstrap=False))
    _db.init_schema(conn)
    high = _db.SCHEMA_VERSION + 3
    conn.execute(
        "UPDATE schema_meta SET value=? WHERE key='schema_version'", (str(high),)
    )
    conn.commit()

    # Act
    _db.init_schema(conn)

    # Assert
    try:
        assert _meta(conn)["schema_version"] == str(high)
    finally:
        conn.close()


def test_the_comparison_is_numeric_not_lexicographic(new_store):
    """'10' < '9' as TEXT. A double-digit stamp must not be lowered to 9.

    THE CLIENT VERSION IS PASSED, NOT PATCHED. This used to stamp the store 10
    and re-run ``init_schema``, asserting the stamp stayed 10 -- which exercised
    the digit boundary only while ``SCHEMA_VERSION`` happened to BE 10. When it
    became 11 the correct behaviour was to raise the stamp to 11, so the test
    failed for pinning a constant instead of a rule, and the property in its own
    name had quietly stopped being tested some time before that.

    :func:`stamp_schema_version` already takes the client version as an
    argument, so the single-digit client can simply be handed to it. That
    exercises the real production comparison with real arguments, and it keeps
    doing so at every future schema version.

    THE STORE IS BUILT BARE RATHER THAN THROUGH ``init_schema``, and that is not
    a shortcut. ``init_schema`` installs the version-floor trigger, whose entire
    job is to refuse a lowered stamp -- so on a store it built, the Arrange step
    could no longer write '10' at all once ``SCHEMA_VERSION`` passed 10, and the
    test read back the current version having silently never set up its own
    precondition. The comparison under test is the one inside this function's
    SQL ``CASE``; the trigger is a separate guard with its own tests.
    """
    # Arrange
    from scitex_cards._schema_shape import stamp_schema_version

    conn = connect(new_store("cards_prov_bare", bootstrap=False))
    conn.execute(_META)
    conn.execute("INSERT INTO schema_meta(key, value) VALUES('schema_version','10')")
    conn.commit()

    # Act: a SINGLE-DIGIT client stamps a DOUBLE-DIGIT store. `'10' < '9'` as
    # text, so a lexicographic floor would "raise" this stamp down to 9.
    stamp_schema_version(conn, 10, 9)
    conn.commit()

    # Assert
    try:
        assert _meta(conn)["schema_version"] == "10"
    finally:
        conn.close()


def test_a_current_client_still_raises_an_old_stamp(new_store):
    """The floor must not block legitimate forward movement."""
    # Arrange
    conn = _v5_shaped(new_store, "cards_prov_forward")

    # Act
    _db.init_schema(conn)

    # Assert
    try:
        assert _meta(conn)["schema_version"] == str(_db.SCHEMA_VERSION)
    finally:
        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# EOF
