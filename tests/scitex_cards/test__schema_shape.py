#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the schema version floor and the physical-shape reading.

The central case reproduces a live measurement rather than an imagined one:
on 2026-07-31 the fleet store was physically v7 and stamping itself v5.
"""

import contextlib
import itertools

import pytest

from scitex_cards._db import connect
from scitex_cards._ddl import execute_ddl
from scitex_cards._schema_probe import has_trigger
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


#: Each store carved here needs its own name; the tests take the factory rather
#: than a fixture value because a fixture would tie them to ONE arrangement and
#: most of these want a specific starting version.
_SEQ = itertools.count()


@pytest.fixture
def floor_store(new_store):
    """Hand out bare floor-trigger stores, and CLOSE them however the test ends.

    THE OWNERSHIP IS NOT A TIDINESS POINT, it is what keeps a red test red. Each
    store is a throwaway SCHEMA, and the harness drops it with ``DROP SCHEMA ...
    CASCADE`` when the test ends -- which BLOCKS while any connection to it is
    still open. Every test in this file used to close its own connection on the
    line after its assertion, which is exactly the line an assertion failure
    never reaches. MEASURED WHILE CONVERTING THIS FILE: one failing assertion
    did not report red, it HUNG the session indefinitely -- and a hang is the
    worst shape a failure can take, because it reads as a slow runner.

    A file store forgave this; a schema on a shared server does not. So the
    fixture owns every connection it hands out and unwinds them on the way out,
    whatever the test did or did not do.
    """
    conns = []

    def make(*, version="7"):
        conn = _store(new_store, version=version)
        conns.append(conn)
        return conn

    yield make
    for conn in conns:
        with contextlib.suppress(Exception):
            conn.close()


@pytest.fixture
def shaped(new_store):
    """The same ownership rule for the artifact-shaped stores. See ``floor_store``."""
    conns = []

    def make(**kwargs):
        conn = _shaped(new_store, **kwargs)
        conns.append(conn)
        return conn

    yield make
    for conn in conns:
        with contextlib.suppress(Exception):
            conn.close()


def _store(new_store, *, version="7"):
    """A bare store carrying `schema_meta` and the floor trigger, nothing else.

    ``execute_ddl`` rather than a driver script runner, and that is what makes
    the trigger REAL here: the constant below is an inline-body ``CREATE
    TRIGGER`` that this engine cannot parse at all, and ``execute_ddl``
    substitutes it for the plpgsql pair in ``_pg_triggers`` -- which was read
    back out of the running server with ``pg_get_triggerdef``. So these tests
    now exercise the guard that is actually installed on the fleet's store,
    where before they exercised a text no store has ever carried.
    """
    conn = connect(new_store("cards_floor_%d" % next(_SEQ), bootstrap=False))
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)", (version,)
    )
    execute_ddl(conn, SCHEMA_VERSION_FLOOR_TRIGGER_SQL)
    conn.commit()
    return conn


def _version(conn):
    return conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()["value"]


class TestTheFloorHoldsAgainstTheRealOldClient:
    """Not a synthetic UPDATE -- the statement read out of the 0.18.0 install."""

    def test_an_old_client_cannot_lower_the_version(self, floor_store):
        # Arrange
        conn = floor_store(version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert _version(conn) == "7"
        conn.close()

    def test_a_newer_client_can_still_raise_it(self, floor_store):
        # Arrange: a floor must not become a ceiling.
        conn = floor_store(version="5")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("7",))
        # Assert
        assert _version(conn) == "7"
        conn.close()

    def test_an_equal_write_is_left_alone(self, floor_store):
        # Arrange
        conn = floor_store(version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("7",))
        # Assert
        assert _version(conn) == "7"
        conn.close()

    def test_the_old_client_write_still_SUCCEEDS(self, floor_store):
        # Arrange: the whole reason this assigns instead of refusing. If the
        # write raised, every 0.17-0.24 container would fail to open the store.
        conn = floor_store(version="7")
        raised = None
        # Act
        try:
            conn.execute(OLD_CLIENT_STAMP, ("5",))
        except Exception as exc:  # noqa: BLE001 -- asserting nothing is raised
            raised = exc
        # Assert
        assert raised is None
        conn.close()

    def test_comparison_is_numeric_not_lexicographic(self, floor_store):
        # Arrange: as TEXT, '10' < '9'. CAST is what makes 10 win.
        conn = floor_store(version="10")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("9",))
        # Assert
        assert _version(conn) == "10"
        conn.close()

    def test_other_keys_are_untouched_by_the_floor(self, floor_store):
        # Arrange: the trigger must be keyed on schema_version alone.
        conn = floor_store(version="7")
        conn.execute("INSERT INTO schema_meta(key,value) VALUES('source','fresh')")
        # Act
        conn.execute("UPDATE schema_meta SET value='migrated' WHERE key='source'")
        # Assert
        got = conn.execute(
            "SELECT value FROM schema_meta WHERE key='source'"
        ).fetchone()["value"]
        assert got == "migrated"
        conn.close()


class TestTheRefusalIsRecorded:
    """A self-healing guard hides the thing it defends against unless it says
    so. On 2026-07-31 the floor held while the writer stayed unidentified
    through three wrong hypotheses, because the destructive event left no
    trace at all.
    """

    def test_a_refused_downgrade_is_counted(self, floor_store):
        # Arrange
        conn = floor_store(version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert downgrade_report(conn).refused == 1
        conn.close()

    def test_repeated_downgrades_accumulate(self, floor_store):
        # Arrange
        conn = floor_store(version="7")
        # Act
        for _ in range(3):
            conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert downgrade_report(conn).refused == 3
        conn.close()

    def test_it_records_what_was_attempted(self, floor_store):
        # Arrange
        conn = floor_store(version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert downgrade_report(conn).last_attempt == "7 -> 5"
        conn.close()

    def test_it_records_when(self, floor_store):
        # Arrange
        conn = floor_store(version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert downgrade_report(conn).last_at.endswith("Z")
        conn.close()

    def test_a_legal_raise_records_nothing(self, floor_store):
        # Arrange: only REFUSALS are counted, not ordinary writes.
        conn = floor_store(version="5")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("7",))
        # Assert
        assert downgrade_report(conn).refused == 0
        conn.close()

    def test_an_untouched_store_reports_never_attempted(self, floor_store):
        # Arrange
        conn = floor_store(version="7")
        # Act
        report = downgrade_report(conn)
        # Assert
        assert report.ever_attempted is False
        conn.close()

    def test_recording_does_not_disturb_the_floor(self, floor_store):
        # Arrange: the counters are written INSIDE the same trigger, so a bug
        # there could clobber the value the trigger just restored.
        conn = floor_store(version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert _version(conn) == "7"
        conn.close()

    def test_recording_cannot_recurse(self, floor_store):
        # Arrange: the trigger writes to its OWN table, which is the classic way
        # to build an infinite loop. The WHEN clause keys on 'schema_version',
        # so the counter rows cannot re-fire it. THERE IS NO SETTING TO TURN ON
        # ANY MORE and that makes this stronger rather than weaker: the previous
        # engine defaulted recursion OFF, so this test had to enable it to
        # remove a safety net. This engine re-enters a trigger whose body writes
        # its own table by default, so the ordinary case IS the hard case.
        conn = floor_store(version="7")
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
    """Re-entrancy, checked rather than assumed.

    THIS WAS TWO TESTS, one per value of ``recursive_triggers``, because that
    engine defaulted it OFF and "correctness must not rest on a default someone
    can flip". There is no such setting here -- a trigger whose body writes the
    table it fires on re-enters, full stop -- so the two collapse into the one
    case that was previously the harder of them. Nothing is being skipped: the
    setting that had to be turned ON to make the test meaningful is now the
    only behaviour there is.
    """

    def test_the_refused_downgrade_does_not_re_enter_the_trigger(self, floor_store):
        # Arrange: NEW=7/OLD=5 on the re-fire makes the WHEN clause false, which
        # is what stops it rather than any engine default.
        conn = floor_store(version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert _version(conn) == "7"
        conn.close()


class TestTheKnownGapIsPinned:
    """A DELETE followed by an INSERT is not an UPDATE, so no UPDATE trigger sees it.

    THE GAP SURVIVED THE ENGINE CHANGE; ONLY ITS SPELLING DID NOT. It was pinned
    as ``INSERT OR REPLACE``, which is not portable syntax and is refused here
    outright -- but ``INSERT OR REPLACE`` was never anything except a DELETE plus
    an INSERT, and that pair is expressible on every engine. So the gap is the
    same gap, restated in the form a writer could actually reach it by, and the
    source guard below is widened to match.

    No writer in this codebase does this to ``schema_meta``. These tests do not
    assert the gap is acceptable -- they pin it, so a future writer that opens
    it turns this red instead of silently defeating the floor.
    """

    def test_a_delete_then_insert_bypasses_the_floor(self, floor_store):
        # Arrange
        conn = floor_store(version="7")
        # Act
        conn.execute("DELETE FROM schema_meta WHERE key='schema_version'")
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', '5')"
        )
        # Assert
        assert _version(conn) == "5"
        conn.close()

    def test_an_upsert_does_NOT_bypass_the_floor(self, floor_store):
        """The CONTROL, and it is the half that changed answer.

        On the previous engine the portable upsert and the non-portable
        ``INSERT OR REPLACE`` were two ways to write the same statement and only
        one of them fired the guard. ``ON CONFLICT DO UPDATE`` is a real UPDATE,
        so the trigger sees it -- which is why the gap above needs a DELETE to
        reach, rather than being one keyword away from every writer.
        """
        # Arrange
        conn = floor_store(version="7")
        # Act
        conn.execute(OLD_CLIENT_STAMP, ("5",))
        # Assert
        assert _version(conn) == "7"
        conn.close()

    def test_no_shipped_writer_reaches_the_gap_on_schema_meta(self):
        # Arrange: the guard for the gap above. Reads the shipped source.
        import pathlib

        import scitex_cards

        root = pathlib.Path(scitex_cards.__file__).parent
        offenders = []
        needles = ("or replace into schema_meta", "delete from schema_meta")
        # Act
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                lowered = line.lower()
                if any(needle in lowered for needle in needles):
                    offenders.append(f"{path.name}: {line.strip()}")
        # Assert
        assert offenders == []


class TestTheTriggerIsNamedSoAGuardCanAssertIt:
    def test_creating_it_installs_a_trigger_under_that_name(self, floor_store):
        # Arrange
        conn = floor_store()
        # Act
        found = has_trigger(conn, SCHEMA_VERSION_FLOOR_TRIGGER)
        # Assert
        assert found is True
        conn.close()

    def test_applying_it_twice_is_idempotent(self, floor_store):
        # Arrange: it runs on every open, so re-running must not raise.
        conn = floor_store()
        raised = None
        # Act
        try:
            execute_ddl(conn, SCHEMA_VERSION_FLOOR_TRIGGER_SQL)
        except Exception as exc:  # noqa: BLE001
            raised = exc
        # Assert
        assert raised is None
        conn.close()


#: A trigger under the name the v7 rung PROBES, and nothing more.
#:
#: The ladder asks the catalogue whether a trigger called
#: ``tasks_bump_revision`` exists; it does not and cannot ask what its body
#: does. So this fixture places the ARTIFACT, exactly as its predecessor did --
#: that one wrote ``UPDATE tasks SET revision = 1``, which is not the shipped
#: trigger's body either. The real trigger's BEHAVIOUR is tested in
#: ``test__revision_trigger.py``, against the pair ``execute_ddl`` installs.
#:
#: NOT routed through ``execute_ddl``, deliberately. That would substitute the
#: real plpgsql pair, whose ``WHEN`` clause references ``new.revision`` -- so
#: the two tests below that ask for this artifact WITHOUT the v6 column (a
#: deliberately broken chain: v7 present, v6 missing) could not build their
#: fixture at all. A body-free trigger is what makes the stranded-artifact case
#: constructible.
_BUMP_TRIGGER = (
    """CREATE OR REPLACE FUNCTION tasks_bump_revision_fn() RETURNS trigger
       LANGUAGE plpgsql AS $fn$ BEGIN RETURN NEW; END; $fn$""",
    """CREATE OR REPLACE TRIGGER tasks_bump_revision AFTER UPDATE ON tasks
       FOR EACH ROW EXECUTE FUNCTION tasks_bump_revision_fn()""",
)


def _shaped(new_store, *, dm=False, revision=False, bump=False, stamp=None):
    """Build a store carrying exactly the chosen physical artifacts.

    NO SECOND STAMP IS WRITTEN. The fixture used to set ``PRAGMA user_version``
    alongside the ``schema_meta`` row; this engine carries one stamp and
    ``_read_stamps`` returns ``stamped_pragma=None`` on it by design.
    """
    conn = connect(new_store("cards_shape_%d" % next(_SEQ), bootstrap=False))
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
        for statement in _BUMP_TRIGGER:
            conn.execute(statement)
    if stamp is not None:
        conn.execute(
            "INSERT INTO schema_meta(key,value) VALUES('schema_version', ?)",
            (str(stamp),),
        )
    conn.commit()
    return conn


class TestTheShapeIsReadFromArtifacts:
    def test_a_full_v7_store_reads_as_7(self, shaped):
        # Arrange
        conn = shaped(dm=True, revision=True, bump=True, stamp=7)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.observed == 7
        conn.close()

    def test_a_v6_store_reads_as_6(self, shaped):
        # Arrange
        conn = shaped(dm=True, revision=True, stamp=6)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.observed == 6
        conn.close()

    def test_a_v5_store_reads_as_5(self, shaped):
        # Arrange
        conn = shaped(dm=True, stamp=5)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.observed == 5
        conn.close()

    def test_below_the_ladder_floor_is_unknown_not_a_guess(self, shaped):
        # Arrange: v1-v4 left nothing this can distinguish, so it must not
        # invent a number it cannot justify from evidence.
        conn = shaped(stamp=3)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.agreement is ShapeAgreement.UNKNOWN
        conn.close()

    def test_unknown_carries_no_observed_version(self, shaped):
        # Arrange
        conn = shaped(stamp=3)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.observed is None
        conn.close()


class TestTheLiveDisagreementIsDetected:
    """The 2026-07-31 reading: physically v7, stamping 5, both stamps low."""

    def test_a_v7_store_stamped_5_reports_the_stamp_is_low(self, shaped):
        # Arrange
        conn = shaped(dm=True, revision=True, bump=True, stamp=5)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.agreement is ShapeAgreement.STAMP_IS_LOW
        conn.close()

    def test_it_still_reports_the_true_version(self, shaped):
        # Arrange: the value a cutover should verify against.
        conn = shaped(dm=True, revision=True, bump=True, stamp=5)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.trustworthy_version == 7
        conn.close()

    def test_it_keeps_the_stamp_visible_rather_than_only_its_resolution(self, shaped):
        # Arrange
        conn = shaped(dm=True, revision=True, bump=True, stamp=5)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.stamped_meta == 5
        conn.close()

    def test_an_agreeing_store_reports_agreement(self, shaped):
        # Arrange
        conn = shaped(dm=True, revision=True, bump=True, stamp=7)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.agreement is ShapeAgreement.AGREES
        conn.close()

    def test_a_stamp_above_the_shape_is_caught_too(self, shaped):
        # Arrange: claiming a migration that never ran is its own failure.
        conn = shaped(dm=True, stamp=7)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.agreement is ShapeAgreement.STAMP_IS_HIGH
        conn.close()

    # `test_the_lower_of_two_disagreeing_stamps_decides` WAS DELETED HERE. Its
    # subject was that the store carried TWO stamps which disagreed with each
    # other on the live board, and that a gating reader must be judged against
    # the LOW one. There is one stamp now: `_read_stamps` returns
    # `stamped_pragma=None` on this engine BY DESIGN, because there is no
    # `PRAGMA` to hold a second. A rule for resolving a disagreement between two
    # readings cannot be restated when only one reading exists, and inventing a
    # second source to disagree with would be testing the fixture. What the rule
    # PROTECTED -- that the stamp is judged against the physical shape rather
    # than trusted -- is exactly what the rest of this class asserts.


class TestABrokenChainIsNotReportedAsAVersion:
    def test_a_stranded_higher_artifact_does_not_raise_the_reading(self, shaped):
        # Arrange: v7's trigger present, v6's column missing.
        conn = shaped(dm=True, revision=False, bump=True)
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.observed == 5
        conn.close()

    def test_it_names_the_inconsistency(self, shaped):
        # Arrange
        conn = shaped(dm=True, revision=False, bump=True)
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
    """The cutover verifies a store it has quiesced, so this must not write.

    THE REFUSAL IS THE ENGINE'S, NOT A FLAG'S. This opened the file a second
    time with ``mode=ro``. `_backend_connect.connect(read_only=True)` would be
    the obvious replacement and it is the WRONG one -- that argument is
    documented as ADVISORY and deliberately enforces nothing, so a test built on
    it would pass whether or not `observed_version` wrote. So the session is put
    into a genuinely read-only transaction state, where any write RAISES
    ``ReadOnlySqlTransaction``, and the read is asked of that.
    """

    def test_it_reads_a_read_only_transaction(self, shaped):
        # Arrange
        conn = shaped(dm=True, revision=True, bump=True, stamp=5)
        conn.commit()
        conn.execute("SET default_transaction_read_only = on")
        conn.commit()
        # Act
        shape = observed_version(conn)
        # Assert
        assert shape.observed == 7
        conn.close()

    def test_the_read_only_state_is_real(self, shaped):
        """THE CONTROL. Without it the test above passes on a session that was
        never actually read-only, which is the shape of a guard that cannot
        fail.
        """
        # Arrange
        conn = shaped(dm=True, revision=True, bump=True, stamp=5)
        conn.commit()
        conn.execute("SET default_transaction_read_only = on")
        conn.commit()
        raised = None
        # Act
        try:
            conn.execute("INSERT INTO tasks(id, title) VALUES('x', 'y')")
        except Exception as exc:  # noqa: BLE001 - any refusal is the point
            raised = exc
        # Assert
        assert raised is not None
        conn.close()


class TestInitSchemaTakesTheShapeAsItsFloor:
    """The stamp is a FLOOR, and the physical shape is what sets it.

    TWO TESTS WERE DELETED FROM THIS CLASS, and the deletion is the whole story
    of what changed rather than a gap in it. Both were about ``PRAGMA
    user_version`` -- the SECOND stamp:

        test_a_backwards_pragma_does_not_re_trigger_a_migration
        test_the_client_side_max_restores_the_pragma_without_the_shape

    They existed because that stamp could be knocked backwards by any writer
    executing a bare ``PRAGMA user_version=<its own>``, and NO TRIGGER COULD
    REACH IT -- a PRAGMA is not a table write. That is the measured 2026-07-31
    loop: ``schema_migrated_at`` advancing every ~45s with ``from=5 to=7`` while
    v6's ``tasks.revision`` and v7's ``tasks_bump_revision`` were physically
    present the whole time, i.e. a current client re-migrating a store that was
    never behind, forever.

    THE STAMP THEY GUARDED DOES NOT EXIST HERE. ``_read_stamps`` returns
    ``stamped_pragma=None`` on this engine by design, and the one stamp that
    remains lives in ``schema_meta`` -- where a trigger CAN reach it, and does
    (``schema_meta_version_floor``, tested at the top of this file). The failure
    class was not mitigated, it was removed: the unreachable stamp is gone.

    What is kept is the test that is not about the second stamp at all.
    """

    def test_a_fresh_store_is_still_a_create_not_a_migration(self, new_store):
        """The regression guard: flooring must not make every new store look migrated.

        ``observed`` is None when no rung is present, which must leave the prior
        version at 0 -- the branch that distinguishes CREATE from MIGRATE. A
        floor that silently turned fresh stores into migrations would make
        ``schema_migrated_at`` useless for the one question it exists to answer.
        """
        # Arrange
        from scitex_cards._db import init_schema

        conn = connect(new_store("cards_shape_create", bootstrap=False))

        # Act
        init_schema(conn)

        # Assert
        migrated = conn.execute(
            "SELECT COUNT(*) AS n FROM schema_meta WHERE key = 'schema_migrated_at'"
        ).fetchone()["n"]
        conn.close()
        assert migrated == 0

    def test_a_genuinely_older_store_IS_recorded_as_a_migration(self, new_store):
        """THE CONTROL for the test above, and it was missing.

        "No migration was recorded" passes for a fresh store AND for a
        provenance recorder that never fires at all. The pair is what separates
        them: the same verb, on a store that really is behind, must record one.

        The stamp is lowered by DELETE + INSERT because the floor trigger
        refuses an UPDATE that lowers it -- a store genuinely stamped 5 predates
        that guard, so rebuilding one must not go through the path the guard
        sits on.
        """
        # Arrange
        from scitex_cards._db import init_schema

        conn = connect(new_store("cards_shape_migrate", bootstrap=False))
        init_schema(conn)
        conn.execute("DROP TRIGGER IF EXISTS tasks_bump_revision ON tasks")
        conn.execute("ALTER TABLE tasks DROP COLUMN revision")
        conn.execute("DELETE FROM schema_meta WHERE key='schema_version'")
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', '5')"
        )
        conn.execute("DELETE FROM schema_meta WHERE key LIKE 'schema_migrated%'")
        conn.commit()

        # Act
        init_schema(conn)

        # Assert
        migrated = conn.execute(
            "SELECT COUNT(*) AS n FROM schema_meta WHERE key = 'schema_migrated_at'"
        ).fetchone()["n"]
        conn.close()
        assert migrated == 1


# EOF
