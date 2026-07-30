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

import sqlite3

import pytest

from scitex_cards._store_retirement import (
    RETIREMENT_TRIGGER_SQL,
    STATUS_CURRENT,
    STATUS_RETIRED,
    TRIGGER_NAMES,
    StoreCannotProveItsStatus,
    StoreRetired,
    read_status,
)


@pytest.fixture
def guarded_store(tmp_path):
    """A throwaway store with schema_meta and the retirement guards installed."""
    conn = sqlite3.connect(tmp_path / "cards.db")
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.executemany(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?)",
        [("store_uuid", "uuid-source"), ("store_status", STATUS_CURRENT)],
    )
    conn.executescript(RETIREMENT_TRIGGER_SQL)
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
    """
    try:
        conn.execute(sql, params)
        return ""
    except sqlite3.IntegrityError as exc:
        return str(exc)


def _value_of(conn, key):
    row = conn.execute("SELECT value FROM schema_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


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


class TestTheTriggersAreActuallyInstalled:
    def test_both_guards_exist_after_applying_the_schema(self, guarded_store):
        # Arrange
        conn = guarded_store
        # Act
        found = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        # Assert
        assert set(TRIGGER_NAMES).issubset(found)

    def test_applying_the_schema_twice_is_idempotent(self, guarded_store):
        # Arrange: init_schema re-applies at every open, so this runs constantly.
        conn = guarded_store
        # Act
        conn.executescript(RETIREMENT_TRIGGER_SQL)
        # Assert
        n = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN (?, ?)",
            TRIGGER_NAMES,
        ).fetchone()[0]
        assert n == 2


# EOF
