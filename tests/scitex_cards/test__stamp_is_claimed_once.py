#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE STAMP IS CLAIMED ONCE — it must not flip to the current writer's name.

Sibling of ``test__store_identity.py``, which asks "given two path strings, do
they name the same store?". This file asks the question one layer up, on the
WRITE side: given that they do, WHICH NAME does the database keep?

THE DEFECT, measured live 2026-07-28/29. ``stamp_store_provenance`` wrote
``INSERT ... ON CONFLICT DO UPDATE`` unconditionally and called itself
"idempotent — a re-stamp with the same store is a no-op". That holds only for a
store with ONE name. This one has three, all of them one inode
(dev/ino 2096/3417791)::

    /home/agent/.scitex/cards/cards.db                    container's name
                                                          (the host CANNOT stat it)
    /home/ywatanabe/.scitex/cards/cards.db                the host's name
    /home/ywatanabe/.dotfiles/src/.scitex/cards/cards.db  its realpath

So the "no-op" was a FLIP. The operator's board was repaired by stamping a
HOST-VISIBLE name, which let the ownership guard's inode branch run at all:
``GET /tasks`` went 500 -> 200 serving 2,684 cards. The next CONTAINER-side
card write re-stamped ``/home/agent/...``; from the host that name cannot be
stat'd, ``_same_file`` fell back to a realpath STRING compare that can never
match, and the board went back to 500. The repair lost a race to the next
write, and every container write wins it.

THE RULE PINNED HERE. An unstamped database is CLAIMED by the first stamp. An
already-stamped database whose stamp names THE SAME FILE is left exactly as it
is, however the current writer would spell that file. A stamp naming a
different file is the ownership guard's business, not the stamper's — and the
last test here is the control proving that guard is untouched.

WHY A HARD LINK AND NOT A SYMLINK. These tests need two real names for one
file. A symlink would NOT reproduce the defect: ``realpath`` collapses a
symlink, so both the inode branch AND the string fallback of ``_same_file``
agree, and a test built on one passes even against the broken unconditional
stamp. A hard link is the production shape exactly — one inode, two directory
entries, NEITHER a symlink, so ``realpath`` cannot collapse them and the
strings genuinely differ. That makes the INODE branch the branch that decides,
which is the branch the live bind mount exercises inside the container (where
both names are stat-able and the flip happened). Each test asserts the
differing-realpath precondition, so a future change that made the two names
collapse would surface as a failed precondition rather than a silent pass.

MITIGATION, NOT FIX. Path identity cannot be made correct across namespaces,
only stable; the real repair is ``store_uuid`` (design PR #601,
``docs/design/store-identity-is-a-uuid.md``, card
``scitex-cards-resolver-never-default-yaml-20260727``).
"""

from __future__ import annotations

import os

import pytest

from scitex_cards import _store_backend
from scitex_cards._db import ENV_DB, connect
from scitex_cards._db_freshness import (
    canonical_path,
    stamp_store_provenance,
    stamped_store_path,
)

_EMPTY = {"tasks": []}


def _card(n: int) -> dict:
    return {
        "id": f"c{n}",
        "title": f"card {n}",
        "status": "deferred",
        "assignee": "agent:test-suite",
    }


def _stamp(db_path, store_path) -> None:
    """Stamp ``db_path``'s database as belonging to the store named ``store_path``."""
    conn = connect(str(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        stamp_store_provenance(conn, store_path)
        conn.commit()
    finally:
        conn.close()


def _stamp_of(db_path) -> str | None:
    conn = connect(str(db_path))
    try:
        return stamped_store_path(conn)
    finally:
        conn.close()


def _one_file_two_names(tmp_path):
    """ONE database file, two real names. Returns ``(opened_as, other_name)``.

    ``opened_as`` is the ONLY name any connection is opened through — the other
    is a pure NAME, passed as ``store_path``. That is deliberate: the databases
    here run in WAL mode, whose sidecar file is derived from the path a
    connection was opened with, and in production the two names are one
    DIRECTORY reached through a bind mount, so their WAL sidecars are one file
    too. Opening one inode through two basenames in ``tmp_path`` would give it
    two different WAL sidecars — an artefact of the fixture, not of the bug.
    """
    opened_as = tmp_path / "as-container.db"
    from conftest import seed_db_from_doc

    seed_db_from_doc(_EMPTY, str(opened_as))
    other_name = tmp_path / "as-host.db"
    os.link(opened_as, other_name)
    return opened_as, other_name


def test_an_unstamped_database_is_claimed_by_the_first_stamp(tmp_path):
    """The claiming path is untouched: nothing has an identity until something writes.

    This is the branch every fresh database and every test fixture takes, and
    the branch an already-populated board took when it was adopted at deploy.
    Skipping the write when a stamp is already present must not become skipping
    the write when there is nothing there yet.
    """
    # Arrange
    db = tmp_path / "fresh.db"
    from conftest import seed_db_from_doc

    seed_db_from_doc(_EMPTY, str(db))
    assert _stamp_of(db) is None, "precondition: a seeded database carries no claim"

    # Act
    _stamp(db, db)

    # Assert
    assert _stamp_of(db) == canonical_path(db), (
        "an unstamped database must be claimed by the first stamp — without "
        "this branch no database ever acquires an identity to guard"
    )


def test_a_stamp_naming_the_same_file_by_another_name_is_not_rewritten(tmp_path):
    """THE REGRESSION. One file, two names: the name claimed FIRST is the name kept.

    The stamp answers "which store is this the database of", not "who wrote
    last". Both names are equally true and each is legible only inside the
    namespace that produced it, so overwriting destroys information for the
    other namespace and adds none — while the FIRST name is at least stable,
    which is the only property the ownership guard can actually use.
    """
    # Arrange — the database is claimed under the OTHER namespace's name,
    # the state the live board was repaired INTO on 2026-07-28.
    opened_as, other_name = _one_file_two_names(tmp_path)
    assert os.path.realpath(opened_as) != os.path.realpath(other_name), (
        "precondition: two DIFFERENT realpaths for ONE inode, else this test "
        "is not reproducing the bug"
    )
    _stamp(opened_as, other_name)

    # Act — THIS namespace stamps the very same file under ITS OWN name.
    _stamp(opened_as, opened_as)

    # Assert
    assert _stamp_of(opened_as) == canonical_path(other_name), (
        "the stamp was FLIPPED to this writer's spelling of the same file — "
        "that is the 500 -> 200 -> 500 race the live board lost on 2026-07-28"
    )


def test_the_claimed_name_survives_successive_writes_through_the_real_write_door(
    tmp_path, monkeypatch
):
    """Stability, not a single lucky call — and through the door production uses.

    ``write_doc_to_db`` stamps with ``resolve_db_path(None)``, i.e. THIS
    process's ``$SCITEX_CARDS_DB`` spelling, on EVERY write. One repair therefore
    survived exactly until the next card write. Five writes here: any per-write
    rewrite shows up on the first of them, and a rule that only holds for one
    call is not stability.
    """
    # Arrange — claimed under the other namespace's name; this process writes
    # through its own.
    opened_as, other_name = _one_file_two_names(tmp_path)
    assert os.path.realpath(opened_as) != os.path.realpath(other_name), (
        "precondition: two DIFFERENT realpaths for ONE inode"
    )
    _stamp(opened_as, other_name)
    monkeypatch.setenv(ENV_DB, str(opened_as))

    # Act — five ordinary, growing card writes (a shrink is refused by a
    # different guard, and is not what this test is about).
    for n in range(1, 6):
        _store_backend.write_doc_to_db(
            {"tasks": [_card(i) for i in range(1, n + 1)]}, opened_as
        )

    # Assert
    assert _stamp_of(opened_as) == canonical_path(other_name), (
        "the claimed name must survive EVERY subsequent write, not just be "
        "restorable between them"
    )


def test_a_genuinely_different_store_is_still_refused_at_the_write_door(
    tmp_path, monkeypatch
):
    """The control: leaving stamps alone must not open the door it was guarding.

    Without this, "never rewrite an existing stamp" could be satisfied by a
    stamper that shrugs at a FOREIGN stamp too — and the guard whose entire job
    is to refuse a write that would REPLACE another store's rows would be dead
    while every test above still passed.
    """
    # Arrange — two separate files, not two names for one; the database is
    # stamped for the foreign one.
    db = tmp_path / "mine.db"
    from conftest import seed_db_from_doc

    seed_db_from_doc(_EMPTY, str(db))
    foreign = tmp_path / "theirs.db"
    seed_db_from_doc(_EMPTY, str(foreign))
    assert db.stat().st_ino != foreign.stat().st_ino, (
        "precondition: genuinely two files"
    )
    _stamp(db, foreign)
    monkeypatch.setenv(ENV_DB, str(db))

    # Act
    # Assert
    with pytest.raises(RuntimeError, match="DIFFERENT"):
        _store_backend.write_doc_to_db({"tasks": [_card(1)]}, db)


# EOF
