#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Carry inbox rows from the SQLite rail onto the canonical store.

The inbox rail still lives in its own SQLite file (``<store_dir>/runtime/cards.db``,
table ``inbox``) while cards live in the canonical store, which for this
deployment is PostgreSQL (table ``notifications``). Measured 2026-08-02:

    SQLite  inbox          3434 rows, 1525 unseen
    Postgres notifications    0 rows

The destination exists, is schema-versioned, and is EMPTY, so the migration is a
carry rather than a merge. The two shapes differ in more than location:

    inbox.recipient   ->  notifications.recipient_id
    (no equivalent)   ->  notifications.record_json

VERIFY BY SET MEMBERSHIP, NEVER BY COUNT. This is the module's central rule and
it is not stylistic. Unseen-count is an ACCUMULATOR THAT A WORKING DRAIN RESETS:
every successful delivery decrements it, and the drain is under active repair.
The count moved 1502 -> 1525 during a single working session. So:

* A gate asserting "expect N rows" fails in the SUCCESS case — delivery starts
  working, the count legitimately drops, and the gate reads that as data loss.
* Comparing a before-count to an after-count cannot distinguish rows lost by the
  carry from rows legitimately drained mid-flight. The two are identical by count.

Row IDs do not have that problem. A row acked mid-carry is still PRESENT, merely
seen, so membership is stable under concurrent drains while counts are not. Hence
:func:`verify_carry` reports MISSING IDS and treats the target as satisfied when
it is a SUPERSET — a floor, never an equality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = [
    "CarryResult",
    "carry_rows",
    "read_source_rows",
    "source_ids",
    "target_ids",
    "verify_carry",
]

#: Columns read from the SQLite ``inbox`` table, in a fixed order so the
#: INSERT below cannot drift out of alignment with the SELECT.
SOURCE_COLUMNS = (
    "id",
    "recipient",
    "event_type",
    "card_id",
    "body",
    "actor",
    "ts",
    "seen",
    "msg_id",
    "pushed_at",
    "confirmed_at",
)

#: The matching ``notifications`` columns. Same order as SOURCE_COLUMNS;
#: ``recipient`` is the one rename. ``record_json`` is appended last and has NO
#: source column — see :func:`carry_rows`, which COMPUTES it.
TARGET_COLUMNS = (
    "id",
    "recipient_id",
    "event_type",
    "card_id",
    "body",
    "actor",
    "ts",
    "seen",
    "msg_id",
    "pushed_at",
    "confirmed_at",
    "record_json",
)


@dataclass
class CarryResult:
    """What a carry did, in terms that survive a concurrent drain."""

    #: Row ids read from the source.
    source: set[str] = field(default_factory=set)
    #: Row ids present in the target afterwards.
    target: set[str] = field(default_factory=set)
    #: Source ids the target does NOT have. Empty means the carry is complete.
    missing: set[str] = field(default_factory=set)

    @property
    def complete(self) -> bool:
        """True when the target is a SUPERSET of the source.

        A superset is success, not a discrepancy: the target may legitimately
        hold rows the source never had (a later enqueue, a prior partial carry).
        Only MISSING ids indicate loss.
        """
        return not self.missing


def read_source_rows(conn: Any) -> list[tuple]:
    """Every row of the SQLite ``inbox`` table, as plain tuples.

    Reads ALL rows by predicate-free select rather than filtering to unseen:
    seen history is part of the rail (an acked row proves a delivery happened)
    and dropping it would silently rewrite the record.
    """
    columns = ", ".join(SOURCE_COLUMNS)
    cursor = conn.execute(f"SELECT {columns} FROM inbox")
    return [tuple(row) for row in cursor.fetchall()]


def source_ids(conn: Any) -> set[str]:
    """Row ids present in the SQLite source."""
    return {str(row[0]) for row in conn.execute("SELECT id FROM inbox")}


def target_ids(conn: Any) -> set[str]:
    """Row ids present in the target ``notifications`` table."""
    return {str(row[0]) for row in conn.execute("SELECT id FROM notifications")}


def carry_rows(
    rows: Iterable[tuple], target_conn: Any, *, placeholder: str = "%s"
) -> int:
    """Insert ``rows`` into ``notifications``, skipping ids already present.

    IDEMPOTENT BY ID. Re-running a carry is a normal operation — an interrupted
    run must be resumable without duplicating, and a duplicate here is not a
    cosmetic problem: it becomes a second delivery of a message the recipient
    already received.

    ``placeholder`` exists because paramstyle differs between drivers; the
    caller passes what its connection speaks.

    THE PAYLOAD IS COMPUTED, NOT COPIED. ``record_json`` is the one target
    column with no source column — the module docstring's "(no equivalent)" —
    and a carry that therefore left it NULL produced rows the export path
    REFUSES, which fails every card write on that database rather than only the
    carried row. There is nothing to copy and everything to compute: the source
    row already holds every field of the record, which is exactly why
    :func:`scitex_cards._inbox_record.rebuild_notification_record` can rebuild
    it exactly.
    """
    columns = ", ".join(TARGET_COLUMNS)
    marks = ", ".join([placeholder] * len(TARGET_COLUMNS))
    statement = (
        f"INSERT INTO notifications ({columns}) VALUES ({marks}) "
        "ON CONFLICT (id) DO NOTHING"
    )
    written = 0
    for row in rows:
        target_conn.execute(statement, _row_with_payload(row))
        written += 1
    return written


def _row_with_payload(row: tuple) -> tuple:
    """``row`` plus the ``record_json`` it has no source column for.

    ``pushed_at`` / ``confirmed_at`` are deliberately NOT in the payload: they
    are DELIVERY STATE the rail mutates after the fact, not part of the record
    the recipient receives — the same reason ``seen`` is overlaid onto the
    payload at export time rather than baked into it.
    """
    from ._inbox_record import notification_payload, notification_record

    fields = dict(zip(SOURCE_COLUMNS, row))
    record = notification_record(
        id=fields["id"],
        event_type=fields["event_type"],
        card_id=fields.get("card_id"),
        body=fields.get("body"),
        actor=fields.get("actor"),
        ts=fields["ts"],
        seen=bool(fields.get("seen")),
        msg_id=fields.get("msg_id"),
    )
    return (*row, notification_payload(record))


def verify_carry(source_conn: Any, target_conn: Any) -> CarryResult:
    """Compare the two rails BY ID and report what the target is missing.

    Deliberately returns a result object rather than a bool: "did it work" is
    not answerable by one flag when the honest answer is "these specific ids did
    not arrive". A caller that wants a gate reads ``.complete``; a caller
    diagnosing a partial carry needs ``.missing``.
    """
    src = source_ids(source_conn)
    tgt = target_ids(target_conn)
    return CarryResult(source=src, target=tgt, missing=src - tgt)


# EOF
