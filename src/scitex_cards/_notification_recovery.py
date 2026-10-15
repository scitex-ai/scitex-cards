"""Finite, recipient-scoped notification recovery. A read never confirms.

The ordinary poll retains its historical heartbeat and delivery-attempt behavior.
This explicit selector path only reads: it neither stamps liveness nor rotates
exchanges. NULL arrival sequences remain NULL; ID order within that historical
phase is deterministic recovery order, not reconstructed arrival order.
"""

from __future__ import annotations

from typing import Any, Protocol

from ._inbox_record import NOTIFICATION_RECORD_KEYS
from ._inbox_shape import POSTGRES_SHAPE

_COLUMNS = NOTIFICATION_RECORD_KEYS + ("pushed_at", "confirmed_at", "seq")
_TABLE = POSTGRES_SHAPE.table
_RECIPIENT = POSTGRES_SHAPE.recipient
_SELECT = ", ".join(f"n.{key}" for key in _COLUMNS)
_MAX_PAGE = 100


def _validate_selectors(
    agent: str,
    unseen_only: bool,
    ack: bool,
    notification_id: str | None,
    limit: int | None,
    after: str | None,
) -> int:
    """Reject invalid requests before identity resolution or store access."""
    if not isinstance(agent, str) or not agent.strip():
        raise ValueError("agent must be a nonempty string")
    if not isinstance(unseen_only, bool) or not isinstance(ack, bool):
        raise ValueError("unseen_only and ack must be booleans")
    if ack:
        raise ValueError("bounded recovery cannot confirm; use ack_notifications")
    for name, value in (("notification_id", notification_id), ("after", after)):
        if value is not None and (
            not isinstance(value, str) or not value or len(value) > 256 or "\0" in value
        ):
            raise ValueError(
                f"{name} must be a nonempty notification ID (<=256 characters)"
            )
    if notification_id is not None and (limit is not None or after is not None):
        raise ValueError("notification_id cannot be combined with limit or after")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= _MAX_PAGE):
        raise ValueError(f"limit must be an integer from 1 to {_MAX_PAGE}")
    return 1 if notification_id is not None else (50 if limit is None else limit)


class _Rows(Protocol):
    """Private read protocol; public callers cannot select a collaborator."""

    def lookup(self, keys: list[str], notification_id: str) -> dict | None: ...

    def page(
        self, keys: list[str], unseen_only: bool, limit: int, cursor: dict | None
    ) -> list[dict]: ...


def _lookup_query(keys: list[str], notification_id: str) -> tuple[str, tuple]:
    # Unknown and foreign IDs use the same scoped query and empty result.
    return (
        f"SELECT {_SELECT} FROM {_TABLE} n "
        f"WHERE n.{_RECIPIENT} = ANY(%s) AND n.id = %s "
        "LIMIT 1",
        (keys, notification_id),
    )


def _page_query(
    keys: list[str], unseen_only: bool, limit: int, cursor: dict | None
) -> tuple[str, tuple]:
    # The schema's global notification-ID primary key makes this union unique.
    # The keyset predicate happens in SQL before the ONE global page limit.
    where = f"n.{_RECIPIENT} = ANY(%s)"
    params: list[Any] = [keys]
    if unseen_only:
        where += " AND n.seen = 0"
    if cursor is not None:
        if cursor["seq"] is None:
            where += " AND n.seq IS NULL AND n.id > %s"
            params.append(cursor["id"])
        else:
            where += (
                " AND (n.seq > %s OR (n.seq = %s AND n.id > %s) OR n.seq IS NULL)"
            )
            params.extend((cursor["seq"], cursor["seq"], cursor["id"]))
    params.append(limit + 1)
    return (
        f"SELECT {_SELECT} FROM {_TABLE} n WHERE {where} "
        "ORDER BY n.seq ASC NULLS LAST, n.id ASC LIMIT %s",
        tuple(params),
    )


class _PostgresRows:
    """The canonical adapter, bound internally to the existing inbox connection."""

    def __init__(self, connection: Any):
        self._connection = connection

    def _read(self, query: str, params: tuple) -> list[dict]:
        with self._connection.cursor() as cursor:
            cursor.execute(query, params)
            return [
                dict(row) if isinstance(row, dict) else dict(zip(_COLUMNS, row))
                for row in cursor.fetchall()
            ]

    def lookup(self, keys: list[str], notification_id: str) -> dict | None:
        rows = self._read(*_lookup_query(keys, notification_id))
        return rows[0] if rows else None

    def page(
        self, keys: list[str], unseen_only: bool, limit: int, cursor: dict | None
    ) -> list[dict]:
        return self._read(*_page_query(keys, unseen_only, limit, cursor))


def _recover_with_rows(
    rows: _Rows,
    *,
    agent: str,
    keys: list[str],
    store: str,
    unseen_only: bool,
    notification_id: str | None,
    limit: int,
    after: str | None,
) -> dict:
    """Real private orchestration, shared by canonical reads and protocol controls."""
    from ._inbox_receipt import is_confirmed

    if notification_id is not None:
        found = rows.lookup(keys, notification_id)
        selected = (
            [found]
            if found is not None and not (unseen_only and found["seen"])
            else []
        )
        more = False
    else:
        cursor = rows.lookup(keys, after) if after is not None else None
        if after is not None and cursor is None:
            raise ValueError("invalid notification cursor for this recipient")
        selected = rows.page(keys, unseen_only, limit, cursor)
        more = len(selected) > limit
        selected = selected[:limit]
    records = []
    for row in selected:
        record = {key: row.get(key) for key in _COLUMNS if key != "seq"}
        record["seen"] = bool(row.get("seen"))
        records.append(record)
    return {
        "agent": agent,
        "recipient_id": keys[-1] if keys else agent,
        "store": store,
        "notifications": records,
        "unconfirmed": [r["id"] for r in records if not is_confirmed(r)],
        "unconfirmed_scope": "page",
        "outstanding": [],
        "confirm_with": "ack_notifications",
        "page": {
            "limit": limit,
            "next_cursor": records[-1]["id"] if more else None,
            "order": (
                "seq ASC NULLS LAST, id ASC "
                "(historical ID order is not arrival order)"
            ),
        },
    }


def recover_notifications(
    agent: str,
    *,
    unseen_only: bool,
    ack: bool,
    store: Any,
    notification_id: str | None,
    limit: int | None,
    after: str | None,
) -> dict:
    page_size = _validate_selectors(
        agent, unseen_only, ack, notification_id, limit, after
    )
    from ._inbox_confirm import recipient_keys
    from ._inbox_postgres import _connect
    from ._store_target import store_label

    keys = recipient_keys(agent, store)
    with _connect(store) as connection:
        try:
            return _recover_with_rows(
                _PostgresRows(connection),
                agent=agent,
                keys=keys,
                store=store_label(store),
                unseen_only=unseen_only,
                notification_id=notification_id,
                limit=page_size,
                after=after,
            )
        finally:
            connection.rollback()
