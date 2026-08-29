#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Test file for: scitex_cards/_schema_ddl_objects.py

"""Tests for the #755 gate: does the DDL ``init_schema`` would run create nothing?

``schema_already_current`` (``_schema_current``) reads the STAMP and the guard
triggers; this gate reads the OBJECTS, so a store whose stamp disagrees but whose
objects are all physically present opens without re-running DDL that a non-owner
role is refused (PostgreSQL checks ownership before ``IF NOT EXISTS`` short-circuits).

Real SQLite stores built through ``open_db``, so the objects under test are the ones
the package actually creates rather than names in a fixture. The PostgreSQL-only
objects (trigger functions, the ``notifications`` sequence) cannot be exercised here
without the live store, which this suite is forbidden from reaching -- the conftest
fails loud if a test touches it -- so those branches are covered by the shared probe
logic (``has_function`` / ``has_sequence``) and the PG dialect tests, and their
gate wiring is asserted by the divergence tests below, which pin the inventory to
the DDL so a renamed or added object cannot silently desync the gate.

CONSERVATIVE DIRECTION IS THE WHOLE CONTRACT. Every test that removes an object
asserts the gate flips to "run the DDL". A gate that answered "skip" for a store
missing an object would leave it unguarded while believing it was complete -- the
shape of the failure that took this board from 2170 rows to 18.
"""

from __future__ import annotations

import pytest

from scitex_cards._db import SCHEMA_VERSION, connect, open_db
from scitex_cards._db_schema_sql import SCHEMA_TABLES
from scitex_cards._schema_current import REQUIRED_GUARD_TRIGGERS
from scitex_cards._schema_ddl_objects import _DDL_INDEXES, _all_ddl_present
from scitex_cards._schema_shape import (
    SchemaShape,
    ShapeAgreement,
    observed_version,
)


@pytest.fixture
def live_store(tmp_path):
    """A real, fully initialised store -- tables, indexes and triggers from the DDL."""
    conn = open_db(tmp_path / "cards.db")
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


def _present(conn, observed):
    """The gate under test, given a shape reporting ``observed`` rungs."""
    return _all_ddl_present(conn, _shape(observed), SCHEMA_VERSION)


# --- The direction that must hold: a complete store is a no-op ----------------


def test_a_fully_initialised_store_is_a_ddl_noop(live_store):
    """The store the ladder places at this client's version, objects all present."""
    assert _present(live_store, SCHEMA_VERSION) is True


def test_a_store_ahead_of_the_client_is_still_a_ddl_noop(live_store):
    """An older client's DDL is a subset of a newer store's; nothing it would
    create is missing, so it skips. (The direction `schema_already_current` fixed
    for the stamp; the object gate must not re-break it.)"""
    assert _present(live_store, SCHEMA_VERSION + 2) is True


def test_an_index_added_after_the_store_was_built_is_not_a_reason_to_run_ddl(live_store):
    """A newer client may have added an index this client's inventory does not know.
    The gate checks that its OWN objects are present, not that the store has no
    objects it does not recognise, so an extra index is not a reason to run DDL."""
    live_store.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_future_column ON tasks(scope, kind)"
    )
    live_store.commit()
    assert _present(live_store, SCHEMA_VERSION) is True


# --- The direction that must NOT flip: anything missing runs the DDL ----------


def test_a_store_behind_the_client_needs_the_ddl(live_store):
    """A store the ladder places below this client's version is genuinely behind;
    its DDL migrates it up and must run. The version rung is the floor."""
    assert _present(live_store, SCHEMA_VERSION - 1) is False


def test_an_unplaceable_store_needs_the_ddl_and_does_not_raise(live_store):
    """Below the ladder floor the shape is None; that must stay a refusal (run the
    DDL), and `None < int` must not escape as a TypeError."""
    shape = SchemaShape(
        observed=None,
        stamped_meta=None,
        stamped_pragma=None,
        agreement=ShapeAgreement.UNKNOWN,
    )
    assert _all_ddl_present(live_store, shape, SCHEMA_VERSION) is False


def test_a_dropped_index_defeats_an_otherwise_complete_store(live_store):
    """Indexes are the ownership-requiring DDL and the one thing the shape ladder
    never looks at. Dropping any of them is exactly the state the gate must catch:
    the ladder still places the store at SCHEMA_VERSION, so without this check the
    gate would skip the CREATE INDEX that is actually missing."""
    victim_table, victim_index = _DDL_INDEXES[0]
    live_store.execute(f"DROP INDEX IF EXISTS {victim_index}")
    live_store.commit()
    assert _present(live_store, SCHEMA_VERSION) is False


def test_a_dropped_table_defeats_an_otherwise_complete_store(tmp_path):
    """A missing table means the DDL would create it. ``inbox_recipients`` is a
    leaf table (no index, no trigger, no FK in either direction) so it can be
    dropped without cascading, which keeps the test honest about removing exactly
    one object."""
    conn = open_db(tmp_path / "cards.db")
    try:
        conn.execute("DROP TABLE IF EXISTS inbox_recipients")
        conn.commit()
        assert _present(conn, SCHEMA_VERSION) is False
    finally:
        conn.close()


def test_a_missing_guard_trigger_defeats_an_otherwise_complete_store(tmp_path):
    """The guard triggers are the retirement enforcement AND the proof-of-currency
    mechanism. They exist on both backends, so the gate checks them here on
    SQLite exactly as it will on PostgreSQL."""
    conn = open_db(tmp_path / "cards.db")
    try:
        victim = sorted(REQUIRED_GUARD_TRIGGERS)[0]
        conn.execute(f"DROP TRIGGER IF EXISTS {victim}")
        conn.commit()
        assert _present(conn, SCHEMA_VERSION) is False
    finally:
        conn.close()


def test_an_unreadable_catalogue_needs_the_ddl_and_does_not_raise(tmp_path):
    """A connection that cannot answer the catalogue at all is not a complete
    schema. It must return False (run the DDL) rather than raise out of a
    predicate whose job is to answer "is everything present?"."""
    path = tmp_path / "cards.db"
    open_db(path).close()
    conn = connect(path)
    conn.close()  # closed: every catalogue query on it now raises
    assert _present(conn, SCHEMA_VERSION) is False


# --- The gate is wired into init_schema: zero DDL in steady state -------------
#
# The live failure this exists for is a pg_proc DEADLOCK, measured 2026-08-01:
# every open re-issued CREATE OR REPLACE FUNCTION for all nine guards (the
# schema-ensure path), so N concurrent opens rewrote the same pg_proc rows and
# 11 of 12 failed DeadlockDetected. The fix is NOT to make the DDL concurrent-
# safe; it is to emit NO DDL at all in steady state, so there is nothing for two
# clients to contend on. That is what these two tests pin, by spying on the
# whole DDL block and asserting init_schema decides to run it exactly when an
# object is genuinely missing.
#
# A true concurrent test cannot live in this suite: it is forbidden to reach a
# live store, there is no test PostgreSQL, and SQLite has no pg_proc for the
# race to happen on -- a SQLite concurrency test would pass with the bug
# present. Asserting "zero DDL emitted" is the property that actually removes
# the contention, so that is what is asserted, serially.


def test_a_complete_store_under_a_refusing_stamp_gate_emits_no_ddl(tmp_path, monkeypatch):
    """The measured oscillation: the stamp disagrees, so the FIRST gate
    (``schema_already_current``) refuses the fast path, but every object the DDL
    would create is physically present. init_schema must then emit NO DDL -- in
    particular not re-issue the CREATE OR REPLACE FUNCTION that rewrites pg_proc
    on every open. Forcing the first gate to refuse isolates this gate's
    contribution: the second gate is what must carry the skip."""
    path = tmp_path / "cards.db"
    open_db(path).close()  # build a complete store with the REAL DDL

    # Now isolate the second open. The spy on _run_schema_ddl must not be live
    # during the build above, or the store would never be created; set it up only
    # for the open under test.
    calls = []
    monkeypatch.setattr(
        "scitex_cards._schema_current.schema_already_current",
        lambda *a, **k: False,  # the first gate refuses the fast path
    )
    monkeypatch.setattr(  # record the decision; do not run the real DDL
        "scitex_cards._db_init_schema._run_schema_ddl",
        lambda conn: calls.append(conn),
    )

    conn = connect(path)
    try:
        from scitex_cards._db_init_schema import init_schema

        init_schema(conn)  # second open, through the full flow
    finally:
        conn.close()
    assert calls == [], (
        "the DDL re-ran on a store whose objects are all present -- the pg_proc "
        "re-rewrite the #755 gate exists to stop"
    )


def test_a_store_missing_an_index_under_a_refusing_stamp_gate_emits_the_ddl(
    tmp_path, monkeypatch
):
    """The conservative direction in the full flow: an object that is genuinely
    missing must still run the DDL even though the first gate already refused the
    fast path. If this gate answered 'complete' here, the dropped index would
    never be created and the store would look healthy while short one object --
    the wrong direction, the one that leaves a store believing it is complete."""
    path = tmp_path / "cards.db"
    build = open_db(path)  # build a complete store with the REAL DDL
    victim_table, victim_index = _DDL_INDEXES[0]
    build.execute(f"DROP INDEX {victim_index}")
    build.commit()
    build.close()

    # Isolate the second open (the spy must not be live during the build above).
    calls = []
    monkeypatch.setattr(
        "scitex_cards._schema_current.schema_already_current",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "scitex_cards._db_init_schema._run_schema_ddl",
        lambda conn: calls.append(conn),
    )

    conn = connect(path)
    try:
        from scitex_cards._db_init_schema import init_schema

        init_schema(conn)
    finally:
        conn.close()
    assert calls, (
        "the DDL was skipped on a store missing an index -- a wrong 'no-op' that "
        "would leave the store short an object while believing it complete"
    )


# --- The inventory is pinned to the DDL (drift cannot desync the gate) --------


class TestTheInventoryMatchesTheDdl:
    """These are the safety net for the hand-written inventory. A renamed or added
    object that the DDL creates but the gate does not list would let the gate read
    a store complete and skip the CREATE that is actually missing. Asserting the
    inventory against a freshly-built store makes that a test failure, not a
    silent divergence."""

    def test_the_index_inventory_is_exactly_the_ddl_indexes(self, live_store):
        """Every ``idx_*`` index a fresh store carries, and only those, by (table,
        name). Auto-indexes (``sqlite_*``) are the engine's, not the DDL's."""
        rows = live_store.execute(
            "SELECT name, tbl_name FROM sqlite_master "
            "WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        actual = {(row[1], row[0]) for row in rows}
        assert actual == set(_DDL_INDEXES)

    def test_every_table_the_gate_checks_is_one_the_schema_creates(self, live_store):
        """SCHEMA_TABLES is the gate's table list; every one of them must exist on a
        fresh store, or the gate would run DDL on a healthy store forever."""
        for table in SCHEMA_TABLES:
            present = (
                live_store.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()
                is not None
            )
            assert present, f"gate lists table {table!r} that the DDL does not create"

    def test_every_guard_trigger_the_gate_checks_is_installed(self, live_store):
        """The gate requires these triggers on both backends; a fresh store must
        carry all of them, or the gate would never report a store complete."""
        for trigger in REQUIRED_GUARD_TRIGGERS:
            present = (
                live_store.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                    (trigger,),
                ).fetchone()
                is not None
            )
            assert present, f"gate requires guard trigger {trigger!r} the DDL lacks"

    def test_a_reopened_store_is_read_as_a_noop(self, tmp_path):
        """The observable end state: a second open of an existing store, which is
        where ~90 containers hit the gate, reads it as a DDL no-op (a READ, not a
        re-assert). This is the integration proof that the ladder and the object
        gate agree a completed store needs nothing."""
        path = tmp_path / "cards.db"
        open_db(path).close()
        conn = connect(path)
        try:
            assert _all_ddl_present(conn, observed_version(conn), SCHEMA_VERSION) is True
        finally:
            conn.close()


# EOF
