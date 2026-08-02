#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STORE OWNERSHIP GUARD — the mirror that survived, and the toggle that didn't.

This file used to be the S1 dual-write mirror's test suite: a whole feature
that mirrored every card write into SQLite while YAML stayed canonical, gated
by ``SCITEX_TODO_DUAL_WRITE``. That feature is DELETED — not defaulted off —
per the operator's 2026-07-21 ruling: 「データベースしか書く場所なんてありえ
ない。デュアルライトっていうオプションがあること自体がおかしい」. Root cause:
``cards.db`` carried a stale ``schema_meta`` row pointing at an old YAML file,
and an agent whose env still carried the dual-write flag had every write
silently routed there instead of the canonical database — every call
returned SUCCESS and ``health`` stayed green while a whole session of card
writes vanished. Deleting the toggle makes that class of bug unrepresentable:
there is no environment variable left to read that could send a write
anywhere but ``$SCITEX_CARDS_DB``.

What SURVIVES, and is tested below, is the OWNERSHIP GUARD
(``_dual_write._db_mirrors_this_store`` / ``_same_file``): the invariant that
a database belongs to exactly ONE store, checked at the write chokepoint
(:func:`scitex_cards._store_backend.write_doc_to_db`), which RAISES rather
than returning quietly on a mismatch — a write that cannot reach the
canonical DB must NEVER report success.
"""

from __future__ import annotations

import sqlite3

import pytest

from scitex_cards import _dual_write, _store, _store_backend
from scitex_cards._db import ENV_DB


def test_the_dual_write_module_now_exposes_only_the_ownership_guard():
    """The deletion, pinned at the module boundary.

    ``_dual_write`` used to export a toggle (``enabled``,
    ``mirror_after_save``, ``ENV_DUAL_WRITE``, ``check_mirror_healthy``, the
    failure counter) alongside the ownership guard. The toggle is DELETED;
    only the guard survives.
    """
    # Arrange
    # Act
    # Assert
    assert set(_dual_write.__all__) == {"_db_mirrors_this_store", "_same_file"}
    for gone in (
        "enabled",
        "ENV_DUAL_WRITE",
        "mirror_after_save",
        "check_mirror_healthy",
        "failure_count",
        "failures",
        "reset_failures",
        "_has_incremental_mirror",
        "_refusal_logged",
        "_failures",
    ):
        assert not hasattr(_dual_write, gone), (
            f"_dual_write.{gone} must not exist — the dual-write toggle was "
            f"DELETED as a feature, not defaulted off"
        )


def test_a_write_reaches_the_db_even_with_the_legacy_flag_set(monkeypatch, tmp_path):
    """The incident, closed: the flag has ZERO effect on where a write lands.

    2026-07-21 root cause: an agent env carrying ``SCITEX_CARDS_DUAL_WRITE=1``
    had every write silently routed to a dead YAML file instead of the
    canonical database. The toggle that made that possible is deleted, so
    setting the EXACT env vars from the incident must have no effect at all —
    the write still lands in ``$SCITEX_CARDS_DB``, or the caller sees a
    real error. There is no silent third outcome.
    """
    # Arrange — bootstrap a real (empty) canonical DB first; `add_task` itself
    # refuses to manufacture a store that does not exist yet (a separate,
    # deliberate guard — see `_store._read_canonical_db_or_raise`), so the
    # legacy flags are set BEFORE that bootstrap too, to prove they influence
    # neither step.
    monkeypatch.setenv("SCITEX_CARDS_DUAL_WRITE", "1")
    monkeypatch.setenv("SCITEX_TODO_DUAL_WRITE", "1")
    store = tmp_path / "tasks.yaml"
    db = tmp_path / "cards.db"
    monkeypatch.setenv(ENV_DB, str(db))
    _store_backend.write_doc_to_db({"tasks": []}, store)

    # Act — the legacy dual-write env vars stay set for the actual write too.
    _store.add_task(store, id="a", title="A", assignee="agent:test-suite")

    # Assert
    assert _db_ids(db) == {"a"}, (
        "a write with the legacy dual-write env vars set must still reach "
        "the canonical database — there is no other place left for it to go"
    )


def _db_ids(db_path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in conn.execute("SELECT id FROM tasks")}
    finally:
        conn.close()


# --------------------------------------------------------------------------
# THE NEAR-MISS — pinned so it can never come back.
# --------------------------------------------------------------------------


def test_a_card_write_does_not_touch_the_messages_table(monkeypatch, tmp_path):
    """DM threads MUST survive a card write.

    `mirror_doc_incremental` is now the SOLE canonical write primitive, and the
    near-miss it guards is unchanged by the cutover: the doc carries `tasks`
    (and the doc-owned sections), never `messages`, which is derived from the
    threads.yaml SIDECAR. A write that rebuilt `messages` from the doc would
    DELETE EVERY DM THREAD ON EVERY CARD WRITE — exactly the kind of thing that
    gets "helpfully" reintroduced by someone tidying up the clear-list. A table
    must be owned by exactly the file that produces it.
    """
    # Arrange — one canonical write builds the DB, then a DM lands in `messages`.
    store = tmp_path / "tasks.yaml"
    db = tmp_path / "own.db"
    monkeypatch.setenv(ENV_DB, str(db))
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {"id": "a", "title": "A", "status": "deferred", "assignee": "tester"}
            ]
        },
        store,
    )
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO messages(id, thread_key, sender, recipient, body, ts, read) "
        "VALUES('m1','dm:x::y','x','y','hello','2026-07-12T00:00:00Z',0)"
    )
    conn.commit()
    conn.close()

    # Act — a second canonical write runs the same mirror primitive again.
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {"id": "a", "title": "A", "status": "deferred", "assignee": "tester"},
                {"id": "b", "title": "B", "status": "deferred", "assignee": "tester"},
            ]
        },
        store,
    )

    # Assert
    conn = sqlite3.connect(str(db))
    try:
        surviving = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        conn.close()
    assert surviving == 1, "the write DELETED a DM thread on a card write"


# --------------------------------------------------------------------------- #
# The mirror belongs to ONE store (2026-07-19 incident)                        #
# --------------------------------------------------------------------------- #
#
# The package's own concurrency test copies os.environ into writer subprocesses,
# so they inherited SCITEX_CARDS_DUAL_WRITE=1, wrote to a pytest tmp store, and
# rebuilt the LIVE fleet DB from a 21-card fixture — replacing 2,136 real cards.
# The three tests below pin the rule that makes that unrepresentable, and the two
# controls guard the opposite error of refusing legitimate mirrors.


def _mirror_of_store_a(monkeypatch, tmp_path):
    """A DB holding A's card, STAMPED for a FOREIGN identity (not its own path).

    Single-identity model: the store IS the database, so "a database that
    belongs to a different store" is one whose provenance stamp names a
    DIFFERENT database path than ``$SCITEX_CARDS_DB`` resolves. The ownership
    guard must refuse a write or read against it. Built by seeding the rows then
    stamping the provenance for a foreign path by hand (the production write
    door stamps the database's OWN path, so it cannot manufacture this).
    """
    from conftest import seed_db_from_doc

    from scitex_cards._db import connect
    from scitex_cards._db_freshness import stamp_store_provenance

    db = tmp_path / "mirror-of-a.db"
    foreign = tmp_path / "a" / "cards.db"  # a DIFFERENT database path
    monkeypatch.setenv(ENV_DB, str(db))
    doc = {
        "tasks": [
            {
                "id": "a-only",
                "title": "A",
                "status": "deferred",
                "assignee": "agent:test-suite",
            }
        ]
    }
    seed_db_from_doc(doc, str(db))
    conn = connect(str(db))
    try:
        conn.execute("BEGIN IMMEDIATE")
        stamp_store_provenance(conn, foreign)
        conn.commit()
    finally:
        conn.close()
    return db


def test_the_mirror_of_store_a_really_holds_store_as_card(monkeypatch, tmp_path):
    # Arrange
    # Act
    db = _mirror_of_store_a(monkeypatch, tmp_path)

    # Assert — the precondition every foreign-write test below rests on.
    assert "a-only" in _db_ids(db), "precondition: the DB mirrors store A"


def test_a_write_to_a_foreign_store_does_not_clobber_another_stores_mirror(
    monkeypatch, tmp_path
):
    """The incident, in miniature: store B must not overwrite store A's mirror.

    The public card verb is protected, not only the low-level primitive: store B
    is the resolved store, but the DB it resolves is stamped for store A, so the
    read door of `add_task` RAISES before any row is touched.
    """
    # Arrange — a DB stamped for a FOREIGN identity, holding A's card.
    db = _mirror_of_store_a(monkeypatch, tmp_path)

    # Act — the resolved database is foreign-stamped, so the read door of
    # add_task RAISES before any row is touched (the `store` arg is cosmetic —
    # identity is the database path).
    store_b = tmp_path / "b" / "cards.db"
    with pytest.raises(RuntimeError):
        _store.add_task(store_b, id="b-only", title="B", assignee="agent:test-suite")

    # Assert — A's mirror is untouched; B never entered it.
    assert _db_ids(db) == {"a-only"}, (
        "a write to store B must not reach the DB that mirrors store A — "
        "mirroring is a REPLACE, so this is how a fixture destroys a live board"
    )


def test_an_unstamped_db_is_adoptable_so_a_fresh_mirror_still_bootstraps(
    monkeypatch, tmp_path
):
    """Control: refusing must not break the FIRST write to a brand-new mirror."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    db = tmp_path / "fresh.db"
    monkeypatch.setenv(ENV_DB, str(db))

    # Act — the first canonical write to an un-adopted DB must claim it.
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {
                    "id": "first",
                    "title": "first",
                    "status": "deferred",
                    "assignee": "agent:test-suite",
                }
            ]
        },
        store,
    )

    # Assert
    assert "first" in _db_ids(db), (
        "an unstamped DB has no store to protect, so the first write claims it"
    )


def test_a_store_writing_to_its_own_mirror_is_not_refused(monkeypatch, tmp_path):
    """Control: the normal case — repeated writes to one's own mirror keep working."""
    # Arrange — first write adopts the DB and stamps it for `store`.
    store = tmp_path / "tasks.yaml"
    db = tmp_path / "own.db"
    monkeypatch.setenv(ENV_DB, str(db))
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {
                    "id": "one",
                    "title": "one",
                    "status": "deferred",
                    "assignee": "agent:test-suite",
                }
            ]
        },
        store,
    )

    # Act — a second write to that same store's own mirror must NOT be refused.
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {
                    "id": "one",
                    "title": "one",
                    "status": "deferred",
                    "assignee": "agent:test-suite",
                },
                {
                    "id": "two",
                    "title": "two",
                    "status": "deferred",
                    "assignee": "agent:test-suite",
                },
            ]
        },
        store,
    )

    # Assert
    assert _db_ids(db) == {"one", "two"}, (
        "a store writing to the DB stamped for that same store must mirror normally"
    )


# The second door. mirror_after_save declines; the CANONICAL path must RAISE.
#
# Guarding only the mirror path left this one open, and the same pytest run that
# first rebuilt the live DB from a fixture (2,136 -> 21) did it again through here,
# harder (2,138 -> 1). Declining quietly is right when YAML still holds the card and
# wrong when the DB is the only copy: it would report success for a card that was
# never stored.
def test_canonical_write_to_a_foreign_db_RAISES_rather_than_clobbering(
    monkeypatch, tmp_path
):
    # Arrange — a DB that belongs to store A.

    db = _mirror_of_store_a(monkeypatch, tmp_path)
    store_b = tmp_path / "b" / "cards.db"

    # Act
    # Assert — a canonical write to a foreign-stamped database must refuse LOUDLY.
    with pytest.raises(RuntimeError, match="DIFFERENT"):
        _store_backend.write_doc_to_db({"tasks": [{"id": "b-only"}]}, store_b)


def test_a_refused_canonical_write_leaves_the_foreign_rows_untouched(
    monkeypatch, tmp_path
):
    # Arrange — a DB that belongs to store A.

    db = _mirror_of_store_a(monkeypatch, tmp_path)
    store_b = tmp_path / "b" / "cards.db"

    # Act — the refused write. (Captured, not `pytest.raises`: the refusal
    # itself is pinned by the test above; here it is only the setup for the
    # single question this test asks — did any row change?)
    try:
        _store_backend.write_doc_to_db({"tasks": [{"id": "b-only"}]}, store_b)
    except RuntimeError:
        pass

    # Assert — store A's rows are untouched. (Had the write been honoured
    # instead of refused, `b-only` would be here and this would fail.)
    assert _db_ids(db) == {"a-only"}


#: WHY THE NEXT TWO EXIST, and why they are a PAIR. On 2026-07-19 the WRITE
#: door refused foreign stores correctly all day while the READ door happily
#: returned them. That asymmetry is how a packaged fixture came to be read AS
#: THE BOARD for hours: nothing objected until someone tried to write, by which
#: point the wrong rows were already being treated as authoritative.
#: `_read_canonical_db_or_raise` is a read-MODIFY-write helper, so what the
#: write door would refuse has to fail at the read door too.
#: They are split because one alone is not evidence. A refusal test alone
#: passes for a guard wired to refuse EVERYTHING — which is the same
#: always-red uselessness as an always-green gate, and this codebase has
#: shipped that shape more than once. The healthy-read test is what proves the
#: guard discriminates rather than merely fires.
def test_reading_a_foreign_stamped_db_RAISES_rather_than_returning_its_rows(
    monkeypatch, tmp_path
):
    # Arrange — a database whose provenance names a DIFFERENT database path.
    from scitex_cards._store import _read_canonical_db_or_raise

    _mirror_of_store_a(monkeypatch, tmp_path)

    # Act
    # Assert
    with pytest.raises(RuntimeError, match="REFUSING TO READ"):
        _read_canonical_db_or_raise()


def test_reading_the_db_that_owns_this_store_returns_its_cards(monkeypatch, tmp_path):
    # Arrange — a database stamped for its OWN path (the normal case): the first
    # canonical write adopts the fresh database and stamps its own identity.
    from scitex_cards._store import _read_canonical_db_or_raise

    db = tmp_path / "own.db"
    monkeypatch.setenv(ENV_DB, str(db))
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {
                    "id": "a-only",
                    "title": "A",
                    "status": "deferred",
                    "assignee": "agent:test-suite",
                }
            ]
        },
        tmp_path / "cosmetic.db",
    )

    # Act
    doc = _read_canonical_db_or_raise()

    # Assert — the guard lets the OWNING store through.
    assert [t["id"] for t in doc["tasks"]] == ["a-only"]


def test_a_missing_canonical_db_RAISES_instead_of_reading_an_empty_store(
    monkeypatch, tmp_path
):
    """A failed READ must never become a write of nothing.

    In canonical mode the value returned here is written back as the WHOLE
    store, so `export_doc` answering a missing database with a well-formed
    {"tasks": []} is not "no cards" but "delete every card". That is not
    theoretical: one comment_task call took the live board from 2,138 cards to
    3 this way. Type-checking the result cannot catch it — the empty document
    is indistinguishable from a real empty board — so the check asks the file
    system instead.
    """
    # Arrange — point at a database that does not exist.
    from scitex_cards._store import _read_canonical_db_or_raise

    monkeypatch.setenv(ENV_DB, str(tmp_path / "not-here.db"))

    # Act
    # Assert
    with pytest.raises(RuntimeError, match="does not exist"):
        _read_canonical_db_or_raise()
