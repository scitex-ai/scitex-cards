#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the schema version floor and the physical-shape reading.

The central case reproduces a live measurement rather than an imagined one:
on 2026-07-31 the fleet store was physically v7 and stamping itself v5.
"""

import sqlite3

import pytest

from scitex_cards._schema_shape import (
    SCHEMA_VERSION_FLOOR_TRIGGER,
    SCHEMA_VERSION_FLOOR_TRIGGER_SQL,
    DowngradeReport,
    SchemaShape,
    ShapeAgreement,
    downgrade_report,
    observed_version,
)

# Verbatim shape of the write in this container's installed 0.18.0 client,
# which is one of the ~135 old clients that caused the oscillation.
OLD_CLIENT_STAMP = (
    "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
    "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
)


def _store(tmp_path, name="s.db", *, version="7"):
    conn = sqlite3.connect(tmp_path / name)
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)", (version,)
    )
    conn.executescript(SCHEMA_VERSION_FLOOR_TRIGGER_SQL)
    conn.commit()
    return conn


def _version(conn):
    return conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()[0]


class TestTheFloorHoldsAgainstTheRealOldClient:
    """Not a synthetic UPDATE -- the statement read out of the 0.18.0 install."""

    def test_an_old_client_cannot_lower_the_version(self, tmp_path):
        # Arrange
        conn = _store(tmp_path, version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert _version(conn) == "7"
        conn.close()

    def test_a_newer_client_can_still_raise_it(self, tmp_path):
        # Arrange: a floor must not become a ceiling.
        conn = _store(tmp_path, version="5")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("7",))
        # Assert
        assert _version(conn) == "7"
        conn.close()

    def test_an_equal_write_is_left_alone(self, tmp_path):
        # Arrange
        conn = _store(tmp_path, version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("7",))
        # Assert
        assert _version(conn) == "7"
        conn.close()

    def test_the_old_client_write_still_SUCCEEDS(self, tmp_path):
        # Arrange: the whole reason this assigns instead of refusing. If the
        # write raised, every 0.17-0.24 container would fail to open the store.
        conn = _store(tmp_path, version="7")
        raised = None
        # Act
        try:
            conn.execute(OLD_CLIENT_STAMP, ("5",))
        except Exception as exc:  # noqa: BLE001 -- asserting nothing is raised
            raised = exc
        # Assert
        assert raised is None
        conn.close()

    def test_comparison_is_numeric_not_lexicographic(self, tmp_path):
        # Arrange: as TEXT, '10' < '9'. CAST is what makes 10 win.
        conn = _store(tmp_path, version="10")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("9",))
        # Assert
        assert _version(conn) == "10"
        conn.close()

    def test_other_keys_are_untouched_by_the_floor(self, tmp_path):
        # Arrange: the trigger must be keyed on schema_version alone.
        conn = _store(tmp_path, version="7")
        conn.execute("INSERT INTO schema_meta(key,value) VALUES('source','fresh')")
        # Act
        conn.execute("UPDATE schema_meta SET value='migrated' WHERE key='source'")
        # Assert
        got = conn.execute(
            "SELECT value FROM schema_meta WHERE key='source'"
        ).fetchone()[0]
        assert got == "migrated"
        conn.close()


class TestTheRefusalIsRecorded:
    """A self-healing guard hides the thing it defends against unless it says
    so. On 2026-07-31 the floor held while the writer stayed unidentified
    through three wrong hypotheses, because the destructive event left no
    trace at all.
    """

    def test_a_refused_downgrade_is_counted(self, tmp_path):
        # Arrange
        conn = _store(tmp_path, version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert downgrade_report(conn).refused == 1
        conn.close()

    def test_repeated_downgrades_accumulate(self, tmp_path):
        # Arrange
        conn = _store(tmp_path, version="7")
        # Act
        for _ in range(3):
            conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert downgrade_report(conn).refused == 3
        conn.close()

    def test_it_records_what_was_attempted(self, tmp_path):
        # Arrange
        conn = _store(tmp_path, version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert downgrade_report(conn).last_attempt == "7 -> 5"
        conn.close()

    def test_it_records_when(self, tmp_path):
        # Arrange
        conn = _store(tmp_path, version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert downgrade_report(conn).last_at.endswith("Z")
        conn.close()

    def test_a_legal_raise_records_nothing(self, tmp_path):
        # Arrange: only REFUSALS are counted, not ordinary writes.
        conn = _store(tmp_path, version="5")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("7",))
        # Assert
        assert downgrade_report(conn).refused == 0
        conn.close()

    def test_an_untouched_store_reports_never_attempted(self, tmp_path):
        # Arrange
        conn = _store(tmp_path, version="7")
        # Act
        report = downgrade_report(conn)
        # Assert
        assert report.ever_attempted is False
        conn.close()

    def test_recording_does_not_disturb_the_floor(self, tmp_path):
        # Arrange: the counters are written INSIDE the same trigger, so a bug
        # there could clobber the value the trigger just restored.
        conn = _store(tmp_path, version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert _version(conn) == "7"
        conn.close()

    def test_recording_cannot_recurse_with_recursive_triggers_on(self, tmp_path):
        # Arrange: the trigger now writes to its OWN table, which is the
        # classic way to build an infinite loop. The WHEN clause keys on
        # 'schema_version', so the counter rows cannot re-fire it.
        conn = _store(tmp_path, version="7")
        conn.execute("PRAGMA recursive_triggers=ON")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert downgrade_report(conn).refused == 1
        conn.close()

    def test_a_negative_count_is_rejected(self):
        # Arrange
        kwargs = dict(refused=-1)
        # Act
        raised = pytest.raises(ValueError)
        # Assert
        with raised:
            DowngradeReport(**kwargs)


class TestItCannotLoop:
    """Re-entrancy checked under BOTH pragma settings, not assumed from default."""

    def test_it_terminates_with_recursive_triggers_off(self, tmp_path):
        # Arrange
        conn = _store(tmp_path, version="7")
        conn.execute("PRAGMA recursive_triggers=OFF")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert _version(conn) == "7"
        conn.close()

    def test_it_terminates_with_recursive_triggers_on(self, tmp_path):
        # Arrange: ON is the setting that could recurse, so it is the one that
        # matters. NEW=7/OLD=5 on the re-fire makes the WHEN clause false.
        conn = _store(tmp_path, version="7")
        conn.execute("PRAGMA recursive_triggers=ON")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert _version(conn) == "7"
        conn.close()


class TestTheKnownGapIsPinned:
    """INSERT OR REPLACE is a DELETE+INSERT, so no UPDATE trigger sees it.

    No writer in this codebase uses it on schema_meta. This test does not
    assert the gap is acceptable -- it pins it, so a future writer that opens
    it turns this red instead of silently defeating the floor.
    """

    def test_insert_or_replace_bypasses_the_floor(self, tmp_path):
        # Arrange
        conn = _store(tmp_path, version="7")
        # Act
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) "
            "VALUES('schema_version', '5')"
        )
        # Assert
        assert _version(conn) == "5"
        conn.close()

    def test_no_shipped_writer_uses_or_replace_on_schema_meta(self):
        # Arrange: the guard for the gap above. Reads the shipped source.
        import pathlib

        import scitex_cards

        root = pathlib.Path(scitex_cards.__file__).parent
        offenders = []
        # Act
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                lowered = line.lower()
                if "or replace into schema_meta" in lowered:
                    offenders.append(f"{path.name}: {line.strip()}")
        # Assert
        assert offenders == []


class TestTheTriggerIsNamedSoAGuardCanAssertIt:
    def test_creating_it_installs_a_trigger_under_that_name(self, tmp_path):
        # Arrange
        conn = _store(tmp_path)
        # Act
        found = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
            (SCHEMA_VERSION_FLOOR_TRIGGER,),
        ).fetchone()
        # Assert
        assert found is not None
        conn.close()

    def test_applying_it_twice_is_idempotent(self, tmp_path):
        # Arrange: it runs on every open, so re-running must not raise.
        conn = _store(tmp_path)
        raised = None
        # Act
        try:
            conn.executescript(SCHEMA_VERSION_FLOOR_TRIGGER_SQL)
        except Exception as exc:  # noqa: BLE001
            raised = exc
        # Assert
        assert raised is None
        conn.close()


def _shaped(tmp_path, name, *, dm=False, revision=False, bump=False, stamp=None):
    """Build a store carrying exactly the chosen physical artifacts."""
    conn = sqlite3.connect(tmp_path / name)
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT)")
    if dm:
        for t in (
            "dm_messages",
            "dm_threads",
            "dm_receipts",
            "dm_thread_member_events",
        ):
            conn.execute(f"CREATE TABLE {t} (id TEXT PRIMARY KEY)")
    if revision:
        conn.execute("ALTER TABLE tasks ADD COLUMN revision INTEGER DEFAULT 0")
    if bump:
        conn.executescript(
            "CREATE TRIGGER tasks_bump_revision AFTER UPDATE ON tasks BEGIN "
            "UPDATE tasks SET revision = 1 WHERE id = NEW.id; END;"
        )
    if stamp is not None:
        conn.execute(
            "INSERT INTO schema_meta(key,value) VALUES('schema_version', ?)",
            (str(stamp),),
        )
        conn.execute(f"PRAGMA user_version={int(stamp)}")
    conn.commit()
    return conn


class TestTheShapeIsReadFromArtifacts:
    def test_a_full_v7_store_reads_as_7(self, tmp_path):
        # Arrange
        conn = _shaped(tmp_path, "a.db", dm=True, revision=True, bump=True, stamp=7)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.observed == 7
        conn.close()

    def test_a_v6_store_reads_as_6(self, tmp_path):
        # Arrange
        conn = _shaped(tmp_path, "b.db", dm=True, revision=True, stamp=6)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.observed == 6
        conn.close()

    def test_a_v5_store_reads_as_5(self, tmp_path):
        # Arrange
        conn = _shaped(tmp_path, "c.db", dm=True, stamp=5)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.observed == 5
        conn.close()

    def test_below_the_ladder_floor_is_unknown_not_a_guess(self, tmp_path):
        # Arrange: v1-v4 left nothing this can distinguish, so it must not
        # invent a number it cannot justify from evidence.
        conn = _shaped(tmp_path, "d.db", stamp=3)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.agreement is ShapeAgreement.UNKNOWN
        conn.close()

    def test_unknown_carries_no_observed_version(self, tmp_path):
        # Arrange
        conn = _shaped(tmp_path, "e.db", stamp=3)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.observed is None
        conn.close()


class TestTheLiveDisagreementIsDetected:
    """The 2026-07-31 reading: physically v7, stamping 5, both stamps low."""

    def test_a_v7_store_stamped_5_reports_the_stamp_is_low(self, tmp_path):
        # Arrange
        conn = _shaped(tmp_path, "f.db", dm=True, revision=True, bump=True, stamp=5)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.agreement is ShapeAgreement.STAMP_IS_LOW
        conn.close()

    def test_it_still_reports_the_true_version(self, tmp_path):
        # Arrange: the value a cutover should verify against.
        conn = _shaped(tmp_path, "g.db", dm=True, revision=True, bump=True, stamp=5)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.trustworthy_version == 7
        conn.close()

    def test_it_keeps_the_stamp_visible_rather_than_only_its_resolution(self, tmp_path):
        # Arrange
        conn = _shaped(tmp_path, "h.db", dm=True, revision=True, bump=True, stamp=5)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.stamped_meta == 5
        conn.close()

    def test_an_agreeing_store_reports_agreement(self, tmp_path):
        # Arrange
        conn = _shaped(tmp_path, "i.db", dm=True, revision=True, bump=True, stamp=7)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.agreement is ShapeAgreement.AGREES
        conn.close()

    def test_a_stamp_above_the_shape_is_caught_too(self, tmp_path):
        # Arrange: claiming a migration that never ran is its own failure.
        conn = _shaped(tmp_path, "j.db", dm=True, stamp=7)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.agreement is ShapeAgreement.STAMP_IS_HIGH
        conn.close()

    def test_the_lower_of_two_disagreeing_stamps_decides(self, tmp_path):
        # Arrange: the live store's two stamps disagreed with each other. A
        # gating reader acts on the low one, so that is what must be judged.
        conn = _shaped(tmp_path, "k.db", dm=True, revision=True, bump=True, stamp=7)
        conn.execute("PRAGMA user_version=5")
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.agreement is ShapeAgreement.STAMP_IS_LOW
        conn.close()


class TestABrokenChainIsNotReportedAsAVersion:
    def test_a_stranded_higher_artifact_does_not_raise_the_reading(self, tmp_path):
        # Arrange: v7's trigger present, v6's column missing.
        conn = _shaped(tmp_path, "l.db", dm=True, revision=False, bump=True)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.observed == 5
        conn.close()

    def test_it_names_the_inconsistency(self, tmp_path):
        # Arrange
        conn = _shaped(tmp_path, "m.db", dm=True, revision=False, bump=True)
        # Act
        shape = observed_version(conn)
        # Assert
        assert "inconsistent" in shape.broken_rung
        conn.close()


class TestTheShapeIsValidated:
    def test_a_conclusive_agreement_without_an_observation_is_rejected(self):
        # Arrange
        kwargs = dict(
            observed=None,
            stamped_meta=5,
            stamped_pragma=5,
            agreement=ShapeAgreement.AGREES,
        )
        # Act
        raised = pytest.raises(ValueError)
        # Assert
        with raised:
            SchemaShape(**kwargs)

    def test_a_bare_string_agreement_is_rejected(self):
        # Arrange
        kwargs = dict(
            observed=7,
            stamped_meta=5,
            stamped_pragma=5,
            agreement="stamp-is-low",
        )
        # Act
        raised = pytest.raises(TypeError)
        # Assert
        with raised:
            SchemaShape(**kwargs)

    def test_a_negative_observation_is_rejected(self):
        # Arrange
        kwargs = dict(
            observed=-1,
            stamped_meta=5,
            stamped_pragma=5,
            agreement=ShapeAgreement.AGREES,
        )
        # Act
        raised = pytest.raises(ValueError)
        # Assert
        with raised:
            SchemaShape(**kwargs)


class TestItIsSafeOnAReadOnlyConnection:
    """The cutover verifies a store it has quiesced, so this must not write."""

    def test_it_reads_a_mode_ro_connection(self, tmp_path):
        # Arrange
        conn = _shaped(tmp_path, "n.db", dm=True, revision=True, bump=True, stamp=5)
        conn.close()
        ro = sqlite3.connect(f"file:{tmp_path / 'n.db'}?mode=ro", uri=True)
        # Act
        shape = observed_version(ro)
        # Assert
        assert shape.observed == 7
        ro.close()


class TestInitSchemaTakesTheShapeAsItsFloor:
    """``PRAGMA user_version`` cannot carry a trigger, so the shape must floor it.

    The engine-level floor protects ``schema_meta`` and structurally cannot
    protect the PRAGMA. Measured on the live store 2026-07-31:
    ``schema_migrated_at`` advanced every ~45s with ``from=5 to=7`` while v6's
    ``tasks.revision`` and v7's ``tasks_bump_revision`` were physically present
    throughout. ``record_migration`` returns early when ``prior == new``, so
    that churn proves the PRAGMA genuinely kept reading 5 -- a current client
    re-migrating a store that was never behind, forever.
    """

    def test_a_backwards_pragma_does_not_re_trigger_a_migration(self, tmp_path):
        """The loop: shape says 7, a stale PRAGMA says 5, so a migration is recorded."""
        # Arrange
        from scitex_cards._db import init_schema

        path = tmp_path / "loop.db"
        conn = sqlite3.connect(path)
        init_schema(conn)
        conn.execute("DELETE FROM schema_meta WHERE key LIKE 'schema_migrated%'")
        conn.execute("PRAGMA user_version=5")
        conn.commit()

        # Act
        init_schema(conn)

        # Assert
        migrated = conn.execute(
            "SELECT COUNT(*) FROM schema_meta WHERE key = 'schema_migrated_at'"
        ).fetchone()[0]
        conn.close()
        assert migrated == 0

    def test_the_client_side_max_restores_the_pragma_without_the_shape(self, tmp_path):
        """NOT bound to the shape floor, and named so nobody thinks it is.

        Measured by mutation: with the shape floor disabled this still passes,
        because ``stamp_schema_version`` already writes
        ``max(prior, SCHEMA_VERSION)``. Two different mechanisms restore the
        PRAGMA and only the OTHER test distinguishes them, so calling this one
        "restored to the shape" would credit this change for work it does not
        do -- and a suite that misattributes its own coverage is how a fix gets
        removed later on the belief that a green test still guards it.

        Kept because the invariant is real and worth pinning on its own terms.
        """
        # Arrange
        from scitex_cards._db import init_schema

        path = tmp_path / "restore.db"
        conn = sqlite3.connect(path)
        init_schema(conn)
        conn.execute("PRAGMA user_version=5")
        conn.commit()

        # Act
        init_schema(conn)

        # Assert
        stamped = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert stamped >= 7

    def test_a_fresh_file_is_still_a_create_not_a_migration(self, tmp_path):
        """The regression guard: flooring must not make every new store look migrated.

        ``observed`` is None when no rung is present, which must leave the prior
        version at 0 -- the branch that distinguishes CREATE from MIGRATE. A
        floor that silently turned fresh databases into migrations would make
        ``schema_migrated_at`` useless for the one question it exists to answer.
        """
        # Arrange
        from scitex_cards._db import init_schema

        conn = sqlite3.connect(tmp_path / "fresh.db")

        # Act
        init_schema(conn)

        # Assert
        migrated = conn.execute(
            "SELECT COUNT(*) FROM schema_meta WHERE key = 'schema_migrated_at'"
        ).fetchone()[0]
        conn.close()
        assert migrated == 0


# EOF
