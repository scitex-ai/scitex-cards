#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The DM backfill and the multi-host merge, as executable requirements.

DESIGN: ``docs/design/dm-into-cards-db-migration.md`` (part 2 — migration,
reversibility, concurrency). Part 1 and its tests cover the schema and the
append-only rules; see ``tests/scitex_cards/test__dm_into_db_design.py``.

These landed as non-strict ``xfail`` INTENT before the migration existed. The
implementation is now in ``scitex_cards._dm_migrate``, so the markers are
gone: an intent test that keeps its xfail after the feature ships stops being
a requirement and becomes a note.

WHY THIS FILE EXISTED BEFORE THE MIGRATION. The two properties that make a
store migration survivable are not obvious from the code that performs it:
that it can be RUN TWICE without doubling anything, and that it can be
UNDONE because it never touched the source. Both are cheap to assert and
expensive to discover afterwards, so they are written down as executable
intent before anyone writes the migration they describe.

The merge tests encode the payoff of the append-only schema: because every
row is immutable and carries a globally-unique primary key, reconciling two
hosts is a pure UNION — no last-write-wins, no clock comparison, and no
operation that can reduce a count. The operator's ruling is that a count
decrease is itself a bug; ``test_merge_never_shrinks_the_message_count`` is
that ruling made executable.

Every database here is an EXPLICIT ``tmp_path`` file. Nothing in this file
migrates real data or touches the live fleet store.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

#: The design this file encodes.
DESIGN_DOC = "docs/design/dm-into-cards-db-migration.md"

#: The one pair thread every fixture below shares.
THREAD_ID = "dm:agent-x::operator"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """An EXPLICIT database path nobody else can resolve."""
    return tmp_path / "cards.db"


@pytest.fixture()
def sidecar(tmp_path: Path) -> Path:
    """A real ``threads.json`` in the shape the live sidecar uses.

    Two records, one already read, so the backfill has both a message and a
    read receipt to carry across.
    """
    path = tmp_path / "threads.json"
    path.write_text(
        json.dumps(
            {
                "threads": {
                    THREAD_ID: [
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
                    ]
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _peer_payload(message_ids: list[str]) -> dict:
    """A peer host's DM export carrying ``message_ids`` in the shared thread.

    Shaped like the union-merge payload the design specifies: one entry per
    table, every row carrying its own primary key so the merge is an
    ``INSERT OR IGNORE`` with no arbitration.

    ``seq`` IS DERIVED FROM THE ID, NOT FROM THE LIST POSITION, and the
    distinction is load-bearing for ``test_thread_order_is_independent_of_
    insertion_order``. That test merges this payload and its reverse, then
    asserts both hosts render the same conversation — which is only a
    statement about ORDERING if both hosts hold THE SAME ROWS. Numbering by
    position made the reversed payload a set of DIFFERENT rows (same ids,
    different ``seq``), so the test would have been asserting that ``seq`` is
    ignored, and the only implementation satisfying it would have dropped the
    per-thread counter that keeps same-second messages in append order.

    In reality a message is authored ONCE, on one host, with one ``seq``;
    merge copies it verbatim. Deriving from the id reproduces that.
    """
    order = sorted(message_ids)
    return {
        "dm_threads": [
            {
                "id": THREAD_ID,
                "kind": "pair",
                "created_at": "2026-07-28T00:00:00Z",
                "origin_host": "host-b",
                "record_json": "{}",
            }
        ],
        "dm_thread_member_events": [],
        "dm_messages": [
            {
                "id": mid,
                "thread_id": THREAD_ID,
                "sender": "agent-x",
                "body": f"from peer {mid}",
                "ts": "2026-07-28T00:00:05Z",
                "seq": order.index(mid) + 1,
                "origin_host": "host-b",
                "record_json": "{}",
            }
            for mid in message_ids
        ],
        "dm_receipts": [],
    }


# --------------------------------------------------------------------------- #
# INTENT — backfill (design part 2, M1)                                        #
# --------------------------------------------------------------------------- #
def test_backfill_leaves_the_sidecar_byte_identical(db_path, sidecar):
    """Reversibility is a property of the SOURCE being untouched, not of a plan.

    As long as ``threads.json`` is still exactly the file it was, rolling back
    is redeploying the previous version — no restore, no repair, no reasoning
    about what the migration got halfway through.
    """
    # Arrange
    from scitex_cards._dm_store import backfill_from_sidecar

    before = sidecar.read_bytes()

    # Act
    backfill_from_sidecar(sidecar, db=db_path)

    # Assert
    assert sidecar.read_bytes() == before


def test_backfill_is_idempotent(db_path, sidecar):
    """A migration that cannot be re-run safely cannot be resumed safely.

    Every insert keys on a stable primary key, so a second pass inserts
    nothing. That is what makes an interrupted backfill recoverable by simply
    running it again.
    """
    # Arrange
    from scitex_cards._dm_store import backfill_from_sidecar, message_count

    backfill_from_sidecar(sidecar, db=db_path)
    first = message_count(db=db_path)

    # Act
    backfill_from_sidecar(sidecar, db=db_path)

    # Assert
    assert message_count(db=db_path) == first


def test_backfill_carries_every_sidecar_message(db_path, sidecar):
    """The migration may lose nothing — not even a record with no id.

    The inbox migration SKIPS id-less records because it cannot dedup them on
    a re-run. Skipping a DM would lose a message, so the design mints a
    content-derived id instead. This test is what forbids the cheaper answer.
    """
    # Arrange
    from scitex_cards._dm_store import backfill_from_sidecar, message_count

    # Act
    backfill_from_sidecar(sidecar, db=db_path)

    # Assert
    assert message_count(db=db_path) == 2


def test_backfill_preserves_read_state_as_a_receipt(db_path, sidecar):
    """A read message must not pop unread for everyone at cutover.

    The sidecar records ``read: true`` without saying WHEN, so the receipt is
    stamped with the backfill time plus a ``backfill`` source — a sentinel,
    not an absence. A NULL would be indistinguishable from "never read".
    """
    # Arrange
    from scitex_cards._dm_store import backfill_from_sidecar, unread_for

    # Act
    backfill_from_sidecar(sidecar, db=db_path)

    # Assert — only the message the sidecar marked unread is still unread.
    assert [m["id"] for m in unread_for("agent-x", db=db_path)] == []


# --------------------------------------------------------------------------- #
# INTENT — group threads (design part 1 section 3, exercised end to end)        #
# --------------------------------------------------------------------------- #
def test_group_message_is_visible_to_every_member(db_path):
    """The reason this card exists: one message, more than one recipient.

    Under today's schema this is not expressible at all — ``recipient`` is a
    single column. Deriving recipients from thread membership is what makes it
    a normal query instead of a schema change.
    """
    # Arrange
    from scitex_cards._dm_store import append, create_group_thread, unread_for

    thread = create_group_thread(
        "standup", ["operator", "agent-a", "agent-b"], db=db_path
    )
    append(thread["id"], "operator", "morning", db=db_path)

    # Act
    reached = {r for r in ("agent-a", "agent-b") if unread_for(r, db=db_path)}

    # Assert
    assert reached == {"agent-a", "agent-b"}


def test_read_receipt_is_scoped_to_one_reader(db_path):
    """With three members, "read" is not a property of the message.

    A single boolean cannot say that Bob read it and Carol did not, which is
    why read state moves into its own per-reader table.
    """
    # Arrange
    from scitex_cards._dm_store import (
        append,
        create_group_thread,
        mark_read,
        unread_for,
    )

    thread = create_group_thread(
        "standup", ["operator", "agent-a", "agent-b"], db=db_path
    )
    message = append(thread["id"], "operator", "morning", db=db_path)

    # Act
    mark_read([message["id"]], "agent-a", db=db_path)

    # Assert — agent-b's unread state is untouched by agent-a reading.
    assert [m["id"] for m in unread_for("agent-b", db=db_path)] == [message["id"]]


def test_group_thread_id_survives_a_membership_change(db_path):
    """A thread id derived from its members would orphan history on a join.

    Adding a member must not move the thread. If the id were the sorted member
    set, every earlier message would have to be rewritten into a new thread —
    a delete-and-insert, which append-only forbids.
    """
    # Arrange
    from scitex_cards._dm_store import (
        add_member,
        append,
        create_group_thread,
        messages_in,
    )

    thread = create_group_thread("war room", ["operator", "agent-a"], db=db_path)
    first = append(thread["id"], "operator", "before the join", db=db_path)

    # Act
    add_member(thread["id"], "agent-b", db=db_path)

    # Assert — the pre-join message is still reachable under the SAME id.
    assert [m["id"] for m in messages_in(thread["id"], db=db_path)] == [first["id"]]


# --------------------------------------------------------------------------- #
# INTENT — multi-host merge (design part 2 section 6.3)                         #
# --------------------------------------------------------------------------- #
def test_merge_from_a_peer_host_is_a_union(db_path, sidecar):
    """Two hosts today FORK silently; reconciliation must add, never replace.

    Because every row is immutable with a globally-unique key, merge is an
    ``INSERT OR IGNORE`` union — no arbitration and no way for a peer's
    snapshot to overwrite a local row.
    """
    # Arrange
    from scitex_cards._dm_store import (
        backfill_from_sidecar,
        merge_dm,
        message_count,
    )

    backfill_from_sidecar(sidecar, db=db_path)

    # Act
    merge_dm(_peer_payload(["m_cccccccccccc"]), db=db_path)

    # Assert
    assert message_count(db=db_path) == 3


def test_merge_is_idempotent(db_path, sidecar):
    """Merging twice must be free, or no operator will dare run it twice.

    Idempotence is also what makes merge order irrelevant across three or more
    hosts, which is the property that removes the need for a coordinator.
    """
    # Arrange
    from scitex_cards._dm_store import (
        backfill_from_sidecar,
        merge_dm,
        message_count,
    )

    backfill_from_sidecar(sidecar, db=db_path)
    payload = _peer_payload(["m_cccccccccccc"])
    merge_dm(payload, db=db_path)
    after_first = message_count(db=db_path)

    # Act
    merge_dm(payload, db=db_path)

    # Assert
    assert message_count(db=db_path) == after_first


def test_merge_never_shrinks_the_message_count(db_path, sidecar):
    """The operator's ruling, made executable: a count decrease IS the bug.

    Every board wipe in the 2026-07-19/20 sequence was a stale snapshot being
    treated as the truth, deleting the rows it happened to lack. A peer's
    export is always a snapshot, and it may be older than what is here — so a
    merge that receives a SUBSET must still keep the local extras.
    """
    # Arrange
    from scitex_cards._dm_store import (
        backfill_from_sidecar,
        merge_dm,
        message_count,
    )

    backfill_from_sidecar(sidecar, db=db_path)
    stale_snapshot = _peer_payload(["m_aaaaaaaaaaaa"])

    # Act
    merge_dm(stale_snapshot, db=db_path)

    # Assert
    assert message_count(db=db_path) == 2


def test_thread_order_is_independent_of_insertion_order(db_path, tmp_path):
    """Every host holding the same rows must render the same conversation.

    Today's order is ``rowid`` — insertion order into one file, which two
    hosts cannot agree on. ``ORDER BY seq, ts, origin_host, id`` is total, so
    order becomes a function of the row SET rather than of arrival sequence.
    """
    # Arrange — the same three rows, merged in opposite orders.
    from scitex_cards._dm_store import merge_dm, messages_in

    ids = ["m_cccccccccccc", "m_dddddddddddd", "m_eeeeeeeeeeee"]
    other_db = tmp_path / "peer.db"
    merge_dm(_peer_payload(ids), db=db_path)
    merge_dm(_peer_payload(list(reversed(ids))), db=other_db)

    # Act
    here = [m["id"] for m in messages_in(THREAD_ID, db=db_path)]
    there = [m["id"] for m in messages_in(THREAD_ID, db=other_db)]

    # Assert
    assert here == there


# EOF
