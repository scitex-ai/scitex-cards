#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_cards/_inbox_receipt_postgres.py
"""Delivery receipts, written where the notifications actually are.

The half of the rail that did not move
--------------------------------------
#780 moved ``enqueue`` / ``poll_inbox`` / ``ack`` into the shared PostgreSQL
``notifications`` table. :mod:`scitex_cards._inbox_receipt` — the module that
stamps ``pushed_at`` when the channel hands a record to the transport, and
``confirmed_at`` when the recipient confirms it by id — did not come along. It
dispatched on ``_use_sqlite()`` (retired 2026-08-23), which was TWO-VALUED, so
the shared-inbox case fell into the ``else`` and wrote a FILE.

MEASURED ON THE LIVE STORE, 2026-08-11 23:30Z::

    notifications rows                                  8
    rows with pushed_at set                             0
    rows with confirmed_at set                          0
    ~/.scitex/cards/.inboxes.json.lock                  present, 22:04 today
    ~/.scitex/cards/inboxes.json                        DOES NOT EXIST

The lock without the file is the whole story: the file rail was being taken at
runtime, finding no such recipient, and returning "stamped nothing" to a caller
that had just successfully pushed a message. Nothing failed, nothing logged.

What that cost, concretely
--------------------------
* ``pushed_at`` never landed, so :mod:`scitex_cards._health_delivery` — the
  check that exists to catch the 2026-07-29 loss of five operator DMs — could
  see no pushes at all and had nothing to go red about. The instrument built
  after that incident was reading a different database from the one under test.
* ``confirmed_at`` never landed, so a recipient's ``ack_notifications`` left no
  evidence, and ``unconfirmed_ids`` reported an empty list for every agent:
  "nothing outstanding", from a rail where everything was outstanding.

Why this is a separate module
-----------------------------
Same seam as ``_inbox_postgres``: the file half knows about paths, and this one
knows about a server. Keeping them apart is what let the (now-deleted) SQLite
half be retired without surgery inside a live module.

One statement per operation, deliberately
-----------------------------------------
A stamp is a single ``UPDATE ... RETURNING``. A SELECT-then-UPDATE would let a
concurrent drain flip a row in between and report the same record pushed twice,
and this rail is shared by every agent on every host — the exact condition that
makes that race ordinary rather than theoretical.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from ._inbox_shape import POSTGRES_SHAPE

__all__ = ["receipts", "stamp"]

_TABLE: Final[str] = POSTGRES_SHAPE.table
_RECIPIENT: Final[str] = POSTGRES_SHAPE.recipient
_ORDER_COLUMN: Final[str] = POSTGRES_SHAPE.order_by

#: Columns a receipt read returns, in the shape the file/SQLite readers return.
_READ_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "event_type",
    "card_id",
    "ts",
    "seen",
    "pushed_at",
    "confirmed_at",
)


def _stampable(column: str) -> str:
    """Return ``column``, or raise — a stamp target is never caller data.

    ``column`` is interpolated into SQL. It is only ever one of two module
    constants today, and this check is what keeps that true: a future caller
    that passes a name from outside gets an error instead of an injection.
    """
    from ._inbox_receipt import RECEIPT_COLUMNS

    if column not in RECEIPT_COLUMNS:
        raise ValueError(
            f"{column!r} is not a receipt column. Expected one of "
            f"{', '.join(RECEIPT_COLUMNS)}."
        )
    return column


def stamp(
    recipient_id: str,
    ids: list[str],
    *,
    column: str,
    stamp: str,
    advance_cursor: bool,
    store: "str | Path | None" = None,
) -> list[str]:
    """Stamp ``column`` on ``ids`` (FIRST stamp wins) in ONE atomic UPDATE.

    ``advance_cursor`` additionally flips ``seen`` in the SAME statement, which
    is what makes "the push is recorded" and "the cursor moved" one fact with no
    window in between — a crash between them would either re-push a delivered
    record or lose an undelivered one.

    ``COALESCE`` keeps the FIRST stamp: the age of an unconfirmed push must
    measure how long it has gone unanswered, not how recently it was retried.

    Returns the ids that exist in this recipient's inbox (the ones stamped), in
    arrival order.
    """
    from ._inbox_postgres import _connect  # noqa: PLC0415 -- import cycle

    target = _stampable(column)
    seen_clause = "seen = 1, " if advance_cursor else ""
    with _connect(store) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {_TABLE} "
                f"SET {seen_clause}{target} = COALESCE({target}, %s) "
                f"WHERE {_RECIPIENT} = %s AND id = ANY(%s) "
                f"RETURNING id, {_ORDER_COLUMN}",
                (stamp, recipient_id, list(ids)),
            )
            rows = cur.fetchall()
        conn.commit()
    # Arrival order, and the sort key is the ordering COLUMN rather than the
    # order rows happened to come back in: `RETURNING` makes no ordering
    # promise, and this list is what a caller reports as "delivered".
    return [row[0] for row in sorted(rows, key=lambda r: (r[1] is None, r[1]))]


def receipts(recipient_id: str, store: "str | Path | None" = None) -> list[dict]:
    """Every record for ``recipient_id`` with its receipts, oldest first.

    READ-ONLY. It creates nothing and alters nothing, so the delivery doctor can
    measure the rail without changing what it measures.
    """
    from ._inbox_postgres import _connect  # noqa: PLC0415 -- import cycle

    columns = ", ".join(_READ_COLUMNS)
    with _connect(store) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {columns} FROM {_TABLE} WHERE {_RECIPIENT} = %s "
                f"{POSTGRES_SHAPE.order()}",
                (recipient_id,),
            )
            rows = cur.fetchall()
        conn.rollback()
    out: list[dict] = []
    for row in rows:
        record: dict[str, Any] = dict(zip(_READ_COLUMNS, row))
        record["seen"] = bool(record.get("seen"))
        out.append(record)
    return out


# EOF
