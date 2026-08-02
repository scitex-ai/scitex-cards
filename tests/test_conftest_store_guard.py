#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The live-store damage detector must still bite after being made precise.

``tests/conftest.py``'s session guard was changed on 2026-07-22 from "did the
real store file change (mtime/size)" to "is the real store still intact". The
old criterion fired on every run longer than a few seconds, because peer fleet
agents write to this shared board continuously. Measured, the old criterion
firing on a peer's in-place card update::

    before (mtime_ns, size) = (1784700394236217844, 34537472)
    after  (mtime_ns, size) = (1784701251174118319, 34537472)

Making a gate quieter is exactly how a gate gets accidentally disabled, so
this file pins the trade explicitly: every damage shape this suite has
actually inflicted on production must still be detected, plus the shapes an
adversarial review found the first version had stopped detecting, and the
specific thing that made it noisy must not fire.

Each test carries ONE assertion, so a failure names one property rather than
stopping at the first of several. Where two facts are genuinely inseparable
(``exactly one store was reported`` and ``for the right reason``), they are
folded into a single assertion whose message prints both — see
:func:`_sole_reason`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from _store_damage import (
    MONOTONE_TABLES,
    content_or_none,
    damage,
    damaged_candidates,
)

#: A healthy "before" snapshot, shaped like the real board.
BEFORE = {
    "counts": {t: 100 for t in MONOTONE_TABLES},
    "live_ids": frozenset({"card-1", "card-2", "card-3"}),
    "meta": {
        "schema_version": "4",
        "store_path": "/home/agent/.scitex/cards/cards.db",
        "min_client_version": "0.17.5",
        "yaml_path": "/nonexistent/scitex-cards-canonical-db-DO-NOT-MIRROR/tasks.yaml",
    },
    "integrity": "ok",
}


def _after(**overrides):
    """A copy of :data:`BEFORE` with ``overrides`` applied."""
    return {**BEFORE, **overrides}


def _counts(**overrides):
    """The healthy per-table counts with ``overrides`` applied."""
    return {**BEFORE["counts"], **overrides}


def _sole_reason(damaged) -> str:
    """The reason reported for the single damaged store, else a describing text.

    Two facts matter at every end-to-end call site — that EXACTLY ONE store was
    reported damaged, and that it was reported for the RIGHT REASON — and they
    are not independent: the second is meaningless without the first. Returning
    a describing string when the count is wrong keeps both in one assertion
    while still naming which of them failed, instead of an opaque
    ``assert 0 == 1``.
    """
    if len(damaged) != 1:
        return f"(expected exactly one damaged store, got {len(damaged)}: {damaged!r})"
    return damaged[0][1]


# --------------------------------------------------------------------------- #
# MUST DETECT — every shape that has actually destroyed data.                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("label", "after_count"),
    [
        ("2026-07-19 mirror write path", 21),
        ("2026-07-19 canonical write path", 1),
        ("2026-07-19 canonical read path", 3),
        ("2026-07-21 third wipe", 18),
        ("off-by-one — one card silently dropped", 99),
    ],
)
def test_detects_every_historical_wipe_shape(label, after_count):
    """A count DECREASE is always damage — the store is append-only."""
    # Arrange
    after = _after(counts=_counts(tasks=after_count))
    # Act
    why = damage(BEFORE, after)
    # Assert
    assert "SHRANK" in (why or ""), f"{label} went undetected (damage said {why!r})"


@pytest.mark.parametrize("table", [t for t in MONOTONE_TABLES if t != "tasks"])
def test_detects_a_wipe_of_any_watched_table_with_cards_intact(table):
    """The review's HIGH finding: cards intact, another table emptied.

    ``_sync_sections`` DELETEs users/user_names/notifications and re-inserts
    from the incoming doc's sections; ``_db_sections`` returns early WITHOUT
    raising when those keys are absent, so the delete commits. Card count,
    schema_meta and integrity all stay pristine — the first version of this
    guard watched only ``tasks`` and would have reported nothing.
    """
    # Arrange — that table emptied, everything else untouched
    after = _after(counts=_counts(**{table: 0}))
    # Act
    why = damage(BEFORE, after)
    # Assert
    assert table in (why or "") and "SHRANK" in (why or ""), (
        f"a wipe of {table} went undetected (damage said {why!r})"
    )


def test_detects_delete_then_reinsert_at_equal_count():
    """Total loss that a cardinality check reads as unchanged."""
    # Arrange — every real card replaced by the same NUMBER of fixture cards,
    # by construction rather than by an asserted coincidence
    replaced = frozenset(f"fixture-{i}" for i in range(len(BEFORE["live_ids"])))
    # Act
    why = damage(BEFORE, _after(live_ids=replaced))
    # Assert
    assert "stopped being VISIBLE" in (why or ""), (
        f"a same-count replacement of every card went undetected (said {why!r})"
    )


def test_detects_a_mass_tombstone_with_every_row_retained():
    """The review's second HIGH finding: delete_task does not remove rows.

    Since the 2026-07-21 P0, ``delete_task`` marks in place. Emptying the whole
    board through the supported API leaves ``count(*)`` AND the raw id set
    bit-identical, so both a count check and a naive id-set check report a
    pristine store while every card has vanished from the board.
    """
    # Arrange — every card tombstoned; rows all still present
    after = _after(live_ids=frozenset())
    # Act
    why = damage(BEFORE, after)
    # Assert
    assert "stopped being VISIBLE" in (why or ""), (
        f"a total tombstone wipe went undetected (damage said {why!r})"
    )


def test_partial_loss_is_not_masked_by_peer_growth():
    """The review's other HIGH finding: absolute counts have a dead zone.

    Peers add ~25 cards/69s. A leak destroying fewer cards than peers add
    nets POSITIVE, so a count comparison stays silent. A per-id set difference
    does not care how much the peers added.
    """
    # Arrange — one real card destroyed, 500 peer cards added
    after_ids = (BEFORE["live_ids"] - {"card-2"}) | {f"peer-{i}" for i in range(500)}
    # Act
    why = damage(
        BEFORE,
        _after(live_ids=after_ids, counts=_counts(tasks=100 - 1 + 500)),
    )
    # Assert
    assert "card-2" in (why or ""), f"partial loss hid behind peer growth ({why!r})"


def test_detects_a_single_vanished_card():
    """Deletes are tombstones; a card never vanishes."""
    # Arrange
    after = _after(live_ids=BEFORE["live_ids"] - {"card-2"}, counts=_counts(tasks=99))
    # Act
    why = damage(BEFORE, after)
    # Assert
    assert why is not None, "a single vanished card went undetected"


def test_detects_schema_meta_damage():
    """wipe #5's shape: the store's identity stamps rewritten."""
    # Arrange
    broken = dict(BEFORE["meta"])
    broken["yaml_path"] = "/home/agent/.scitex/cards/tasks.yaml"  # re-armed
    # Act
    why = damage(BEFORE, _after(meta=broken))
    # Assert
    assert "schema_meta CHANGED" in (why or ""), (
        f"a rewritten yaml_path stamp went undetected (damage said {why!r})"
    )


def test_detects_dropped_version_floor():
    """Losing ``min_client_version`` re-opens the board to outdated clients."""
    # Arrange
    without_floor = {
        k: v for k, v in BEFORE["meta"].items() if k != "min_client_version"
    }
    # Act
    why = damage(BEFORE, _after(meta=without_floor))
    # Assert
    assert "schema_meta CHANGED" in (why or ""), (
        f"a dropped version floor went undetected (damage said {why!r})"
    )


def test_corruption_during_the_session_is_attributed_to_the_session():
    """ok -> broken means this run broke it, and may say so."""
    # Arrange
    after = _after(integrity="row 42 missing from index")
    # Act
    why = damage(BEFORE, after)
    # Assert
    assert "DURING this session" in (why or ""), (
        f"corruption appearing during the run was not attributed to it ({why!r})"
    )


def test_pre_existing_corruption_is_reported_without_blaming_the_session():
    """Broken before we started: still fails, but does not invent a culprit.

    The fixture's banner says "DAMAGED DURING THIS TEST SESSION". If the board
    was already corrupt at snapshot time that sentence is false, and silently
    ignoring corruption is worse than a misattribution — so it must report,
    and must say which happened.
    """
    # Arrange
    already = {**BEFORE, "integrity": "malformed database schema"}
    # Act
    why = damage(already, {**already, "integrity": "malformed database schema"})
    # Assert
    assert "ALREADY" in (why or "") and "NOT caused by this run" in (why or ""), (
        f"pre-existing corruption must report without blaming the run ({why!r})"
    )


def test_detects_store_becoming_unreadable():
    """Readable before, unreadable after."""
    # Arrange
    after = {"unreadable": "disk I/O error"}
    # Act
    why = damage(BEFORE, after)
    # Assert
    assert "UNREADABLE" in (why or ""), (
        f"a store becoming unreadable went undetected (damage said {why!r})"
    )


def test_detects_store_disappearing():
    """It existed when we snapshotted it and does not now."""
    # Arrange
    before = BEFORE
    # Act
    why = damage(before, None)
    # Assert
    assert "DISAPPEARED" in (why or ""), (
        f"a store disappearing went undetected (damage said {why!r})"
    )


def test_reports_a_gate_that_was_never_armed():
    """Unreadable AT SNAPSHOT TIME must not silently disarm the guard.

    Returning "no damage" because the before-read failed is failing OPEN: the
    session then runs with no live-store protection at all and says nothing.
    """
    # Arrange
    before = {"unreadable": "database is locked"}
    # Act
    why = damage(before, _after())
    # Assert
    assert "never armed" in (why or ""), (
        f"a gate that never armed was not reported (damage said {why!r})"
    )


# --------------------------------------------------------------------------- #
# MUST NOT FIRE — the noise the change exists to remove.                       #
# --------------------------------------------------------------------------- #


def test_peer_writes_do_not_fire():
    """Peers add cards to this shared live board throughout any long run."""
    # Arrange — peers added 25 cards
    after = _after(
        counts=_counts(tasks=125),
        live_ids=BEFORE["live_ids"] | {f"peer-{i}" for i in range(25)},
    )
    # Act
    why = damage(BEFORE, after)
    # Assert
    assert why is None, f"a peer's writes fired the guard: {why!r}"


def test_peer_growth_in_every_table_does_not_fire():
    """Comments, edges, notifications and DMs all grow during a long run."""
    # Arrange
    after = _after(counts={t: 250 for t in MONOTONE_TABLES})
    # Act
    why = damage(BEFORE, after)
    # Assert
    assert why is None, f"peer growth across every table fired the guard: {why!r}"


def test_unchanged_store_does_not_fire():
    """The common case."""
    # Arrange
    after = _after()
    # Act
    why = damage(BEFORE, after)
    # Assert
    assert why is None, f"an unchanged store fired the guard: {why!r}"


def test_absent_before_is_not_damage():
    """A candidate path that never existed cannot have been damaged by us."""
    # Arrange
    after = _after()
    # Act — both shapes of "there was nothing there to begin with"
    observed = (damage(None, None), damage(None, after))
    # Assert
    assert observed == (None, None), (
        f"a store that never existed was reported as damaged: {observed!r}"
    )


def test_table_missing_from_this_schema_is_not_damage():
    """An unobservable table must not read as a shrink to zero."""
    # Arrange
    before = {**BEFORE, "counts": _counts(messages=None)}
    # Act
    why = damage(before, _after(counts=_counts(messages=None)))
    # Assert
    assert why is None, f"an unobservable table read as damage: {why!r}"


# --------------------------------------------------------------------------- #
# END TO END, against real databases — the wiring, not just the predicate.     #
# --------------------------------------------------------------------------- #


def _make_store(path: Path, *, tasks: int, meta: dict | None = None) -> None:
    """Write a minimal store-shaped database at ``path``.

    Verifies its OWN output before returning: a fixture that quietly wrote the
    wrong number of rows would make every assertion downstream meaningless, and
    that is the helper's contract to keep rather than each test's to re-check.
    """
    conn = sqlite3.connect(path)
    try:
        for table in MONOTONE_TABLES:
            if table == "tasks":
                conn.execute(
                    "CREATE TABLE tasks (id TEXT PRIMARY KEY, log_meta_json TEXT)"
                )
                continue
            conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO tasks (id) VALUES (?)",
            [(f"card-{i}",) for i in range(tasks)],
        )
        conn.executemany(
            "INSERT INTO users (id) VALUES (?)",
            [(f"agent-{i}",) for i in range(8)],
        )
        conn.executemany(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?)",
            sorted((meta or {"schema_version": "4"}).items()),
        )
        conn.commit()
        written = conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        if written != tasks:
            raise AssertionError(
                f"_make_store wrote {written} tasks, not the {tasks} requested; "
                "every assertion built on this fixture would be meaningless"
            )
    finally:
        conn.close()


def test_end_to_end_detects_a_real_wipe(tmp_path):
    """Snapshot a real DB, really delete its rows, and catch it."""
    # Arrange
    db = tmp_path / "cards.db"
    _make_store(db, tasks=2170)
    before = {db: content_or_none(db)}
    # Act — the 2026-07-21 wipe, for real: 2170 -> 18
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM tasks WHERE id NOT IN (SELECT id FROM tasks LIMIT 18)")
    conn.commit()
    conn.close()
    # Assert
    why = _sole_reason(damaged_candidates(before, (db,)))
    assert "SHRANK: 2170 -> 18" in why, f"a real wipe was not caught: {why}"


def test_end_to_end_detects_the_identity_registry_wipe(tmp_path):
    """The review's HIGH finding, for real: users emptied, cards untouched."""
    # Arrange
    db = tmp_path / "cards.db"
    _make_store(db, tasks=2261)
    before = {db: content_or_none(db)}
    # Act — exactly what _sync_sections does with a doc lacking a users key
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM users")
    conn.commit()
    conn.close()
    # Assert
    why = _sole_reason(damaged_candidates(before, (db,)))
    assert "users" in why, f"identity registry wipe went undetected: {why}"


def test_end_to_end_ignores_a_real_peer_insert(tmp_path):
    """The measured false positive: a peer adds cards and the file changes."""
    # Arrange
    db = tmp_path / "cards.db"
    _make_store(db, tasks=2261)
    before = {db: content_or_none(db)}
    # Act — a peer writes 25 new cards, exactly as sac/scitex-ui do
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO tasks (id) VALUES (?)", [(f"peer-{i}",) for i in range(25)]
    )
    conn.commit()
    conn.close()
    # Assert
    damaged = damaged_candidates(before, (db,))
    assert damaged == [], f"a peer's insert was reported as damage: {damaged!r}"


def test_end_to_end_detects_a_real_stamp_rewrite(tmp_path):
    """wipe #5's shape, for real: the DO-NOT-MIRROR sentinel re-armed."""
    # Arrange
    db = tmp_path / "cards.db"
    sentinel = "/nonexistent/scitex-cards-canonical-db-DO-NOT-MIRROR/tasks.yaml"
    _make_store(db, tasks=10, meta={"yaml_path": sentinel})
    before = {db: content_or_none(db)}
    # Act
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE schema_meta SET value = ? WHERE key = 'yaml_path'",
        ("/home/agent/.scitex/cards/tasks.yaml",),
    )
    conn.commit()
    conn.close()
    # Assert
    why = _sole_reason(damaged_candidates(before, (db,)))
    assert "schema_meta CHANGED" in why, f"a real stamp rewrite was missed: {why}"


def test_end_to_end_detects_a_deleted_store(tmp_path):
    """Existed at snapshot time, gone at teardown."""
    # Arrange
    db = tmp_path / "cards.db"
    _make_store(db, tasks=5)
    before = {db: content_or_none(db)}
    # Act
    db.unlink()
    # Assert
    why = _sole_reason(damaged_candidates(before, (db,)))
    assert "DISAPPEARED" in why, f"a deleted store was not reported: {why}"


def test_end_to_end_detects_a_real_mass_tombstone(tmp_path):
    """The supported delete API, for real: rows retained, board emptied."""
    # Arrange
    db = tmp_path / "cards.db"
    _make_store(db, tasks=2261)
    before = {db: content_or_none(db)}
    # Act — what delete_task does: mark in place, never remove
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE tasks SET log_meta_json = ?",
        ('{"deleted_at": "2026-07-22T06:00:00Z", "deleted_by": "leak"}',),
    )
    conn.commit()
    retained = conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
    conn.close()
    # Assert — every row is still there, so a count check sees nothing wrong;
    # the guard must fire on visibility anyway
    why = _sole_reason(damaged_candidates(before, (db,)))
    assert retained == 2261 and "stopped being VISIBLE" in why, (
        f"mass tombstone went undetected (rows retained={retained}, said {why})"
    )


def test_absent_candidate_is_silent_and_creates_nothing(tmp_path):
    """A candidate path that does not exist must not be reported, or created."""
    # Arrange
    missing = tmp_path / "never-existed.db"
    before = {missing: content_or_none(missing)}
    # Act
    damaged = damaged_candidates(before, (missing,))
    observed = (before[missing], damaged, missing.exists())
    # Assert — reads as absent, reports nothing, and the read-only probe did
    # not bring the store into existence
    assert observed == (None, [], False), (
        "an absent candidate must snapshot as None, report no damage and stay "
        f"absent; got (snapshot, damaged, exists) = {observed!r}"
    )


# EOF
