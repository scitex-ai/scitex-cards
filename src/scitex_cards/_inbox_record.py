#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_cards/_inbox_record.py
"""The notification RECORD and its payload, built in one place so no writer
can omit half of it.

The outage this exists to remove
--------------------------------
``notifications.record_json`` holds each record VERBATIM; the export/read path
(:mod:`scitex_cards._db_export`) reconstructs from that payload, never from the
typed columns, and REFUSES a row that has none. That invariant held for as long
as the only writer of the table was the IMPORTER, which populated the payload.

The shared Postgres inbox (#780) made ``notifications`` a LIVE-WRITTEN table.
Its ``enqueue`` — and the SQLite twin, and the carry — each spelled their own
INSERT column list, and all three listed the typed columns WITHOUT
``record_json``. Every notification they wrote was therefore unreadable by the
very next read, and because the read assembles the WHOLE document, one such row
failed every card write fleet-wide: ``add_task``, ``update_task``,
``comment_task``, all of it. Measured 2026-08-11, three payload-less rows —
``n_d826dea57cd1`` 22:03:54Z, ``n_f31b4d1e2803`` 22:17:19Z, ``n_3bd421eb5f33``
19:02:07Z — took the board down three times in one night, and one of them was an
operator DM that had never been delivered.

The reader's message blamed a legacy schema ("this DB predates schema v3"). The
rows were MINUTES OLD, written by current code into a database created by
current code, so the message sent three separate agents hunting a migration
problem that did not exist. An error that names the wrong cause costs more than
no error.

Why a module and not a helper next to each writer
-------------------------------------------------
Three writers spelled the same record three times and all three drifted the same
way. The fix is not "remember the column": it is that the record and its payload
are now ONE object, and :func:`notification_columns` derives the INSERT's column
list FROM that object — so a writer cannot spell an insert that omits the
payload without also omitting the id and the body. The field-less shape is not
guarded against; it is unrepresentable.

Rebuilding is exact here, which is why the reader may repair
-----------------------------------------------------------
A CARD may not be rebuilt from its columns — measured on the live store, 22
distinct card keys are not in the column mapping at all, so a column rebuild
drops them silently (see :mod:`scitex_cards._db_payload`). A NOTIFICATION is the
opposite case: its shape is CLOSED and every key is a column of its own, so
:func:`rebuild_notification_record` returns exactly what the writer would have
stored.

The nuance that makes this safe: an IMPORTED notification may carry keys this
module has never heard of (the export tests seed one), and those keys live only
in the payload — but the importer ALWAYS writes the payload. A row with a NULL
payload therefore cannot be an imported row. It can only have come from a
column-only writer, whose record is exactly the closed shape below. So there is
nothing to lose by rebuilding, and a whole board to lose by refusing.
"""

from __future__ import annotations

import json
from typing import Any, Final, Mapping

__all__ = [
    "NOTIFICATION_RECORD_KEYS",
    "notification_columns",
    "notification_payload",
    "notification_record",
    "rebuild_notification_record",
]

#: The record's keys, IN ORDER. The order is part of the contract: the export
#: reproduces each record's own key order, so a rebuild that reordered them
#: would change the shape of every serialized notification.
NOTIFICATION_RECORD_KEYS: Final[tuple[str, ...]] = (
    "id",
    "event_type",
    "card_id",
    "body",
    "actor",
    "ts",
    "seen",
    "msg_id",
)

#: Keys whose column is NOT NULL in the schema — a row missing any of them
#: carries no recoverable record and must not be silently invented.
_REQUIRED_KEYS: Final[tuple[str, ...]] = ("id", "event_type", "ts")


def notification_record(
    *,
    id: str,
    event_type: str,
    card_id: "str | None",
    body: "str | None",
    actor: "str | None",
    ts: str,
    seen: bool = False,
    msg_id: "str | None" = None,
) -> dict:
    """The record every inbox backend enqueues — one shape, one definition.

    Keyword-only by design: the three writers this replaces each passed these
    eight values positionally into their own INSERT, and a positional list is
    exactly what silently drifts when a column is added.
    """
    return {
        "id": id,
        "event_type": event_type,
        "card_id": card_id,
        "body": body,
        "actor": actor,
        "ts": ts,
        "seen": bool(seen),
        "msg_id": msg_id,
    }


def notification_payload(record: Mapping[str, Any]) -> str:
    """The record VERBATIM as JSON — the blob a read reconstructs from.

    ``sort_keys`` is deliberately NOT set, matching
    :func:`scitex_cards._db_payload.card_payload_json`: the read hands callers
    the record's own key order, so this must preserve it.
    """
    return json.dumps(dict(record), ensure_ascii=False)


def notification_columns(
    record: Mapping[str, Any],
    *,
    recipient_id: str,
    recipient_column: str,
    payload_column: "str | None" = "record_json",
    origin_column: "str | None" = None,
) -> "tuple[tuple[str, ...], tuple[Any, ...]]":
    """``(column_names, values)`` for an INSERT — derived FROM the record.

    This is the barrier. The payload is not an extra argument a writer may
    forget to pass; it is computed here, from the same object that supplies the
    id and the body, and it is emitted as part of the same tuple. There is no
    spelling of this call that yields the columns without the payload.

    ``payload_column`` is ``None`` for the SQLite ``inbox`` table, which has no
    payload column at all — it is not the table the export reads, so a payload
    there would be dead weight rather than an invariant. Passing ``None`` is the
    one legitimate way to get a column list without the payload, and it is named
    after the table's real shape rather than left to a caller's belief.

    ``seen`` is emitted as the INTEGER the column is declared as, while the
    payload keeps the JSON bool the record contract returns.
    """
    columns: list[str] = ["id", recipient_column]
    values: list[Any] = [record["id"], recipient_id]
    for key in NOTIFICATION_RECORD_KEYS:
        if key == "id":
            continue
        columns.append(key)
        values.append(int(record["seen"]) if key == "seen" else record.get(key))
    if payload_column is not None:
        columns.append(payload_column)
        values.append(notification_payload(record))
    if origin_column is not None:
        # WHICH NODE WROTE THIS ROW. Emitted here, beside the payload, for the
        # identical reason: three writers of this table each hand-wrote a
        # column list and all three omitted the payload, so the list is derived
        # rather than typed. `origin_node` was omitted by every one of them too
        # — measured 2026-08-21, 0 of 7,737 notification rows populated, while
        # `sweep_state` (the one writer that does call it) has 598 of 598.
        #
        # The value comes from the existing helper rather than a new one: the
        # concept, the env override and the hostname fallback are already
        # decided in `_db_sweep_state._origin_node`, and a second spelling of
        # "which node am I" is how two answers to one question begin.
        from ._db_sweep_state import _origin_node  # noqa: PLC0415 -- import cycle

        columns.append(origin_column)
        values.append(_origin_node())
    return tuple(columns), tuple(values)


def rebuild_notification_record(row: Any) -> "dict | None":
    """Reconstruct a record from a row's own columns, or ``None`` if it cannot.

    Used by the read path to REPAIR a payload-less row rather than refuse the
    whole database for it. Returns ``None`` — never a partial record — when a
    NOT NULL column is absent or empty, because such a row carries no record to
    recover and inventing one would be the stripped export the payload column
    exists to prevent.
    """
    try:
        available = set(row.keys())
    except AttributeError:
        return None
    if not set(_REQUIRED_KEYS) <= available:
        return None
    values = {key: row[key] for key in NOTIFICATION_RECORD_KEYS if key in available}
    if any(values.get(key) in (None, "") for key in _REQUIRED_KEYS):
        return None
    return notification_record(
        id=values["id"],
        event_type=values["event_type"],
        card_id=values.get("card_id"),
        body=values.get("body"),
        actor=values.get("actor"),
        ts=values["ts"],
        seen=bool(values.get("seen")),
        msg_id=values.get("msg_id"),
    )


# EOF
