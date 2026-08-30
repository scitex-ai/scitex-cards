#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STORE IDENTITY IS A UUID -- the contract against a REAL database.

Design: ``docs/design/store-identity-is-a-uuid.md``.
Card: ``scitex-cards-resolver-never-default-yaml-20260727`` (P0).
Companion: ``test__store_uuid_identity_contract.py`` (the pure decision table).
Split from it only because one file hit the 512-line cap; the ``@NOT_YET``
convention is identical and is explained there.

WHERE THE IDENTITY IS CONSULTED. ``_dual_write._db_mirrors_this_store`` becomes
uuid-first: a stamped identity decides, and THE PATH IS NOT CONSULTED AT ALL on
that branch. Both doors keep calling the SAME predicate -- the read door
(``_store._read_canonical_db_or_raise``) and the write door
(``_store_backend.write_doc_to_db``) are not split and do not get a lenient
variant. On 2026-07-19 the write door refused a foreign store correctly all day
while the read door returned its rows, and a packaged fixture was read AS THE
BOARD for hours. That asymmetry is what these tests exist to keep unbuildable.

TESTS MUST NOT MEASURE THE MACHINE. A test written on 2026-07-28 stamped the
real ``/home/agent/...`` path to exercise the unresolvable branch, and PASSED
VACUOUSLY inside the container where that path exists -- green here, red on the
host, the same environment-coupling that let the bug reach the operator. So a
path that must not resolve is spelled ``/proc/self/no-such-mount-namespace/...``
(which cannot exist in any environment), and the strongest test here does not
depend on resolvability at all: it stamps a path that IS resolvable and IS
genuinely different alongside a MATCHING identity, and asserts the identity
wins. That cannot pass vacuously under today's code, nor under PR #598.

EVERY TEST USES AN EXPLICIT TMP STORE. Never the default -- see ``store_db``.
"""

from __future__ import annotations

import pytest

# THE ``@NOT_YET`` MARKERS ARE GONE — see the companion file. ``strict`` meant
# an XPASS failed the suite, so landing the implementation FORCED their removal
# rather than allowing a marker to sit behind as a silently-skipped promise.

#: The same two fixed identities the companion file uses.
IDENTITY_A = "3f2b8c1e-9d4a-4f77-b0c5-1a2e3d4f5a6b"
IDENTITY_B = "7c9e0d21-5b3f-4a08-9e6d-2f4a6b8c0d1e"


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #
@pytest.fixture
def store_db(new_store, env):
    """An EXPLICIT throwaway store, pinned as ``$SCITEX_CARDS_DB`` for the test.

    NEVER the ambient default. That default is the live fleet board (2646 cards
    on 2026-07-28), and ``_store._read_canonical_db_or_raise`` resolves
    ``$SCITEX_CARDS_DB`` regardless of any ``store`` argument a caller passes --
    so a test that forgets this pin does not merely read the operator's board,
    it can rewrite it. Pinning here rather than per-test makes forgetting hard.
    """
    from scitex_cards._db import ENV_DB

    db = new_store("cards_uuid_guard", bootstrap=False)
    _seed(db)
    env.set(ENV_DB, db)
    return db


def _seed(db_path) -> None:
    """Build a real schema with one card, via the suite's seeding primitive."""
    from conftest import seed_db_from_doc

    seed_db_from_doc(
        {
            "tasks": [
                {
                    "id": "seeded",
                    "title": "seeded",
                    "status": "deferred",
                    "assignee": "agent:test-suite",
                }
            ]
        },
        db_path,
    )


def _bind(db_path, identity: str) -> None:
    """Stamp ``identity`` into ``schema_meta`` -- the migration step, by hand."""
    from scitex_cards._db import connect
    from scitex_cards._store_uuid import stamp_store_uuid

    conn = connect(db_path)
    try:
        # NO `BEGIN IMMEDIATE`: only one engine ever understood it, and it is a
        # hard syntax error here. The driver opens a transaction anyway.
        stamp_store_uuid(conn, identity)
        conn.commit()
    finally:
        conn.close()


def _stamp_path(db_path, store_path) -> None:
    """Stamp the legacy path provenance -- what the identity is REPLACING."""
    from scitex_cards._db import connect
    from scitex_cards._db_freshness import stamp_store_provenance

    conn = connect(db_path)
    try:
        stamp_store_provenance(conn, store_path)
        conn.commit()
    finally:
        conn.close()


def _card_ids(db_path) -> set[str]:
    from scitex_cards._db import connect

    conn = connect(db_path)
    try:
        return {
            str(r["id"]) for r in conn.execute("SELECT id FROM tasks").fetchall()
        }
    finally:
        conn.close()


def _detail(report: dict, check_name: str) -> str:
    """The ``detail`` string of one named health check."""
    for check in report.get("checks", []):
        if check.get("name") == check_name:
            return str(check.get("detail", ""))
    return ""


# --------------------------------------------------------------------------- #
# The guard                                                                   #
# --------------------------------------------------------------------------- #


def test_the_identity_decides_even_when_the_stamped_path_contradicts_it(
    store_db, tmp_path, env
):
    """The strongest test here: the identity wins, the path is never read.

    The database is stamped for a path that RESOLVES and is GENUINELY DIFFERENT,
    and carries an identity that MATCHES the caller's expectation. Today that is
    refused (the path says "different store"). Under this design it is accepted,
    because a stamped identity short-circuits before the path is consulted.

    Deliberately NOT built on an unresolvable path. An unresolvable-path test
    passes for the wrong reason under PR #598 (which makes "cannot tell" mean
    "proceed"), and passes vacuously in any environment where the path happens
    to exist -- which is exactly how a test of this branch went green in the
    container and red on the host. This one cannot pass by either route.
    """
    # Arrange
    from scitex_cards._dual_write import _db_mirrors_this_store
    from scitex_cards._store_uuid import ENV_EXPECTED_STORE_UUID

    someone_elses = tmp_path / "someone-elses.db"
    someone_elses.write_bytes(b"")
    # A path stamp NAMING SOMETHING ELSE. It is still written as a path because
    # that is what the legacy `store_path` row holds; what the test needs from
    # it is only that it is genuinely not this store, and a resolvable file on
    # disk is the least ambiguous way to be that.
    _stamp_path(store_db, someone_elses)
    _bind(store_db, IDENTITY_A)
    env.set(ENV_EXPECTED_STORE_UUID, IDENTITY_A)

    # Act
    mirrors = _db_mirrors_this_store(store_db, store_db)

    # Assert
    assert mirrors


def test_a_database_carrying_another_stores_identity_is_refused(
    store_db, tmp_path, env
):
    """The guard still guards, at the level that replaced the path compare.

    The mirror image of the test above: the path stamp agrees (it is this very
    database) and the IDENTITY disagrees. Refusing here is what stops a write
    from replacing another board's rows -- the 2026-07-19 shape, which needs
    only a mispaired destination and no malice at all.
    """
    # Arrange
    from scitex_cards._dual_write import _db_mirrors_this_store
    from scitex_cards._store_uuid import ENV_EXPECTED_STORE_UUID

    _stamp_path(store_db, store_db)
    _bind(store_db, IDENTITY_A)
    env.set(ENV_EXPECTED_STORE_UUID, IDENTITY_B)

    # Act
    mirrors = _db_mirrors_this_store(store_db, store_db)

    # Assert
    assert not mirrors


def test_an_unresolvable_path_stamp_no_longer_decides_anything(store_db, env):
    """The realpath string fallback is REMOVED, so this path answers nothing.

    scitex-dev's framing, endorsed: "a fallback that triggers only in the case
    it cannot judge is worse than no fallback." It fires precisely when the
    stamped path is unstat-able -- i.e. exactly when you are across a boundary,
    which is when it is least entitled to an opinion.

    The stamped path here CANNOT EXIST IN ANY ENVIRONMENT, so this test measures
    the code and not the machine it runs on. With a matching identity present,
    the unresolvable path is simply never consulted.
    """
    # Arrange
    from scitex_cards._dual_write import _db_mirrors_this_store
    from scitex_cards._store_uuid import ENV_EXPECTED_STORE_UUID

    _stamp_path(store_db, "/proc/self/no-such-mount-namespace/cards.db")
    _bind(store_db, IDENTITY_A)
    env.set(ENV_EXPECTED_STORE_UUID, IDENTITY_A)

    # Act
    mirrors = _db_mirrors_this_store(store_db, store_db)

    # Assert
    assert mirrors


# --------------------------------------------------------------------------- #
# IDENTITY AND RESOLUTION ARE TWO SEPARATE RULES                              #
# --------------------------------------------------------------------------- #
# NOT xfail. This one passes TODAY and must keep passing: it is the regression
# lock on scitex-dev's first required addition. It spells the env var by literal
# name rather than importing the constant, precisely so it does not depend on
# the unimplemented module and stays able to fail loudly if the implementation
# ever wires the identity into the resolution guard.


def test_a_matching_identity_does_not_bypass_the_ambient_store_creation_guard(
    tmp_path, env
):
    """An expectation says WHICH store. It never says "create one here".

    Contract rule 4b ("no expectation is not evidence of a foreign store") is
    correct for IDENTITY and must NEVER merge with RESOLUTION, or it silently
    becomes "use whatever you were pointed at". scitex-dev hit this on
    2026-07-28: their ``add_task`` resolved to an ambient default and tried to
    manufacture a board, and this guard refused it correctly.

    So an identity in the environment must not make an ambient, unnamed,
    nonexistent store creatable -- no matter how confidently it is spelled.
    """
    # Arrange
    from scitex_cards._db import ENV_DB
    from scitex_cards._paths import refuse_ambient_store_creation

    env.delete(ENV_DB)
    env.set("SCITEX_CARDS_STORE_UUID", IDENTITY_A)
    nobody_named_this = tmp_path / "ambient" / "cards.db"

    # Act
    # Assert
    with pytest.raises(RuntimeError):
        refuse_ambient_store_creation(nobody_named_this)


# --------------------------------------------------------------------------- #
# What this design CANNOT decide, stated honestly                             #
# --------------------------------------------------------------------------- #


def test_a_copy_carries_the_same_identity_and_cannot_self_distinguish(
    store_db, new_store
):
    """The residual, pinned as a LIMITATION rather than papered over.

    A COPY IS STILL A COPY, and the engine change did not close this. It was
    ``cp cards.db elsewhere.db``; it is now a second store carrying the same
    ``schema_meta.store_uuid`` -- which is what a restore from a dump, a
    replica promoted by hand, or a schema duplicated for a migration rehearsal
    all produce. The store cannot self-distinguish, because everything it could
    distinguish itself by is inside the thing that was copied. Both are ACCEPTed.

    The copy is made here by carrying the IDENTITY ROW across rather than the
    bytes, and that is the same fact stated in the terms this engine has: the
    identity is the only thing the guard reads, so copying it is copying
    everything that matters to the guard.

    This test asserts the copy is INDISTINGUISHABLE on purpose. Someone will
    eventually try to close this gap by mixing a path, an inode or an endpoint
    back into the identity -- which would reintroduce exactly the
    view-dependence this change removes, and this test would go red the moment
    they did.

    The real close is an INJECTED expectation pairing the identity with
    something OUTSIDE the file: scitex-dev's host registry, carrying
    ``(expected_store_uuid, endpoint)`` per service. Even that detects "wrong
    endpoint", never "this copy is the stale one". This package's half is
    exposing the identity as a first-class read so that registry can be
    populated without archaeology.
    """
    # Arrange
    from scitex_cards._db import connect
    from scitex_cards._store_uuid import read_store_uuid

    _bind(store_db, IDENTITY_A)
    copy = new_store("cards_uuid_copy", bootstrap=False)
    _seed(copy)
    _bind(copy, IDENTITY_A)

    # Act
    conn = connect(copy)
    try:
        copied = read_store_uuid(conn)
    finally:
        conn.close()

    # Assert
    assert copied == IDENTITY_A


# --------------------------------------------------------------------------- #
# Migration safety, and exposure (contract point 8)                           #
# --------------------------------------------------------------------------- #


def test_binding_an_identity_leaves_every_card_row_untouched(store_db):
    """The one-time migration step must be boring, and provably so.

    Re-stamping ``store_path`` was repair attempt 3 on 2026-07-28: the 500
    cleared and the board came back EMPTY, which is the 2,138-card-wipe shape (a
    failed read promoted to an authoritative empty document that a
    read-modify-write writes back). Binding an identity writes ONE
    ``schema_meta`` row: it must not touch ``tasks``, must not touch
    ``store_path``, and must not change what any resolver resolves.
    """
    # Arrange
    before = _card_ids(store_db)

    # Act
    _bind(store_db, IDENTITY_A)

    # Assert
    assert _card_ids(store_db) == before


def test_resolve_store_reports_the_databases_identity(store_db):
    """Contract point 8, machine-readable half.

    The registry's expected-uuid field has to be populated from somewhere, and
    "open the database and run a SQL query" is archaeology. ``resolve_store``
    already answers "which store did I actually resolve"; it should answer "and
    what is it" in the same breath.
    """
    # Arrange
    from scitex_cards._store import resolve_store

    _bind(store_db, IDENTITY_A)

    # Act
    resolved = resolve_store(store_db)

    # Assert
    assert resolved.get("store_uuid") == IDENTITY_A


def test_health_names_the_databases_identity(store_db):
    """Contract point 8, human-facing half.

    ``_run_check`` coerces every check to ``{name, ok, detail, hint}``, so the
    identity has to be IN the detail string to reach the doctor output. On
    2026-07-19 ``store_canonical`` reported ok while every write was being
    refused for an identity mismatch; a doctor that cannot name the identity
    cannot diagnose that.
    """
    # Arrange
    from scitex_cards._health import health

    _bind(store_db, IDENTITY_A)

    # Act
    report = health(store=store_db)

    # Assert
    assert IDENTITY_A in _detail(report, "store_identity")


# EOF
