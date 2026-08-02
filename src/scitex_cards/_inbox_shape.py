#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where the inbox rail's rows live, spelled for whichever backend is connected.

The rail is moving from its own SQLite file to the canonical store, and the two
ends do not merely differ in location. Measured on the live stores 2026-08-02:

    SQLite  runtime/todo.db   table ``inbox``          3496 rows
    Postgres (cards store)    table ``notifications``     0 rows

    inbox.recipient   ->  notifications.recipient_id     a RENAME
    ORDER BY rowid    ->  ORDER BY seq                   a REPLACEMENT

THE ORDERING IS THE PART THAT CANNOT BE A RENAME. ``rowid`` is a SQLite
implementation detail with no PostgreSQL equivalent, and it is what the drain
and the ack both order by — so a pure table/column rename produces SQL that is
valid on both engines and silently loses delivery order. Schema v9 added
``notifications.seq`` for exactly this; ``inbox`` has no such column and does
not need one, because on SQLite ``rowid`` already IS the arrival order.

So the three facts travel together. Splitting them would let a caller rename the
table without fixing the ordering, which is the failure this module exists to
make unrepresentable.

WHY NOT ``ORDER BY ts, id``, which the export path uses for ``notifications``:
measured on the live rail, 1256 of 3496 positions differ from arrival order and
8 are genuine timestamp INVERSIONS, because ``enqueue(ts=...)`` takes a
caller-supplied timestamp. Reproducible is not the same as chronological. The
full measurement is in :func:`scitex_cards._db_migrations._migrate_v8_to_v9`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["InboxShape", "SQLITE_SHAPE", "POSTGRES_SHAPE", "shape_for"]


@dataclass(frozen=True)
class InboxShape:
    """The three names a statement against the inbox rail needs."""

    #: Table holding the notification rows.
    table: str
    #: Column naming the recipient.
    recipient: str
    #: Expression giving ARRIVAL order, oldest first.
    order_by: str

    def order(self) -> str:
        """``ORDER BY <arrival order>`` — spelled once so call sites cannot drift."""
        return f"ORDER BY {self.order_by}"


#: The rail as it exists today: its own SQLite file, ``rowid`` as arrival order.
SQLITE_SHAPE = InboxShape(table="inbox", recipient="recipient", order_by="rowid")

#: The rail in the canonical store. ``seq`` is schema v9's arrival-order column,
#: server-assigned via a sequence DEFAULT so a client that predates it still
#: writes a correctly ordered row.
POSTGRES_SHAPE = InboxShape(
    table="notifications", recipient="recipient_id", order_by="seq"
)


def shape_for(conn: Any) -> InboxShape:
    """Pick the shape from the LIVE connection, never from a caller's belief.

    Same reasoning as :func:`scitex_cards._sql_null_safe.null_safe_eq_for`: the
    backend is a property of what is actually open. Reading it from the
    connection is what stops a call site being correct in tests and wrong in
    production.
    """
    from ._schema_probe import _is_postgres  # noqa: PLC0415 -- import cycle

    return POSTGRES_SHAPE if _is_postgres(conn) else SQLITE_SHAPE


# EOF
