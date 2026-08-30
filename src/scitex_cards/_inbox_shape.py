#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where the inbox rail's rows live — the names every statement against it needs.

The rail moved from its own per-host file into the canonical store, and the two
ends did not merely differ in location. Measured on the live stores 2026-08-02:

    the retired per-host file   table ``inbox``          3496 rows
    the cards store             table ``notifications``     0 rows

    inbox.recipient   ->  notifications.recipient_id     a RENAME
    ORDER BY rowid    ->  ORDER BY seq                   a REPLACEMENT

THE ORDERING WAS THE PART THAT COULD NOT BE A RENAME. The retired rail ordered
by an implicit row counter with no equivalent here, and that counter is what the
drain and the ack both ordered by — so a pure table/column rename would have
produced SQL that runs and silently loses delivery order. Schema v9 added
``notifications.seq`` for exactly this.

So the three facts travel together in one shape. Splitting them would let a
caller rename the table without fixing the ordering, which is the failure this
module exists to make unrepresentable.

WHY NOT ``ORDER BY ts, id``, which the export path uses for ``notifications``:
measured on the live rail, 1256 of 3496 positions differ from arrival order and
8 are genuine timestamp INVERSIONS, because ``enqueue(ts=...)`` takes a
caller-supplied timestamp. Reproducible is not the same as chronological. The
full measurement is in :func:`scitex_cards._db_migrations._migrate_v8_to_v9`.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["InboxShape", "POSTGRES_SHAPE"]


@dataclass(frozen=True)
class InboxShape:
    """The names a statement against the inbox rail needs.

    ``payload`` joined the other three after a fourth difference cost a night.
    The canonical ``notifications`` table carries a VERBATIM ``record_json``
    that the export/read path reconstructs from and REFUSES a row without; the
    retired rail's ``inbox`` table had no such column and needed none, because
    nothing exported it. An enqueue that assumed one shape wrote payload-less
    rows into the other, and one such row failed every card write fleet-wide —
    so where the payload lives belongs here, with the other names, rather than
    in each writer's head.
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
    #: MINUTES ON 2026-08-09. The rail's INSERT named nine columns, correct
    #: against the retired ``inbox`` table, which had exactly those. On the
    #: canonical store
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
    #: Column recording WHICH NODE WROTE THE ROW, or ``None`` where the table
    #: has no such column. Same reasoning as ``payload``, one incident later.
    #:
    #: THIS FIELD EXISTS BECAUSE ITS ABSENCE COST TWO AGENTS TWO SESSIONS.
    #: `notifications.origin_node` has been in the schema since the sync-column
    #: migration, declared for the stated purpose of surviving a host boundary
    #: (`_db_sync_columns`: "crosses a host boundary carries origin_node,
    #: row_uuid, revision, updated_at, deleted_at"). Measured 2026-08-21:
    #:
    #:     sweep_state      598 rows   origin_node populated 598  ['scitex-compute-04']
    #:     notifications  7,737 rows   origin_node populated   0
    #:     task_comments 13,191 rows   origin_node populated   0
    #:     tasks          5,719 rows   origin_node populated   0
    #:
    #: One writer of four filled it in. So when stale digests began arriving
    #: from an unidentified producer, the rows could not say where they came
    #: from and the question had to be chased by ELIMINATION instead: a
    #: six-host unit sweep, a sequence analysis, and two hypotheses refuted —
    #: across two agents — for a fact the row was designed to carry.
    #:
    #: The value is not a new concept: `_db_sweep_state._origin_node()` already
    #: computes it, and `sweep_state` proves the shape is a plain host name.
    #: What was missing was calling it here.
    origin: str | None = None

    def order(self) -> str:
        """``ORDER BY <arrival order>`` — spelled once so call sites cannot drift."""
        return f"ORDER BY {self.order_by}"


#: The rail in the canonical store. ``seq`` is schema v9's arrival-order column,
#: server-assigned via a sequence DEFAULT so a client that predates it still
#: writes a correctly ordered row. ``record_json`` is schema v3's payload column,
#: which the export reconstructs from — a row without it is unreadable.
POSTGRES_SHAPE = InboxShape(
    table="notifications",
    recipient="recipient_id",
    order_by="seq",
    payload="record_json",
    origin="origin_node",
)


# EOF
