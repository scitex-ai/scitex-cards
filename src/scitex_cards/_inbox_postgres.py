#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_cards/_inbox_postgres.py
"""The inbox in the SHARED database, so a notification can cross hosts.

The bug this exists to fix
--------------------------
The inbox was a PER-HOST SQLite file at ``<store_dir>/runtime/todo.db``.
Measured 2026-08-09, the same logical inbox on two machines::

    laptop      4901 rows, 1981 unseen, 130 recipients
    compute-04   162 rows,   87 unseen,  12 recipients

Two files that never meet. A notification enqueued on one host was
invisible on the other, so the operator's messages to agents on compute-04
reached nobody — 「scitex-compute-04 のエージェントにカード notification が
言ってないみたいです」 — and it cost him most of a day before anyone
found the cause.

It hid for weeks because nothing failed. Every enqueue succeeded, every
poll returned cleanly, and both were telling the truth about a file that
the reader was never going to open.

Postgres is the SOURCE OF TRUTH here, not a mirror
--------------------------------------------------
``_db_mirror._sync_sections`` rebuilds a ``notifications`` table from
``doc["inboxes"]`` with a whole-section DELETE and re-insert. That is
SQLite-only — the function is typed ``sqlite3.Connection`` and takes a
``db_path``, so it cannot reach this backend. But it shows the existing
architecture treats notifications as a DERIVED PROJECTION of the document.

A cross-host inbox cannot work that way, because the document is per-host —
that IS the bug above. So when this backend is selected the shared table is
authoritative and ``doc["inboxes"]`` is not. Two writers over one logical
inbox is the shape that lost 2159 rows in July; this module does not
re-create it.

What it refuses to do
---------------------
**Never falls back.** If this backend is selected and the database is
unreachable, it RAISES and names the DSN. A fallback to a local file would
accept every write, report success, reach nobody, and leave nothing in any
log — which is precisely the failure above, re-implemented on purpose.

**Reading never marks seen.** :func:`poll_inbox` leaves ``seen`` alone
unless the caller explicitly asks. The separate :func:`ack` exists so a
consumer that dies between reading and confirming loses nothing; ack-on-read
destroyed five operator DMs on 2026-07-29.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final, Sequence

from ._inbox_record import notification_columns, notification_record
from ._inbox_shape import POSTGRES_SHAPE

__all__ = ["ack", "enqueue", "poll_inbox", "resolve_dsn"]

#: Where the DSN comes from. The store setting is consulted first because
#: the inbox belongs with the cards; the dedicated variable is an override
#: for the case where they genuinely differ.
_ENV_INBOX_DSN: Final[str] = "SCITEX_CARDS_INBOX_DSN"
_ENV_STORE: Final[str] = "SCITEX_CARDS_DB"
_ENV_STORE_LEGACY: Final[str] = "SCITEX_TODO_DB"

#: Table/column/ordering names come from the shared shape, NOT from
#: constants here. `_inbox_shape` already measured why the three travel
#: together: `inbox.recipient` -> `notifications.recipient_id` is a rename,
#: but `ORDER BY rowid` -> `ORDER BY seq` is a REPLACEMENT, and a call site
#: that renamed the table without fixing the ordering would produce SQL
#: valid on both engines that silently loses delivery order.
_SHAPE: Final = POSTGRES_SHAPE
_TABLE: Final[str] = _SHAPE.table
_RECIPIENT: Final[str] = _SHAPE.recipient
_ORDER: Final[str] = _SHAPE.order()


class InboxUnavailableError(RuntimeError):
    """The Postgres inbox was selected and could not be reached.

    Deliberately fatal. The alternative — quietly using a local file — is
    the bug this module exists to remove.
    """


def resolve_dsn(store: "str | Path | None" = None) -> str:
    """The DSN this backend writes to, or raise saying what to set.

    ``store`` wins when it is already a DSN, so a caller that has resolved
    the store does not get second-guessed by the environment.
    """
    if store is not None:
        text = str(store)
        if text.startswith(("postgres://", "postgresql://")):
            return text
    for name in (_ENV_INBOX_DSN, _ENV_STORE, _ENV_STORE_LEGACY):
        value = (os.environ.get(name) or "").strip()
        if value.startswith(("postgres://", "postgresql://")):
            return value
    raise InboxUnavailableError(
        "The Postgres inbox backend is selected but no DSN was found.\n"
        "\n"
        f"Looked at: store argument, ${_ENV_INBOX_DSN}, ${_ENV_STORE}, "
        f"${_ENV_STORE_LEGACY}.\n"
        "\n"
        "Set one to a 'postgresql://...' URL, or select another backend "
        "with SCITEX_TODO_INBOX_BACKEND=sqlite. This does NOT fall back to "
        "a local file on its own: a private inbox nobody else can read is "
        "the exact failure this backend was written to remove."
    )


def _connect(store: "str | Path | None"):
    """Open a connection, or raise naming the DSN. Never returns None."""
    dsn = resolve_dsn(store)
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise InboxUnavailableError(
            "The Postgres inbox backend needs the 'psycopg' driver, which is "
            f"not installed ({exc}). Install psycopg[binary], or select "
            "another backend with SCITEX_TODO_INBOX_BACKEND=sqlite."
        ) from None
    try:
        return psycopg.connect(dsn, autocommit=False)
    except Exception as exc:
        safe = _safe_dsn(dsn)
        raise InboxUnavailableError(
            f"Cannot reach the Postgres inbox at {safe}: {exc}\n"
            "\n"
            "NOT falling back to a local file. A local inbox would accept "
            "every write and reach nobody, which is how this defect stayed "
            "hidden for weeks."
        ) from None


def _safe_dsn(dsn: str) -> str:
    """``postgres://<host>:<port>/<db>`` — never the password."""
    from urllib.parse import urlsplit

    parts = urlsplit(dsn)
    host = parts.hostname or "?"
    port = f":{parts.port}" if parts.port else ""
    database = parts.path.lstrip("/") or "?"
    return f"postgres://{host}{port}/{database}"


def _row_to_record(row: Sequence[Any], columns: Sequence[str]) -> dict:
    """One API row. ``seen`` is normalised to a bool for the MCP contract."""
    record = dict(zip(columns, row))
    record["seen"] = bool(record.get("seen"))
    return record


_SELECT_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "event_type",
    "card_id",
    "body",
    "actor",
    "ts",
    "seen",
    "msg_id",
)
_SELECT_LIST: Final[str] = ", ".join(_SELECT_COLUMNS)


def enqueue(
    recipient_id: str,
    *,
    event_type: str,
    card_id: str,
    body: str,
    actor: "str | None",
    ts: "str | None" = None,
    supersede: bool = False,
    msg_id: "str | None" = None,
    store: "str | Path | None" = None,
) -> "dict | None":
    """Postgres twin of :func:`scitex_cards._inbox.enqueue` — same contract.

    Dedup mirrors the SQLite backend exactly, including WHY: with a
    ``msg_id`` the key is exact, and without one it falls back to
    ``(event_type, card_id, ts, actor)``, which is many-to-one by
    construction because DM timestamps are second-resolution. Two distinct
    durable messages were measured collapsing onto one notification on the
    live store, and the second was never delivered — so a producer that can
    supply ``msg_id`` should.
    """
    if not recipient_id:
        return None

    from ._inbox import _generate_notification_id, _utc_now_iso

    timestamp = ts if ts is not None else _utc_now_iso()

    with _connect(store) as conn:
        with conn.cursor() as cur:
            if supersede:
                # Cumulative snapshot: a newer full-state notification makes
                # its UNSEEN predecessors redundant. Seen ones are left
                # alone — they are already history.
                cur.execute(
                    f"UPDATE {_TABLE} SET seen = 1 "
                    f"WHERE {_RECIPIENT} = %s AND seen = 0 "
                    "AND event_type IS NOT DISTINCT FROM %s "
                    "AND card_id IS NOT DISTINCT FROM %s",
                    (recipient_id, event_type, card_id),
                )

            if msg_id:
                cur.execute(
                    f"SELECT 1 FROM {_TABLE} WHERE {_RECIPIENT} = %s "
                    "AND msg_id IS NOT DISTINCT FROM %s LIMIT 1",
                    (recipient_id, msg_id),
                )
            else:
                cur.execute(
                    f"SELECT 1 FROM {_TABLE} WHERE {_RECIPIENT} = %s "
                    "AND event_type IS NOT DISTINCT FROM %s "
                    "AND card_id IS NOT DISTINCT FROM %s "
                    "AND ts IS NOT DISTINCT FROM %s "
                    "AND actor IS NOT DISTINCT FROM %s LIMIT 1",
                    (recipient_id, event_type, card_id, timestamp, actor),
                )
            if cur.fetchone() is not None:
                # Persist a supersede-only pass even when the insert dedups.
                conn.commit()
                return None

            record = notification_record(
                id=_generate_notification_id(),
                event_type=event_type,
                card_id=card_id,
                body=body,
                actor=actor,
                ts=timestamp,
                seen=False,
                msg_id=msg_id,
            )
            # `record_json` IS NOT OPTIONAL, and omitting it is a fleet outage.
            #
            # This INSERT listed nine columns and left the payload out. Every
            # notification it wrote landed with record_json NULL — not
            # occasionally, on EVERY row — because nothing here ever called the
            # serialiser. A NULL payload makes the read path refuse, and that
            # path assembles the WHOLE document, so on 2026-08-11 it took every
            # card write down fleet-wide three times (add_task, update_task,
            # comment_task) over rows minutes old. Two of the blocking rows were
            # UNDELIVERED DMs, so the obvious "quarantine the bad row" remedy
            # would have destroyed real messages; they were repaired by
            # back-filling from their own columns, which is possible precisely
            # because nothing was lost.
            #
            # #803 fixed this INSERT by adding the missing column. The column
            # list is now DERIVED FROM THE RECORD instead, because hand-writing
            # it IS the defect: three separate writers of this table each
            # hand-wrote their own list and all three omitted the payload. There
            # is no spelling of the call below that drops it without also
            # dropping the id and the body.
            columns, values = notification_columns(
                record,
                recipient_id=recipient_id,
                recipient_column=_RECIPIENT,
                payload_column=_SHAPE.payload,
            )
            placeholders = ", ".join(["%s"] * len(columns))
            # ON CONFLICT DO NOTHING makes a retried insert idempotent even
            # if two writers race past the dedup SELECT above.
            cur.execute(
                f"INSERT INTO {_TABLE}({', '.join(columns)}) "
                f"VALUES({placeholders}) "
                "ON CONFLICT (id) DO NOTHING",
                values,
            )
        conn.commit()
    return dict(record)


def poll_inbox(
    recipient_id: str,
    *,
    unseen_only: bool = True,
    mark_seen: bool = False,
    store: "str | Path | None" = None,
) -> list[dict]:
    """Postgres twin of :func:`scitex_cards._inbox.poll_inbox` — same contract.

    ``mark_seen`` defaults to FALSE and should stay that way. Handing
    notifications over is not the same as confirming they were delivered:
    a consumer that dies in between must find them again on its next poll.
    """
    if not recipient_id:
        return []

    where_seen = "AND seen = 0" if unseen_only else ""

    with _connect(store) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_LIST} FROM {_TABLE} "
                f"WHERE {_RECIPIENT} = %s {where_seen} {_ORDER}",
                (recipient_id,),
            )
            rows = cur.fetchall()
            records = [_row_to_record(r, _SELECT_COLUMNS) for r in rows]

            if not mark_seen or not records:
                conn.commit()
                return records

            ids = [r["id"] for r in records]
            cur.execute(
                f"UPDATE {_TABLE} SET seen = 1 "
                f"WHERE {_RECIPIENT} = %s AND id = ANY(%s)",
                (recipient_id, ids),
            )
        conn.commit()

    for record in records:
        record["seen"] = True
    return records


def ack(
    recipient_id: str,
    notification_ids: "list[str] | str",
    store: "str | Path | None" = None,
) -> list[str]:
    """Postgres twin of :func:`scitex_cards._inbox.ack` — same contract.

    Returns the ids ACTUALLY flipped unseen -> seen, in arrival order, so an
    already-seen or unknown id is a no-op for that id rather than an error.
    That is what makes a retrying consumer safe: confirming twice costs
    nothing.
    """
    if not recipient_id:
        return []
    if isinstance(notification_ids, str):
        notification_ids = [notification_ids]
    wanted = [nid for nid in (notification_ids or []) if nid]
    if not wanted:
        return []

    with _connect(store) as conn:
        with conn.cursor() as cur:
            # Flip and RETURN in one statement: a separate SELECT then
            # UPDATE would let a concurrent ack flip a row in between and
            # report it twice.
            cur.execute(
                f"UPDATE {_TABLE} SET seen = 1 "
                f"WHERE {_RECIPIENT} = %s AND seen = 0 AND id = ANY(%s) "
                "RETURNING id, seq",
                (recipient_id, wanted),
            )
            flipped = [row[0] for row in sorted(cur.fetchall(), key=lambda r: r[1])]
        conn.commit()
    return flipped


# EOF
