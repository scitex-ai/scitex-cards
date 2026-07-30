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

import sqlite3

import pytest

from scitex_cards._db_migrations import record_migration_provenance


def _store() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def _meta(conn: sqlite3.Connection) -> dict:
    return dict(conn.execute("SELECT key, value FROM schema_meta").fetchall())


# === it must fire on a real upgrade =======================================


def test_an_upgrade_is_recorded():
    # Arrange
    conn = _store()

    # Act
    recorded = record_migration_provenance(conn, 5, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    assert recorded is True


def test_the_record_names_the_version_that_did_it():
    """The field whose absence forced me to infer the culprit from residue."""
    # Arrange
    conn = _store()

    # Act
    record_migration_provenance(conn, 5, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    assert _meta(conn)["schema_migrated_by"] == "0.24.0"


def test_the_record_names_both_ends_of_the_move():
    # Arrange
    conn = _store()

    # Act
    record_migration_provenance(conn, 5, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    meta = _meta(conn)
    assert (meta["schema_migrated_from"], meta["schema_migrated_to"]) == ("5", "7")


def test_the_record_carries_a_timestamp():
    # Arrange
    conn = _store()

    # Act
    record_migration_provenance(conn, 5, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    assert _meta(conn)["schema_migrated_at"] == "2026-07-30T09:00:00Z"


def test_a_later_upgrade_replaces_the_earlier_record():
    """Last upgrade wins: the question is "who moved it to where it is now"."""
    # Arrange
    conn = _store()
    record_migration_provenance(conn, 5, 6, "2026-07-30T09:00:00Z", "0.23.0")

    # Act
    record_migration_provenance(conn, 6, 7, "2026-07-30T10:00:00Z", "0.24.0")

    # Assert
    assert _meta(conn)["schema_migrated_by"] == "0.24.0"


# === it must NOT fire otherwise ===========================================


def test_a_fresh_database_is_not_a_migration():
    """user_version 0 means the file is being CREATED. Stamping it would put a
    migration record on every new store and answer nobody's question."""
    # Arrange
    conn = _store()

    # Act
    recorded = record_migration_provenance(conn, 0, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    assert recorded is False


def test_a_fresh_database_gets_no_rows_at_all():
    # Arrange
    conn = _store()

    # Act
    record_migration_provenance(conn, 0, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    assert _meta(conn) == {}


def test_reopening_an_already_current_store_is_not_a_migration():
    """THE IMPORTANT NEGATIVE. ~90 containers open this store constantly and
    init_schema is idempotent by design. Recording those would rewrite the
    timestamp on every connection, destroying the only thing it is good for."""
    # Arrange
    conn = _store()

    # Act
    recorded = record_migration_provenance(conn, 7, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Assert
    assert recorded is False


def test_reopening_does_not_overwrite_an_existing_record():
    """The earlier upgrade's stamp must survive every subsequent connection."""
    # Arrange
    conn = _store()
    record_migration_provenance(conn, 5, 7, "2026-07-30T09:00:00Z", "0.24.0")

    # Act
    record_migration_provenance(conn, 7, 7, "2026-07-30T23:59:59Z", "0.24.0")

    # Assert
    assert _meta(conn)["schema_migrated_at"] == "2026-07-30T09:00:00Z"


# === end to end through init_schema ======================================


def test_init_schema_stamps_a_real_upgrade(tmp_path):
    """Through the production entry point, on a file that starts at an old
    version -- so the wiring in _db.py is exercised, not just the helper."""
    # Arrange
    from scitex_cards import _db

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    _db.init_schema(conn)
    conn.execute("PRAGMA user_version=5")
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version','5') "
        "ON CONFLICT(key) DO UPDATE SET value='5'"
    )
    conn.commit()

    # Act
    _db.init_schema(conn)

    # Assert
    assert _meta(conn).get("schema_migrated_from") == "5"


def test_init_schema_leaves_a_fresh_database_unstamped(tmp_path):
    """The common path for a new store must stay clean."""
    # Arrange
    from scitex_cards import _db

    conn = sqlite3.connect(tmp_path / "new.db")

    # Act
    _db.init_schema(conn)

    # Assert
    assert "schema_migrated_from" not in _meta(conn)


# === the stamp is a floor ==================================================
#
# Measured on the live store 2026-07-30: four read-only connections seconds
# apart reported user_version 6, 6, 6, then 5 -- while the revision column and
# bump trigger were present in ALL of them. An older client had stamped a newer
# store as older. These tests pin the direction.


def test_an_older_client_cannot_lower_the_stamp(tmp_path):
    """THE REGRESSION TEST. A v5-era client opening a v7 store must not say 5."""
    # Arrange
    from scitex_cards import _db

    conn = sqlite3.connect(tmp_path / "current.db")
    _db.init_schema(conn)
    high = _db.SCHEMA_VERSION + 3
    conn.execute(f"PRAGMA user_version={high}")
    conn.commit()

    # Act
    _db.init_schema(conn)

    # Assert
    assert conn.execute("PRAGMA user_version").fetchone()[0] == high


def test_the_schema_meta_row_also_holds_the_floor(tmp_path):
    """Both stamps must agree, or readers pick whichever suits them."""
    # Arrange
    from scitex_cards import _db

    conn = sqlite3.connect(tmp_path / "current.db")
    _db.init_schema(conn)
    high = _db.SCHEMA_VERSION + 3
    conn.execute(f"PRAGMA user_version={high}")
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(high),),
    )
    conn.commit()

    # Act
    _db.init_schema(conn)

    # Assert
    assert _meta(conn)["schema_version"] == str(high)


def test_the_comparison_is_numeric_not_lexicographic(tmp_path):
    """'10' < '9' as TEXT. A double-digit schema must not be downgraded to 9."""
    # Arrange
    from scitex_cards import _db

    conn = sqlite3.connect(tmp_path / "current.db")
    _db.init_schema(conn)
    conn.execute("PRAGMA user_version=10")
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version','10') "
        "ON CONFLICT(key) DO UPDATE SET value='10'"
    )
    conn.commit()

    # Act
    _db.init_schema(conn)

    # Assert
    assert _meta(conn)["schema_version"] == "10"


def test_a_current_client_still_raises_an_old_stamp(tmp_path):
    """The floor must not block legitimate forward movement."""
    # Arrange
    from scitex_cards import _db

    conn = sqlite3.connect(tmp_path / "old.db")
    _db.init_schema(conn)
    conn.execute("PRAGMA user_version=5")
    conn.commit()

    # Act
    _db.init_schema(conn)

    # Assert
    assert conn.execute("PRAGMA user_version").fetchone()[0] == _db.SCHEMA_VERSION


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# EOF
