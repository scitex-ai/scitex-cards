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
    """The names a statement against the inbox rail needs.

    ``payload`` joined the other three after a fourth difference cost a night.
    The canonical ``notifications`` table carries a VERBATIM ``record_json``
    that the export/read path reconstructs from and REFUSES a row without; the
    SQLite ``inbox`` table has no such column and needs none, because nothing
    exports it. An enqueue that assumed one shape wrote payload-less rows into
    the other, and one such row failed every card write fleet-wide — so where
    the payload lives belongs here, with the other names that differ, rather
    than in each writer's head.
    """

    #: Table holding the notification rows.
    table: str
    #: Column naming the recipient.
    recipient: str
    #: Expression giving ARRIVAL order, oldest first.
    order_by: str
    #: Column holding the VERBATIM record payload, or ``None`` where the table
    #: has no such column. NOT a boolean: the read path NAMES the column, so
    #: the shape must too.
    #:
    #: THIS FIELD EXISTS BECAUSE OMITTING IT TOOK THE FLEET BOARD DOWN FOR 20
    #: MINUTES ON 2026-08-09. The rail's INSERT named nine columns, correct on
    #: SQLite where ``inbox`` has exactly those. On the canonical store
    #: ``notifications.record_json`` then landed NULL — and a NULL there is
    #: LOAD-BEARING: ``_db_payload.card_payload_json``'s own docstring says it
    #: makes the read guard REFUSE THE WHOLE DB rather than hand back a card
    #: whose fields changed shape. So ONE malformed notification made all 3556
    #: cards unreadable and unwritable fleet-wide, with `resolve_store` and
    #: `health` still green because the store itself was fine.
    #:
    #: Putting the column name in the SHAPE rather than in an ``if`` at the
    #: call site is deliberate: the shape is what every statement already
    #: consults, so a future backend cannot be added without answering "does it
    #: carry a payload, and under what name".
    payload: str | None = None

    def order(self) -> str:
        """``ORDER BY <arrival order>`` — spelled once so call sites cannot drift."""
        return f"ORDER BY {self.order_by}"


#: The rail as it exists today: its own SQLite file, ``rowid`` as arrival order.
#: No payload column — the ``inbox`` table never had one, which is why the
#: nine-column INSERT was correct here and lethal on the store. ``payload`` is
#: passed EXPLICITLY rather than left to the default: this is the shape whose
#: missing payload caused the outage, so "it has none" is stated, not implied.
SQLITE_SHAPE = InboxShape(
    table="inbox", recipient="recipient", order_by="rowid", payload=None
)

#: The rail in the canonical store. ``seq`` is schema v9's arrival-order column,
#: server-assigned via a sequence DEFAULT so a client that predates it still
#: writes a correctly ordered row. ``record_json`` is schema v3's payload column,
#: which the export reconstructs from — a row without it is unreadable.
POSTGRES_SHAPE = InboxShape(
    table="notifications",
    recipient="recipient_id",
    order_by="seq",
    payload="record_json",
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
