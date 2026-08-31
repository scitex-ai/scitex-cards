#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Test file for: scitex_cards/_schema_current.py

"""Tests for the gate that decides whether a client must re-assert the schema.

There was no test module for this file, which is part of why the direction bug
survived: the gate was written to answer "is the store current?" and was tested
only through the path where the client and the store agree exactly. The
interesting cases are the two where they do not.

Real stores built through ``open_db``, so the guard triggers under test
are the ones the package actually creates rather than names in a fixture.
A fixture that declares the triggers would pass even if the DDL stopped
creating them.
"""

from __future__ import annotations

import pytest

from scitex_cards._db import SCHEMA_VERSION, connect, open_db
from scitex_cards._schema_current import (
    REQUIRED_GUARD_TRIGGERS,
    schema_already_current,
)
from scitex_cards._schema_shape import SchemaShape, ShapeAgreement


@pytest.fixture
def live_store(tmp_path, new_store):
    """A real, fully initialised store — triggers created by the real DDL."""
    path = new_store()
    conn = open_db(path)
    try:
        yield conn
    finally:
        conn.close()


def _shape(observed):
    """A self-consistent shape reporting ``observed`` physical rungs."""
    return SchemaShape(
        observed=observed,
        stamped_meta=observed,
        stamped_pragma=observed,
        agreement=ShapeAgreement.AGREES,
    )


def test_a_store_at_the_clients_own_version_needs_no_ddl(live_store):
    # Arrange
    shape = _shape(SCHEMA_VERSION)
    # Act
    current = schema_already_current(live_store, shape, SCHEMA_VERSION)
    # Assert
    assert current is True


def test_a_store_AHEAD_of_the_client_needs_no_ddl(live_store):
    # Arrange
    # THE FIX. This returned False until 2026-08-02 because the check was `!=`,
    # so a client older than the store re-ran its whole DDL on every connection
    # — taking ShareRowExclusiveLock on pg_proc each time and deadlocking
    # unrelated writers. A v7 client cannot add anything a v9 store lacks.
    #
    # THIS SHAPE IS NOT ONE A REAL BEHIND-CLIENT CAN PRODUCE, which is why the
    # bug below survived this test for four weeks. `_shape` builds a SELF-
    # CONSISTENT reading — observed and both stamps equal, agreement AGREES — but
    # a client's physical-rung reader only knows the rungs its own version
    # defines, so it can never observe a version above its own. What it actually
    # reports is observed == its own version with the stamps higher, i.e.
    # STAMP_IS_HIGH. This test therefore exercises the version comparison and
    # nothing else. `test_the_shape_a_real_behind_client_reports_needs_no_ddl`
    # is the one that covers the real reading; keep both, because they fail for
    # different reasons.
    shape = _shape(SCHEMA_VERSION + 2)
    # Act
    current = schema_already_current(live_store, shape, SCHEMA_VERSION)
    # Assert
    assert current is True


def _behind_shape(client_version, stamped):
    """What a client BEHIND the store actually reads.

    Its rung reader is capped at what its own version defines, so `observed` is
    the client's version however far ahead the store is; the stamps come off the
    store and are higher. That combination is STAMP_IS_HIGH.
    """
    return SchemaShape(
        observed=client_version,
        stamped_meta=stamped,
        stamped_pragma=stamped,
        agreement=ShapeAgreement.STAMP_IS_HIGH,
    )


def test_the_shape_a_real_behind_client_reports_needs_no_ddl(live_store):
    # Arrange
    # THE ACTUAL FLEET FAILURE, measured on the live board 2026-08-31: a
    # deployed client at SCHEMA_VERSION 12 against a store stamped 13 read
    # observed 12, all nine guard triggers present, and STAMP_IS_HIGH — and so
    # re-ran the whole DDL on EVERY connection, taking ShareRowExclusiveLock on
    # pg_proc each time and deadlocking the operator's own card writes three
    # times in under twenty minutes. Its DDL knows no rung the store lacks, so
    # the work was pure contention.
    shape = _behind_shape(SCHEMA_VERSION, SCHEMA_VERSION + 1)
    # Act
    current = schema_already_current(live_store, shape, SCHEMA_VERSION)
    # Assert
    assert current is True


def test_a_current_client_seeing_a_high_stamp_still_needs_the_ddl(live_store):
    # Arrange
    # The boundary that keeps the exemption honest. This client can read every
    # rung it would assert, so a stamp above it is NOT explained by the client
    # being old — it is unexplained, which is the anomaly the fast path must
    # never swallow. Same agreement as the test above; opposite answer.
    shape = _behind_shape(SCHEMA_VERSION, SCHEMA_VERSION)
    # Act
    current = schema_already_current(live_store, shape, SCHEMA_VERSION)
    # Assert
    assert current is False


def test_stamps_that_disagree_with_each_other_still_need_the_ddl(live_store):
    # Arrange
    # Only ONE stamp is above this client, so the store is not provably ahead
    # and the exemption must not apply. This is why the helper takes `min` of
    # the stamps rather than `max` — a detail no other test in this file pins.
    shape = SchemaShape(
        observed=SCHEMA_VERSION,
        stamped_meta=SCHEMA_VERSION + 1,
        stamped_pragma=SCHEMA_VERSION,
        agreement=ShapeAgreement.STAMP_IS_HIGH,
    )
    # Act
    current = schema_already_current(live_store, shape, SCHEMA_VERSION)
    # Assert
    assert current is False


def test_a_behind_client_still_needs_a_guarded_store(tmp_path):
    # Arrange
    # The exemption must not become a way to skip the guard-trigger proof. A
    # behind-client whose store is missing a guard runs the DDL like anyone
    # else — dropping a real trigger rather than fabricating a list, so this
    # measures the DDL's own output.
    from scitex_cards._db import open_db as _open_db

    path = tmp_path / "cards.db"
    conn = _open_db(path)
    victim = sorted(REQUIRED_GUARD_TRIGGERS)[0]
    conn.execute(f"DROP TRIGGER IF EXISTS {victim}")
    conn.commit()
    shape = _behind_shape(SCHEMA_VERSION, SCHEMA_VERSION + 1)
    # Act
    current = schema_already_current(conn, shape, SCHEMA_VERSION)
    conn.close()
    # Assert
    assert current is False


def test_a_store_BEHIND_the_client_still_needs_the_ddl(live_store):
    # Arrange
    # The direction that must NOT change: this is what migrations are for.
    shape = _shape(SCHEMA_VERSION - 1)
    # Act
    current = schema_already_current(live_store, shape, SCHEMA_VERSION)
    # Assert
    assert current is False


def test_an_unplaceable_store_is_not_treated_as_current(live_store):
    # Arrange
    # Below the ladder floor the shape reader cannot place the store at all.
    # `None` must stay a refusal rather than becoming a comparison — unknown is
    # not "current", and it is the conservative branch.
    shape = SchemaShape(
        observed=None,
        stamped_meta=None,
        stamped_pragma=None,
        agreement=ShapeAgreement.UNKNOWN,
    )
    # Act
    current = schema_already_current(live_store, shape, SCHEMA_VERSION)
    # Assert
    assert current is False


def test_an_unplaceable_store_does_not_raise(live_store):
    # Arrange
    # `None < int` is a TypeError, so the null case has to be handled before the
    # comparison rather than by it.
    shape = SchemaShape(
        observed=None,
        stamped_meta=None,
        stamped_pragma=None,
        agreement=ShapeAgreement.UNKNOWN,
    )
    raised = None
    # Act
    try:
        schema_already_current(live_store, shape, SCHEMA_VERSION)
    except TypeError as exc:
        raised = exc
    # Assert
    assert raised is None


def test_a_store_whose_stamp_disagrees_with_its_rungs_needs_the_ddl(live_store):
    # Arrange
    # A disagreement is precisely the state the migration chain exists to
    # repair, so it must never take the fast path however current it looks.
    shape = SchemaShape(
        observed=SCHEMA_VERSION,
        stamped_meta=SCHEMA_VERSION - 1,
        stamped_pragma=SCHEMA_VERSION,
        agreement=ShapeAgreement.STAMP_IS_LOW,
    )
    # Act
    current = schema_already_current(live_store, shape, SCHEMA_VERSION)
    # Assert
    assert current is False


def test_a_missing_guard_trigger_defeats_an_otherwise_current_store(tmp_path, new_store):
    # Arrange
    # The guard triggers are not decoration: they are the retirement
    # enforcement AND the proof-of-currency mechanism. Skipping the DDL without
    # confirming they exist would leave a store unguarded while believing it
    # guarded. Dropping one is the honest way to test that — asserting on a
    # fabricated trigger list would only prove the fixture.
    path = new_store()
    conn = open_db(path)
    victim = sorted(REQUIRED_GUARD_TRIGGERS)[0]
    # NAMED WITH ITS TABLE, because PostgreSQL's DROP TRIGGER requires one.
    # `DROP TRIGGER IF EXISTS <name>` is the retired engine's spelling -- trigger names are
    # global there -- and against a server it is a syntax error at end of input,
    # so this arrange step failed before the act ever ran.
    #
    # The table is READ FROM THE CATALOGUE rather than written down beside the
    # name. A hardcoded pairing is a second list to keep in step with
    # REQUIRED_GUARD_TRIGGERS, and the one that drifts is the one that stops
    # dropping anything -- which would leave this test asserting False about a
    # store whose trigger is still present, i.e. passing for the wrong reason.
    owning_table = conn.execute(
        "SELECT event_object_table AS t FROM information_schema.triggers "
        "WHERE trigger_schema = current_schema() AND trigger_name = ?",
        (victim,),
    ).fetchone()["t"]
    conn.execute(f"DROP TRIGGER IF EXISTS {victim} ON {owning_table}")
    conn.commit()
    shape = _shape(SCHEMA_VERSION)
    # Act
    current = schema_already_current(conn, shape, SCHEMA_VERSION)
    conn.close()
    # Assert
    assert current is False


def test_an_unreadable_catalogue_is_not_a_current_schema(tmp_path, new_store):
    # Arrange
    # A connection that cannot answer the catalogue query at all.
    path = new_store()
    open_db(path).close()
    conn = connect(path)
    conn.close()  # closed: every query on it now raises
    shape = _shape(SCHEMA_VERSION)
    # Act
    current = schema_already_current(conn, shape, SCHEMA_VERSION)
    # Assert
    assert current is False


# EOF
