#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for one-way, engine-enforced store retirement.

THE ENFORCEMENT TESTS ATTEMPT THE FORBIDDEN TRANSITION AND REQUIRE A REFUSAL.

They do not check that a trigger exists. Earlier today I shipped an enforcement
test that deleted a NONEXISTENT id -- a BEFORE DELETE trigger fires per row, so
deleting zero rows succeeded trivially and the test reported "not enforced"
against a store where the guard provably works. A test that cannot fail for the
right reason cannot pass for one either. Every test here mutates a row that
really exists, and each captures the outcome with try/except so the refusal and
its message are one assertion rather than two fused ones.
"""

import contextlib
import itertools

import pytest

from scitex_cards._db import connect
from scitex_cards._ddl import execute_ddl
from scitex_cards._schema_probe import trigger_names
from scitex_cards._store_retirement import (
    RETIREMENT_TRIGGER_SQL,
    STATUS_CURRENT,
    STATUS_RETIRED,
    TRIGGER_NAMES,
    StoreCannotProveItsStatus,
    StoreRetired,
    read_status,
    retire_store,
)


_SEQ = itertools.count()

_META = "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)"


def _open_bare(new_store, prefix: str):
    """An unprovisioned throwaway store carrying nothing but ``schema_meta``."""
    conn = connect(new_store("%s_%d" % (prefix, next(_SEQ)), bootstrap=False))
    conn.execute(_META)
    return conn


@pytest.fixture
def bare_store(new_store):
    """Hand out bare stores, and CLOSE them however the test ends.

    THE OWNERSHIP IS WHAT KEEPS A RED TEST RED. Each store is a throwaway
    SCHEMA, and the harness drops it with ``DROP SCHEMA ... CASCADE`` when the
    test ends -- which BLOCKS while any connection to it is still open. A test
    that opens a connection and then raises never reaches its own ``close()``,
    so the failure does not report red: it HANGS, which reads as a slow runner
    rather than as a failure. Measured twice while converting this branch, in
    two different files.

    A file store forgave this; a schema on a shared server does not.
    """
    conns = []

    def make(prefix: str):
        conn = _open_bare(new_store, prefix)
        conns.append(conn)
        return conn

    yield make
    for conn in conns:
        with contextlib.suppress(Exception):
            conn.close()


@pytest.fixture
def guarded_store(new_store):
    """A throwaway store with schema_meta and the retirement guards installed.

    ``execute_ddl``, not a driver script runner, and that is what makes these
    tests exercise the guard the fleet actually carries: the constant is an
    inline-body ``CREATE TRIGGER`` no store has ever held, and ``execute_ddl``
    substitutes it for the plpgsql pair in ``_pg_triggers`` -- which was read
    back out of a running server with ``pg_get_triggerdef``.
    """
    conn = _open_bare(new_store, "cards_retire")
    conn.executemany(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?)",
        [("store_uuid", "uuid-source"), ("store_status", STATUS_CURRENT)],
    )
    execute_ddl(conn, RETIREMENT_TRIGGER_SQL)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def retired_store(guarded_store):
    """A store that has been retired in favour of uuid-destination."""
    guarded_store.execute(
        "UPDATE schema_meta SET value = ? WHERE key = 'store_status'", (STATUS_RETIRED,)
    )
    guarded_store.executemany(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?)",
        [
            ("retired_at", "2026-07-30T15:00:00Z"),
            ("retired_in_favour_of", "uuid-destination"),
        ],
    )
    guarded_store.commit()
    yield guarded_store


def _refusal_message(conn, sql, params=()):
    """Run sql and return the refusal message, or "" if it was allowed.

    try/except rather than pytest.raises so the outcome and its message are a
    single assertion. Fusing them hides the second when the first fails.

    THE ROLLBACK IS NOT TIDYING. A failed statement puts this engine's
    transaction into an aborted state, where EVERY subsequent statement fails
    with ``InFailedSqlTransaction`` -- so without it the next read (``_value_of``,
    which several tests below call precisely to prove the data survived) would
    fail for a reason that has nothing to do with the guard. The previous
    engine simply carried on after an IntegrityError, which is why nothing here
    had to say this before.

    The exception type is not narrowed to one class either: the guard raises
    from inside plpgsql, and pinning the driver's exception class would be
    asserting something about the driver rather than about the refusal. The
    MESSAGE is what the callers assert on, and that is the guard's own text.
    """
    try:
        conn.execute(sql, params)
        return ""
    except Exception as exc:  # noqa: BLE001 - the message is the assertion
        conn.rollback()
        return str(exc)


def _value_of(conn, key):
    row = conn.execute("SELECT value FROM schema_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


class TestRetiringIsPermitted:
    def test_current_can_become_retired(self, retired_store):
        # Arrange
        conn = retired_store
        # Act
        value = _value_of(conn, "store_status")
        # Assert
        assert value == STATUS_RETIRED

    def test_the_successor_is_recorded(self, retired_store):
        # Arrange
        conn = retired_store
        # Act
        value = _value_of(conn, "retired_in_favour_of")
        # Assert
        assert value == "uuid-destination"


class TestRetirementIsOneWay:
    """The forbidden transition must be REFUSED BY THE ENGINE."""

    def test_retired_cannot_become_current_again(self, retired_store):
        # Arrange
        sql = "UPDATE schema_meta SET value = ? WHERE key = 'store_status'"
        # Act
        message = _refusal_message(retired_store, sql, (STATUS_CURRENT,))
        # Assert
        assert "one-way" in message

    def test_the_status_is_unchanged_after_the_refusal(self, retired_store):
        # Arrange: proves the refusal protected the data, not merely raised.
        sql = "UPDATE schema_meta SET value = ? WHERE key = 'store_status'"
        _refusal_message(retired_store, sql, (STATUS_CURRENT,))
        # Act
        value = _value_of(retired_store, "store_status")
        # Assert
        assert value == STATUS_RETIRED

    def test_writing_null_over_the_status_is_also_refused(self, retired_store):
        # Arrange: SQLite's <> is NULL-propagating, so a guard using <> would
        # let NULL through and the retirement would be erasable.
        sql = "UPDATE schema_meta SET value = NULL WHERE key = 'store_status'"
        # Act
        message = _refusal_message(retired_store, sql)
        # Assert
        assert "one-way" in message

    def test_an_arbitrary_other_value_is_refused(self, retired_store):
        # Arrange
        sql = "UPDATE schema_meta SET value = ? WHERE key = 'store_status'"
        # Act
        message = _refusal_message(retired_store, sql, ("probably-fine",))
        # Assert
        assert "one-way" in message


class TestRetirementIsUndeletable:
    def test_the_status_row_cannot_be_deleted(self, retired_store):
        # Arrange
        sql = "DELETE FROM schema_meta WHERE key = 'store_status'"
        # Act
        message = _refusal_message(retired_store, sql)
        # Assert
        assert "cannot be deleted" in message

    def test_the_successor_record_cannot_be_deleted(self, retired_store):
        # Arrange
        sql = "DELETE FROM schema_meta WHERE key = 'retired_in_favour_of'"
        # Act
        message = _refusal_message(retired_store, sql)
        # Assert
        assert "cannot be deleted" in message

    def test_the_row_survives_the_refused_delete(self, retired_store):
        # Arrange
        _refusal_message(
            retired_store, "DELETE FROM schema_meta WHERE key = 'store_status'"
        )
        # Act
        value = _value_of(retired_store, "store_status")
        # Assert
        assert value == STATUS_RETIRED

    def test_unrelated_keys_are_still_deletable(self, retired_store):
        # Arrange: the guard must be exactly as wide as the constraint, or it
        # becomes a reason to disable the whole mechanism.
        retired_store.execute(
            "INSERT INTO schema_meta(key, value) VALUES('scratch', 'x')"
        )
        # Act
        retired_store.execute("DELETE FROM schema_meta WHERE key = 'scratch'")
        # Assert
        assert _value_of(retired_store, "scratch") is None

    def test_a_current_store_is_not_frozen(self, guarded_store):
        # Arrange: before retirement nothing is frozen -- the guard engages only
        # once a retirement exists.
        # Act
        guarded_store.execute("DELETE FROM schema_meta WHERE key = 'store_status'")
        # Assert
        assert _value_of(guarded_store, "store_status") is None


class TestTheGuardHasThreeStates:
    def test_a_guarded_store_with_no_retirement_is_current(self):
        # Arrange
        rows = {"store_uuid": "uuid-source"}
        # Act
        status = read_status(rows, set(TRIGGER_NAMES), unguarded_store="refuse")
        # Assert
        assert status == STATUS_CURRENT

    def test_a_retired_store_raises(self):
        # Arrange
        rows = {"store_status": STATUS_RETIRED, "retired_in_favour_of": "uuid-dest"}
        # Act
        raised = pytest.raises(StoreRetired)
        # Assert
        with raised:
            read_status(rows, set(TRIGGER_NAMES), unguarded_store="refuse")

    def test_the_retired_error_carries_the_successor(self):
        # Arrange
        rows = {"store_status": STATUS_RETIRED, "retired_in_favour_of": "uuid-dest"}
        successor = None
        try:
            read_status(rows, set(TRIGGER_NAMES), unguarded_store="refuse")
        except StoreRetired as exc:
            successor = exc.successor
        # Act
        found = successor
        # Assert
        assert found == "uuid-dest"

    def test_the_retired_message_names_the_successor_for_a_human(self):
        # Arrange
        rows = {"store_status": STATUS_RETIRED, "retired_in_favour_of": "uuid-dest"}
        message = ""
        try:
            read_status(rows, set(TRIGGER_NAMES), unguarded_store="refuse")
        except StoreRetired as exc:
            message = str(exc)
        # Act
        found = message
        # Assert
        assert "uuid-dest" in found

    def test_an_unguarded_store_cannot_prove_it_is_current(self):
        # Arrange: THE THIRD STATE. 0.18.0 creates no triggers, so a store it
        # initialised has none, and absence of a retirement proves nothing there.
        rows = {"store_uuid": "uuid-source"}
        # Act
        raised = pytest.raises(StoreCannotProveItsStatus)
        # Assert
        with raised:
            read_status(rows, set(), unguarded_store="refuse")

    def test_a_partially_guarded_store_also_refuses(self):
        # Arrange: one of the two triggers present is not the guarantee.
        rows = {"store_uuid": "uuid-source"}
        # Act
        raised = pytest.raises(StoreCannotProveItsStatus)
        # Assert
        with raised:
            read_status(rows, {TRIGGER_NAMES[0]}, unguarded_store="refuse")

    def test_an_explicit_retirement_is_believed_even_without_the_guard(self):
        # Arrange: a retirement someone wrote is evidence; its ABSENCE on an
        # unguarded store is not. So retirement is checked first.
        rows = {"store_status": STATUS_RETIRED}
        # Act
        raised = pytest.raises(StoreRetired)
        # Assert
        with raised:
            read_status(rows, set(), unguarded_store="refuse")


class TestTheUnguardedEraMustBeStatedExplicitly:
    """MEASURED 2026-07-30, and it is why the permissive answer exists at all.

    The live canonical store carries six triggers and NONE of them are these.
    They install via init_schema, which runs on a WRITE open; readers open
    mode=ro and a read-only connection CANNOT create a trigger ("attempt to
    write a readonly database", verified). So on release day every reader would
    find no guard, refuse, and the board would go dark until some writer
    happened to open the store -- the guard causing the very outage it exists to
    prevent. Hence a required keyword rather than a default: each call site
    states its era, and flipping it later is a visible one-line change.
    """

    def test_an_unguarded_store_is_current_during_the_rollout(self):
        # Arrange
        rows = {"store_uuid": "uuid-source"}
        # Act
        status = read_status(rows, set(), unguarded_store=STATUS_CURRENT)
        # Assert
        assert status == STATUS_CURRENT

    def test_a_retirement_is_still_honoured_during_the_rollout(self):
        # Arrange: the permissive era must NOT make a retired store readable.
        # This is the branch the cutover depends on, and it is safe today.
        rows = {"store_status": STATUS_RETIRED, "retired_in_favour_of": "uuid-dest"}
        # Act
        raised = pytest.raises(StoreRetired)
        # Assert
        with raised:
            read_status(rows, set(), unguarded_store=STATUS_CURRENT)

    def test_the_keyword_is_required(self):
        # Arrange: no default, so nobody inherits an era they did not choose.
        rows = {"store_uuid": "uuid-source"}
        # Act
        raised = pytest.raises(TypeError)
        # Assert
        with raised:
            read_status(rows, set(TRIGGER_NAMES))

    def test_an_unrecognised_era_is_rejected_rather_than_guessed(self):
        # Arrange: a typo must not silently select the permissive branch.
        rows = {"store_uuid": "uuid-source"}
        # Act
        raised = pytest.raises(ValueError)
        # Assert
        with raised:
            read_status(rows, set(), unguarded_store="curent")


class TestTheRetireVerbHandlesTheUpsertHazard:
    """schema_meta's idiom is INSERT OR REPLACE, which SQLite implements as
    DELETE-then-INSERT, so the obvious upsert trips the delete guard on an
    already-retired store. The verb exists so nobody has to remember that.
    """

    def test_it_retires_a_current_store(self, guarded_store):
        # Arrange
        conn = guarded_store
        # Act
        retire_store(
            conn,
            successor_uuid="uuid-dest",
            by="scitex-cards",
            at="2026-07-30T15:00:00Z",
        )
        # Assert
        assert _value_of(conn, "store_status") == STATUS_RETIRED

    def test_it_records_the_successor(self, guarded_store):
        # Arrange
        conn = guarded_store
        # Act
        retire_store(
            conn,
            successor_uuid="uuid-dest",
            by="scitex-cards",
            at="2026-07-30T15:00:00Z",
        )
        # Assert
        assert _value_of(conn, "retired_in_favour_of") == "uuid-dest"

    def test_it_works_on_a_store_with_no_status_row_at_all(self, guarded_store):
        # Arrange: a store too old to carry store_status -- UPDATE alone would
        # match zero rows and silently do nothing.
        guarded_store.execute("DELETE FROM schema_meta WHERE key = 'store_status'")
        # Act
        retire_store(
            guarded_store,
            successor_uuid="uuid-dest",
            by="me",
            at="2026-07-30T15:00:00Z",
        )
        # Assert
        assert _value_of(guarded_store, "store_status") == STATUS_RETIRED

    def test_calling_it_twice_is_permitted(self, guarded_store):
        # Arrange
        retire_store(
            guarded_store,
            successor_uuid="uuid-dest",
            by="me",
            at="2026-07-30T15:00:00Z",
        )
        # Act
        retire_store(
            guarded_store,
            successor_uuid="uuid-other",
            by="you",
            at="2026-07-30T16:00:00Z",
        )
        # Assert
        assert _value_of(guarded_store, "store_status") == STATUS_RETIRED

    def test_a_second_call_does_not_rewrite_the_original_successor(self, guarded_store):
        # Arrange: the moment a store was retired is a FACT; a later call must
        # not overwrite it. That is the correct reading of one-way.
        retire_store(
            guarded_store,
            successor_uuid="uuid-dest",
            by="me",
            at="2026-07-30T15:00:00Z",
        )
        # Act
        retire_store(
            guarded_store,
            successor_uuid="uuid-other",
            by="you",
            at="2026-07-30T16:00:00Z",
        )
        # Assert
        assert _value_of(guarded_store, "retired_in_favour_of") == "uuid-dest"

    def test_a_second_call_does_not_rewrite_the_original_timestamp(self, guarded_store):
        # Arrange
        retire_store(
            guarded_store,
            successor_uuid="uuid-dest",
            by="me",
            at="2026-07-30T15:00:00Z",
        )
        # Act
        retire_store(
            guarded_store,
            successor_uuid="uuid-dest",
            by="me",
            at="2026-07-30T16:00:00Z",
        )
        # Assert
        assert _value_of(guarded_store, "retired_at") == "2026-07-30T15:00:00Z"

    def test_it_refuses_without_a_successor(self, guarded_store):
        # Arrange: retiring in favour of nothing leaves a reader nowhere to go.
        conn = guarded_store
        # Act
        raised = pytest.raises(ValueError)
        # Assert
        with raised:
            retire_store(conn, successor_uuid="", by="me", at="2026-07-30T15:00:00Z")

    def test_it_refuses_a_whitespace_successor(self, guarded_store):
        # Arrange
        conn = guarded_store
        # Act
        raised = pytest.raises(ValueError)
        # Assert
        with raised:
            retire_store(conn, successor_uuid="   ", by="me", at="2026-07-30T15:00:00Z")


class TestTheReadDoorActuallyRefusesARetiredStore:
    """The primitive existing is not the point -- something must CALL it.

    Six times today the shape was "the artifact exists, the consumer cannot see
    it". A retirement guard nothing invokes is that shape exactly, so these
    tests exercise the read door itself rather than read_status.
    """

    def test_a_retired_store_is_refused_at_the_read_door(self, bare_store):
        # Arrange
        from scitex_cards._store_canonical_read import _refuse_if_retired_on

        conn = bare_store("cards_door_retired")
        execute_ddl(conn, RETIREMENT_TRIGGER_SQL)
        retire_store(
            conn, successor_uuid="uuid-dest", by="me", at="2026-07-30T16:00:00Z"
        )
        conn.commit()
        # Act
        raised = pytest.raises(StoreRetired)
        # Assert
        with raised:
            _refuse_if_retired_on(conn)

    def test_a_current_store_is_allowed_through(self, bare_store):
        # Arrange: the regression that matters -- this path reads the live board,
        # and a guard that refuses everything is as useless as one that refuses
        # nothing.
        from scitex_cards._store_canonical_read import _refuse_if_retired_on

        conn = bare_store("cards_door_current")
        conn.execute("INSERT INTO schema_meta(key, value) VALUES('store_uuid', 'u')")
        execute_ddl(conn, RETIREMENT_TRIGGER_SQL)
        conn.commit()
        # Act
        allowed = _refuse_if_retired_on(conn)
        # Assert -- returning at all is the assertion; it must not raise
        assert allowed is None

    def test_a_store_without_schema_meta_is_not_treated_as_retired(self, bare_store):
        # Arrange: absence of the table is not a retirement, and refusing here
        # would break stores predating it for no safety gain.
        from scitex_cards._store_canonical_read import _refuse_if_retired_on

        conn = bare_store("cards_door_bare_noschema")
        conn.execute("DROP TABLE schema_meta")
        # Act
        allowed = _refuse_if_retired_on(conn)
        # Assert
        assert allowed is None

    def test_an_unguarded_store_is_allowed_during_the_rollout(self, bare_store):
        # Arrange: MEASURED -- no live store carried the guards when this was
        # written, they install on a WRITE open, and this door opens read-only.
        # Refusing here would black out every board on release day.
        from scitex_cards._store_canonical_read import _refuse_if_retired_on

        conn = bare_store("cards_door_unguarded")
        conn.execute("INSERT INTO schema_meta(key, value) VALUES('store_uuid', 'u')")
        conn.commit()
        # Act
        allowed = _refuse_if_retired_on(conn)
        # Assert
        assert allowed is None

    def test_an_unguarded_store_that_IS_retired_is_still_refused(self, bare_store):
        # Arrange: the permissive era must never make a retired store readable.
        # This is the branch the cutover depends on.
        from scitex_cards._store_canonical_read import _refuse_if_retired_on

        conn = bare_store("cards_door_unguarded_retired")
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('store_status', ?)",
            (STATUS_RETIRED,),
        )
        conn.commit()
        # Act
        raised = pytest.raises(StoreRetired)
        # Assert
        with raised:
            _refuse_if_retired_on(conn)


class TestTheTriggersAreActuallyInstalled:
    def test_both_guards_exist_after_applying_the_schema(self, guarded_store):
        # Arrange
        conn = guarded_store
        # Act
        found = trigger_names(conn)
        # Assert
        assert set(TRIGGER_NAMES).issubset(found)

    def test_applying_the_schema_twice_is_idempotent(self, guarded_store):
        # Arrange: init_schema re-applies at every open, so this runs constantly.
        conn = guarded_store
        # Act
        execute_ddl(conn, RETIREMENT_TRIGGER_SQL)
        # Assert -- still exactly the two, not four. `CREATE OR REPLACE TRIGGER`
        # is the idempotent spelling here (there is no `IF NOT EXISTS` form for a
        # trigger on this engine), so a re-apply must replace rather than add.
        assert trigger_names(conn) & set(TRIGGER_NAMES) == set(TRIGGER_NAMES)


# EOF
