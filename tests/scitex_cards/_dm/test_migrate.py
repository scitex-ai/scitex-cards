#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The backfill's operational properties: dry run, id minting, the shrink refusal.

DESIGN: ``docs/design/dm-into-cards-db-migration.md`` M1 / M2 / §6.3.

COMPANION to ``test__dm_into_db_migration_design.py``, which encodes the
DESIGN's requirements (reversible, idempotent, union merge). This file covers
what an operator actually does with the migration: run it dry, read the two
counts, decide, apply — and the guards that decide for them when the numbers
disagree.

THE DRY RUN IS THE PART MOST WORTH TESTING, because it is the part everyone
trusts without checking. It performs every insert for real and rolls the
transaction back, so its counts are measurements. A dry run computed by a
separate estimating code path would be describing a different operation than
the one about to run, and would be most wrong exactly when the data is
strangest — which is when someone is reading it.

Every database is an EXPLICIT ``tmp_path`` file. Nothing here migrates real
data or touches the live fleet store.
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pytest

from scitex_cards._dm import store as _dm_store
from scitex_cards._db import open_db
from scitex_cards._dm.migrate import _assert_no_shrink

THREAD_ID = "dm:agent-x::operator"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """An EXPLICIT database path nobody else can resolve."""
    return tmp_path / "cards.db"


def _write_sidecar(path: Path, records: list[dict]) -> Path:
    path.write_text(
        json.dumps({"threads": {THREAD_ID: records}}, indent=2), encoding="utf-8"
    )
    return path


@pytest.fixture()
def sidecar(tmp_path: Path) -> Path:
    """A sidecar in the live file's shape: two records, one already read."""
    return _write_sidecar(
        tmp_path / "threads.json",
        [
            {
                "id": "m_aaaaaaaaaaaa",
                "thread": THREAD_ID,
                "from": "operator",
                "to": "agent-x",
                "body": "first",
                "ts": "2026-07-28T00:00:01Z",
                "read": True,
            },
            {
                "id": "m_bbbbbbbbbbbb",
                "thread": THREAD_ID,
                "from": "agent-x",
                "to": "operator",
                "body": "second",
                "ts": "2026-07-28T00:00:02Z",
                "read": False,
            },
        ],
    )


@pytest.fixture()
def id_less_sidecar(tmp_path: Path) -> Path:
    """A sidecar record with NO id — the case a naive migration skips."""
    return _write_sidecar(
        tmp_path / "threads.json",
        [
            {
                "thread": THREAD_ID,
                "from": "operator",
                "to": "agent-x",
                "body": "no id on this one",
                "ts": "2026-07-28T00:00:01Z",
                "read": False,
            }
        ],
    )


# --------------------------------------------------------------------------- #
# Dry run (design part 2, M1)                                                  #
# --------------------------------------------------------------------------- #
def test_a_dry_run_commits_nothing(db_path, sidecar):
    """The default has to be the one that cannot damage anything.

    A bulk copy into the store of record is the operation that has cost this
    fleet three boards. ``--apply`` is a word an operator types after reading
    the counts, not a flag they have to remember to omit.
    """
    # Arrange
    _dm_store.backfill_from_sidecar(sidecar, db=db_path, dry_run=True)

    # Act
    count = _dm_store.message_count(db=db_path)

    # Assert
    assert count == 0


def test_a_dry_run_reports_what_it_would_insert(db_path, sidecar):
    """The counts are MEASURED, not estimated — every insert really ran.

    An estimate produced by a different code path would be describing a
    different operation, and would diverge exactly where the data is unusual.
    """
    # Arrange
    expected = 2

    # Act
    report = _dm_store.backfill_from_sidecar(sidecar, db=db_path, dry_run=True)

    # Assert
    assert report["inserted_messages"] == expected


def test_the_dry_run_leaves_the_sidecar_byte_identical(db_path, sidecar):
    """Reversibility is a property of the SOURCE being untouched."""
    # Arrange
    before = sidecar.read_bytes()

    # Act
    _dm_store.backfill_from_sidecar(sidecar, db=db_path, dry_run=True)

    # Assert
    assert sidecar.read_bytes() == before


def test_the_applied_backfill_matches_the_sidecar_count(db_path, sidecar):
    """The migration's whole claim, checked: the two sides agree.

    Reporting only one number would be reporting the half that cannot be
    verified against anything.
    """
    # Arrange
    report = _dm_store.backfill_from_sidecar(sidecar, db=db_path)

    # Act
    after = report["db_messages_after"]

    # Assert
    assert report["sidecar_messages"] == after == 2


# --------------------------------------------------------------------------- #
# Ids and read state (design part 2, M1)                                       #
# --------------------------------------------------------------------------- #
def test_a_record_with_no_id_is_carried_across_anyway(db_path, id_less_sidecar):
    """SKIPPING would lose a message, and this migration may not lose one.

    The inbox migration skips id-less records because it cannot dedup them on
    a re-run. Here the id is derived from the content instead, so a second
    pass maps to the same primary key.
    """
    # Arrange
    _dm_store.backfill_from_sidecar(id_less_sidecar, db=db_path)

    # Act
    count = _dm_store.message_count(db=db_path)

    # Assert
    assert count == 1


def test_a_minted_id_is_stable_across_a_second_pass(db_path, id_less_sidecar):
    """Content-derived means idempotent — otherwise a re-run DOUBLES the record.

    This is the test that forbids the cheaper "mint a random id" answer, which
    would look correct on the first run and corrupt the thread on the second.
    """
    # Arrange
    _dm_store.backfill_from_sidecar(id_less_sidecar, db=db_path)

    # Act
    _dm_store.backfill_from_sidecar(id_less_sidecar, db=db_path)

    # Assert
    assert _dm_store.message_count(db=db_path) == 1


def test_existing_message_ids_are_carried_over_verbatim(db_path, sidecar):
    """No id is rewritten. Rewriting one is a delete plus an insert."""
    # Arrange
    _dm_store.backfill_from_sidecar(sidecar, db=db_path)

    # Act
    stored = _dm_store.messages_in(THREAD_ID, db=db_path)

    # Assert
    assert [m["id"] for m in stored] == ["m_aaaaaaaaaaaa", "m_bbbbbbbbbbbb"]


def test_a_backfilled_receipt_is_marked_as_a_sentinel(db_path, sidecar):
    """The sidecar says ``read: true`` but never says WHEN.

    So the receipt carries the backfill time plus ``source='backfill'`` — a
    SENTINEL, not an absence. A NULL ``read_at`` would be indistinguishable
    from "never read", which is a different fact.
    """
    # Arrange
    _dm_store.backfill_from_sidecar(sidecar, db=db_path)
    connection = open_db(db_path)

    # Act
    try:
        sources = [r[0] for r in connection.execute("SELECT source FROM dm_receipts")]
    finally:
        connection.close()

    # Assert
    assert sources == ["backfill"]


def test_the_backfill_joins_both_peers_of_a_pair_thread(db_path, sidecar):
    """Recipients are DERIVED from membership, so membership must exist.

    Without the join events the messages are in the store and visible to
    nobody — present by every count and absent from every read.
    """
    # Arrange
    _dm_store.backfill_from_sidecar(sidecar, db=db_path)

    # Act
    members = _dm_store.list_members(THREAD_ID, db=db_path)

    # Assert
    assert members == ["agent-x", "operator"]


# --------------------------------------------------------------------------- #
# The no-shrink rule (design section 4.4 / 6.3)                                #
# --------------------------------------------------------------------------- #
def test_a_shrinking_post_state_raises():
    """The operator's ruling, called directly: a count decrease IS the bug.

    Exercised at the guard rather than through a merge, because a union
    CANNOT shrink — so the only way to prove the refusal fires is to hand it
    the state a bug would produce. A guard that cannot be shown to fire is
    just an assertion.
    """
    # Arrange — the live table's real row count, one row short of itself.
    shrink = partial(_assert_no_shrink, 2042, 2041)

    # Act
    refusal = pytest.raises(RuntimeError, match="append-only")

    # Assert
    with refusal:
        shrink()


def test_an_unchanged_count_is_not_a_shrink():
    """Idempotence must not trip the guard, or no one dares re-run a merge."""
    # Arrange
    before = 2042

    # Act
    result = _assert_no_shrink(before, before)

    # Assert
    assert result is None


# --------------------------------------------------------------------------- #
# The A/B verification gate (design part 2, M2)                                #
# --------------------------------------------------------------------------- #
def test_verify_reports_the_gap_before_a_backfill(db_path, sidecar):
    """The gate has to FAIL when the store is missing something.

    A gate that only ever passes is indistinguishable from no gate; this is
    its positive control.
    """
    # Arrange
    open_db(db_path).close()

    # Act
    report = _dm_store.verify_against_sidecar(sidecar, db=db_path)

    # Assert
    assert report["missing_in_db"] == ["m_aaaaaaaaaaaa", "m_bbbbbbbbbbbb"]


def test_verify_passes_once_the_backfill_has_run(db_path, sidecar):
    """The clean state M3 waits for."""
    # Arrange
    _dm_store.backfill_from_sidecar(sidecar, db=db_path)

    # Act
    report = _dm_store.verify_against_sidecar(sidecar, db=db_path)

    # Assert
    assert report["ok"] is True


def test_extra_rows_in_the_store_do_not_fail_the_gate(db_path, sidecar):
    """ "The database has more" is the HEALTHY steady state after the flip.

    New DMs land in the store first, so treating a surplus as a mismatch
    would make the gate cry wolf exactly when it is working correctly.
    """
    # Arrange
    _dm_store.backfill_from_sidecar(sidecar, db=db_path)
    _dm_store.append(THREAD_ID, "operator", "sent after the backfill", db=db_path)

    # Act
    report = _dm_store.verify_against_sidecar(sidecar, db=db_path)

    # Assert
    assert report["ok"] is True


# --------------------------------------------------------------------------- #
# Cross-host round trip (design section 6.3)                                   #
# --------------------------------------------------------------------------- #
def test_an_export_merges_into_an_empty_peer_intact(db_path, sidecar, tmp_path):
    """Export then merge is how a second host gets the history at all.

    The payload has to carry threads, membership, messages and receipts — drop
    the membership half and the peer holds messages nobody can see.
    """
    # Arrange
    _dm_store.backfill_from_sidecar(sidecar, db=db_path)
    payload = _dm_store.export_dm(db=db_path)
    peer = tmp_path / "peer.db"

    # Act
    _dm_store.merge_dm(payload, db=peer)

    # Assert
    assert [m["id"] for m in _dm_store.messages_in(THREAD_ID, db=peer)] == [
        "m_aaaaaaaaaaaa",
        "m_bbbbbbbbbbbb",
    ]


def test_a_merged_receipt_keeps_the_peers_read_state(db_path, sidecar, tmp_path):
    """Read state has to survive the hop, or the peer shows everything unread."""
    # Arrange
    _dm_store.backfill_from_sidecar(sidecar, db=db_path)
    peer = tmp_path / "peer.db"
    _dm_store.merge_dm(_dm_store.export_dm(db=db_path), db=peer)

    # Act
    unread = _dm_store.unread_for("agent-x", db=peer)

    # Assert
    assert unread == []


# EOF
