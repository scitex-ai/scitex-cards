#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`health` reports when the store and the identity this process EXPECTS disagree.

The ownership guard refuses EVERY read and EVERY write when they disagree —
correctly, since treating one store's database as another's is how a board gets
destroyed. But the symptom is a total outage with no monitor, which is what this
check exists to surface.

On 2026-07-19 the MCP server resolved one store while the database was stamped
for another. Every write through the surface other agents use was refused, and
it went unnoticed because the maintainer's own writes used an explicit path.

WHAT THE DISAGREEMENT IS MADE OF CHANGED, and this file changed with it. It used
to arrange two `tasks.yaml` paths and a DB stamped for the first, because
identity was a path and a mismatch was one path against another. Identity is now
the store's UUID, compared against `$SCITEX_CARDS_EXPECTED_STORE_UUID`;
`_health_store_identity` is explicit that for a server store "the uuid is the
only identity evidence there is -- nothing weaker is being consulted underneath
this answer". So there is no path comparison left to arrange, and the old
fixture could only ever produce "the resolved store target does not name a
store".

The subject is unchanged: does the store this process resolved match the one it
was told to expect, and does the report say so usefully.

Real stores and real environment variables. No mocks and no ``monkeypatch``
(STX-NM): the env fixture sets and restores ``os.environ`` itself, so what the
code reads is the same object production reads.
"""

from __future__ import annotations

import pytest

from scitex_cards._health import health
from scitex_cards._store_uuid import ENV_EXPECTED_STORE_UUID, read_store_uuid

#: A syntactically valid identity that no store carries.
_A_DIFFERENT_IDENTITY = "00000000-0000-4000-8000-00000000dead"


def _check(report: dict, name: str) -> dict:
    return {c["name"]: c for c in report["checks"]}[name]


def _identity_of(dsn: str) -> str | None:
    from scitex_cards._db import connect

    conn = connect(dsn)
    try:
        return read_store_uuid(conn)
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def agreeing_store(new_store, env):
    """A real store, with the expectation set to the identity it carries."""
    dsn = new_store("health_identity_ok")
    identity = _identity_of(dsn)
    if not identity:
        pytest.skip("this store carries no uuid yet, so agreement cannot be posed")
    env.set(ENV_EXPECTED_STORE_UUID, identity)
    return dsn


@pytest.fixture()
def disagreeing_store(new_store, env):
    """A real store, with the expectation set to an identity it does NOT carry.

    The modern shape of the 2026-07-19 outage: the process resolved a real
    store, and the identity it was told to expect belongs to a different one.
    Every read and write is refused by the ownership guard, and this check is
    what makes that visible.
    """
    dsn = new_store("health_identity_mismatch")
    env.set(ENV_EXPECTED_STORE_UUID, _A_DIFFERENT_IDENTITY)
    return dsn


def test_matching_store_and_expectation_is_ok(agreeing_store):
    """The normal case must not raise a false alarm."""
    # Arrange
    # Act
    check = _check(health(store=agreeing_store), "store_identity")
    # Assert
    assert check["ok"] is True, check["detail"]


def test_a_mismatch_is_reported_as_a_write_outage(disagreeing_store):
    """The 2026-07-19 shape: resolved store != the identity expected."""
    # Arrange
    # Act
    check = _check(health(store=disagreeing_store), "store_identity")
    # Assert
    assert check["ok"] is False


def test_a_mismatch_is_named_as_such_in_the_detail(disagreeing_store):
    """Split from its sibling under STX-TQ007.

    "Not ok" and "ok, but says the wrong thing about WHY" are different
    defects. Merged, the detail claim only ran once the ok claim had passed —
    so a check that failed for an unrelated reason would report the ok failure
    and hide that the diagnosis was also wrong.
    """
    # Arrange
    # Act
    check = _check(health(store=disagreeing_store), "store_identity")
    # Assert
    assert "STORE IDENTITY MISMATCH" in check["detail"]


#: THE HINT TESTS BELOW WERE ONE, AND THE HISTORY IS THE REASON TO SPLIT THEM.
#: The merged assertion used to require the literal string ``db import`` — a CLI
#: verb that does not exist. The test did not merely tolerate the dead remedy, it
#: PINNED it: removing the bad advice would have broken the suite. A test can
#: hold a defect in place, and this one did, on the total-write-outage path.
#:
#: Split, each thing the hint must and must not say fails on its own line, so the
#: next person to change the hint learns exactly which clause they broke rather
#: than which assertion happened to be first.


def test_the_hint_names_the_store_pointer_the_reader_can_change(disagreeing_store):
    # Arrange
    # Act
    check = _check(health(store=disagreeing_store), "store_identity")
    # Assert
    assert "SCITEX_CARDS_DB" in check["hint"]


def test_the_hint_names_the_expectation_the_reader_can_change(disagreeing_store):
    """The OTHER pointer, and under UUID identity it is the likelier culprit.

    This assertion used to require `dev db get-path`, a verb that printed where
    the store file lived. There is no file to print now, and the hint was
    rewritten around the actual remedy: "fix the EXPECTATION, not the database".
    Pinning the old string would pin advice the check no longer gives.

    What survives is the property the old test was really after — the hint must
    name something the reader can actually change. Under a UUID mismatch there
    are two such things, and the expectation is the one to reach for first,
    because re-pointing the store is how you write one board into another.
    """
    # Arrange
    # Act
    check = _check(health(store=disagreeing_store), "store_identity")
    # Assert
    assert ENV_EXPECTED_STORE_UUID in check["hint"]


def test_the_hint_does_not_name_the_nonexistent_import_verb(disagreeing_store):
    """The negative half, and the one that was previously INVERTED.

    `db import` does not exist. Naming it strands the reader on the path where
    every write is already being refused, and re-stamping asserts the database
    belongs to a different store — the very claim the ownership guard exists to
    doubt.
    """
    # Arrange
    # Act
    check = _check(health(store=disagreeing_store), "store_identity")
    # Assert
    assert "db import" not in check["hint"]


def test_a_store_with_no_expectation_is_not_an_alarm(new_store, env):
    """Nothing to disagree with yet — a fresh install must report clean.

    With no expectation recorded the verdict is ADOPTABLE, not a mismatch. That
    has to stay quiet: every deployment starts here, and a doctor that alarms on
    the ordinary first state teaches its reader to ignore it.
    """
    # Arrange
    dsn = new_store("health_identity_unbound")
    env.delete(ENV_EXPECTED_STORE_UUID)
    # Act
    check = _check(health(store=dsn), "store_identity")
    # Assert
    assert check["ok"] is True, check["detail"]


# EOF
