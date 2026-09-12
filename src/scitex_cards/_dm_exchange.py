#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DM exchange status through the canonical ``scitex_dev.status`` primitive.

The DM row and the exchange row answer different questions. ``dm_messages``
proves that the content is durable; ``status_exchanges`` says whether the
accepted delivery exchange ever reached a later, final observation. The
immediate status is HTTP 202: accepted and persisted, not displayed or
acknowledged.
"""

from __future__ import annotations

import json
import socket
import uuid
from datetime import datetime, timezone

from psycopg import Error as PostgresError
from scitex_dev.status import (
    LEDGER_TABLE,
    StatusCode,
    ledger_record,
    new_exchange_id,
)
from scitex_dev.store import NEW_RECORD, Store, plugin_for, resolve_target

__all__ = [
    "DmExchangeError",
    "accepted_status",
    "failure_status",
    "get_exchange",
    "new_exchange",
    "new_client_request_id",
    "open_exchange_store",
    "persist_dm",
    "record_exchange",
    "rotate_failed_notification_exchange",
]


class DmExchangeError(RuntimeError):
    """A DM exchange failed, retaining its safe canonical wire facts."""

    def __init__(self, exchange_id, status, *, message_id=None):
        super().__init__(status.message)
        self.exchange_id = exchange_id
        self.status = status
        self.message_id = message_id


def new_client_request_id() -> str:
    """Mint the caller-owned retry identity, distinct from responder xch_."""
    return f"req_{uuid.uuid4().hex}"


def new_exchange() -> tuple[str, str]:
    """Mint the exchange id and its immutable opening timestamp."""
    return new_exchange_id(), datetime.now(timezone.utc).isoformat()


def open_exchange_store(sender: str) -> Store:
    """Open scitex-dev's canonical exchange ledger, with no local fallback."""
    plugin = plugin_for(LEDGER_TABLE)
    return Store(
        resolve_target(plugin),
        plugin.schema,
        node=socket.gethostname(),
        writer_policy=plugin.writer_policy,
        actor=sender,
    )


def accepted_status(exchange_id: str, message_id: str) -> StatusCode:
    """The immediate, non-final status after the DM row commits."""
    return StatusCode(
        kind="http",
        code=202,
        message=(
            f"OBSERVED: DM {message_id} and its recipient notification "
            f"persisted in canonical PostgreSQL as exchange {exchange_id}. "
            "NOT ESTABLISHED: live visibility or "
            "recipient acknowledgement. NEXT: poll "
            f"`scitex-cards dm get-status {exchange_id} --json`."
        ),
    )


def failure_status(
    exc: Exception,
    exchange_id: str | None,
    *,
    persistence_known_absent: bool = False,
    stage: str = "persist",
) -> StatusCode:
    """Map observed failures onto native HTTP codes without exposing secrets."""
    from scitex_dev.store import StoreError

    from ._backend import BackendUnavailableError
    from ._messaging import AgentIdentityUnresolved
    from ._store_target import StoreTargetNotConfigured

    if isinstance(exc, AgentIdentityUnresolved):
        code = 400
        persistence_known = True
        observation = "sender identity was unresolved; the DM API was not called"
        action = (
            "set SCITEX_CARDS_AGENT_ID=<your-agent> or pass --sender, then rerun "
            "`scitex-cards dm send RECIPIENT MESSAGE`"
        )
    elif isinstance(exc, StoreTargetNotConfigured):
        code = 503
        persistence_known = True
        observation = "no canonical PostgreSQL target was configured"
        action = (
            "set SCITEX_STORE_DSN, then inspect it with "
            "`scitex-cards resolve-store` before retrying"
        )
    elif isinstance(exc, ModuleNotFoundError):
        code = 503
        persistence_known = True
        observation = "the PostgreSQL driver was unavailable before the write"
        action = "install `scitex-cards[all]`, then retry the command"
    elif isinstance(exc, (StoreError, PostgresError)):
        code = 503
        persistence_known = persistence_known_absent
        detail = str(exc).lower()
        refused_config = any(
            marker in detail
            for marker in ("auth", "password", "pgbouncer", "configuration")
        )
        if stage == "config":
            observation = (
                "PostgreSQL authentication/configuration refused the shared "
                "status-ledger connection before DM persistence"
                if refused_config
                else "the shared PostgreSQL status ledger could not be opened"
            )
            action = (
                'run `psql "$SCITEX_STORE_DSN" -c \'select '
                "current_database(), pg_is_in_recovery()'`; correct the "
                "DSN/auth route, then run `scitex-cards health`"
            )
        else:
            observation = (
                "the canonical PostgreSQL DM/notification write returned no "
                "durable result"
            )
            action = (
                "run `scitex-cards health`; inspect dm_messages and "
                "notifications by the quoted exchange before sending again"
            )
    elif isinstance(exc, BackendUnavailableError):
        detail = str(exc).lower()
        if "token" in detail or "auth" in detail or "bearer" in detail:
            code = 401
            persistence_known = True
            observation = "the configured hub rejected or lacked credentials"
            action = "run `scitex-cards hub doctor`, re-provision the token, then retry"
        elif "identity" in detail or "x-scitex-agent" in detail:
            code = 400
            persistence_known = True
            observation = "the configured hub received no sender identity"
            action = (
                "set SCITEX_CARDS_AGENT_ID=<your-agent> or pass --sender, then retry"
            )
        else:
            code = 503
            persistence_known = False
            observation = "the configured canonical hub route returned no DM result"
            action = (
                "run `scitex-cards hub doctor`; inspect dm_messages before retrying"
            )
    elif isinstance(exc, ValueError):
        code = 400
        persistence_known = True
        observation = "sender, recipient, or message validation was refused"
        action = "supply non-empty values, then rerun `scitex-cards dm send --help`"
    else:
        code = 500
        persistence_known = persistence_known_absent
        observation = (
            "the shared PostgreSQL ledger could not be opened"
            if persistence_known_absent
            else "the DM API returned no durable message id"
        )
        action = (
            "verify SCITEX_STORE_DSN names the writable "
            "canonical PostgreSQL, then run `scitex-cards health`"
            if persistence_known_absent
            else "run `scitex-cards health`; inspect dm_messages before retrying"
        )
    exchange_label = f" for exchange {exchange_id}" if exchange_id else ""
    persistence = (
        f"No DM was persisted{exchange_label}."
        if persistence_known
        else (
            "NOT ESTABLISHED: whether the request produced a durable DM"
            f"{exchange_label}."
        )
    )
    return StatusCode(
        kind="http",
        code=code,
        message=(f"OBSERVED: {observation}. {persistence} NEXT: {action}."),
    )


def record_exchange(
    store: Store,
    *,
    exchange_id: str,
    opened_at: str,
    sender: str,
    recipient: str,
    status: StatusCode,
) -> None:
    """Append the exchange's first status to scitex-dev's shared ledger."""
    row = ledger_record(
        exchange_id=exchange_id,
        initiator=sender,
        responder=recipient,
        operation="cards.dm.delivery",
        status=status,
        opened_at=opened_at,
    )
    store.put(row, expected_revision=NEW_RECORD, owner=recipient, actor=sender)


def rotate_failed_notification_exchange(
    notification: dict,
    *,
    recipient: str,
    store=None,
) -> dict:
    """Atomically replace one terminal-failed notification exchange.

    The notification row and the status ledger deliberately live in the same
    canonical PostgreSQL database.  Locking the notification and appending the
    successor exchange through ``Store`` in one transaction makes concurrent
    pollers converge on one new id.  The failed predecessor remains immutable
    in the ledger as audit evidence.  Non-final (including HTTP 102) and final
    successful exchanges are returned unchanged.
    """
    exchange_id = notification.get("exchange_id")
    notification_id = notification.get("id")
    sender = notification.get("actor")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (exchange_id, notification_id, sender, recipient)
    ):
        return notification

    from psycopg import sql

    from ._inbox_postgres import _connect as connect_inbox
    from ._inbox_postgres import _row_by_name

    with connect_inbox(store) as inbox_connection:
        inbox_location_row = inbox_connection.execute(
            "SELECT current_database() AS database, current_schema() AS schema"
        ).fetchone()
        inbox_location = _row_by_name(
            inbox_location_row,
            ("database", "schema"),
        )
        inbox_database = inbox_location["database"]
        inbox_schema = inbox_location["schema"]
    with open_exchange_store(sender) as ledger:
        with ledger.batch():
            # Cross-record atomicity is intentional here: Store owns the
            # canonical transaction, while Cards owns the notification row.
            # There is no second connection and therefore no crash window
            # between publishing the successor and pointing delivery at it.
            connection = ledger._connection
            ledger_database = connection.execute(
                "SELECT current_database() AS database"
            ).fetchone()["database"]
            if ledger_database != inbox_database:
                raise RuntimeError(
                    "Cards notifications and the canonical status ledger are in "
                    "different PostgreSQL databases; refusing a non-atomic exchange "
                    "rotation. Point SCITEX_STORE_DSN at the "
                    "same database, then retry the unconfirmed notification."
                )
            notification_table = sql.Identifier(inbox_schema, "notifications")
            locked = connection.execute(
                sql.SQL(
                    "SELECT exchange_id, record_json, recipient_id, actor "
                    "FROM {} WHERE id = %s FOR UPDATE"
                ).format(notification_table),
                (notification_id,),
            ).fetchone()
            if locked is None:
                raise DmExchangeError(
                    exchange_id,
                    failure_status(RuntimeError("notification absent"), exchange_id),
                )
            if locked["recipient_id"] != recipient:
                raise DmExchangeError(
                    exchange_id,
                    failure_status(
                        RuntimeError("notification recipient mismatch"), exchange_id
                    ),
                )
            if locked["actor"] != sender:
                raise DmExchangeError(
                    exchange_id,
                    failure_status(
                        RuntimeError("notification sender mismatch"), exchange_id
                    ),
                )
            current_id = str(locked["exchange_id"] or "")
            current = ledger.get({"exchange_id": current_id})
            if current is None:
                raise DmExchangeError(
                    current_id or exchange_id,
                    persisted_without_exchange_status(
                        current_id or exchange_id,
                        str(notification.get("msg_id") or "?"),
                    ),
                )
            values = dict(current.values)
            if values.get("operation") != "cards.dm.delivery":
                raise DmExchangeError(
                    current_id,
                    failure_status(
                        RuntimeError("exchange operation mismatch"), current_id
                    ),
                )
            if values.get("initiator") != sender:
                raise DmExchangeError(
                    current_id,
                    failure_status(
                        RuntimeError("exchange initiator mismatch"), current_id
                    ),
                )
            if values.get("responder") != recipient:
                raise DmExchangeError(
                    current_id,
                    failure_status(
                        RuntimeError("exchange responder mismatch"), current_id
                    ),
                )
            current_status = StatusCode(
                kind=values["kind"],
                code=values["code"],
                message=values["message"],
            )
            if not current_status.final or current_status.ok:
                return {**notification, "exchange_id": current_id}

            successor_id, opened_at = new_exchange()
            successor_status = StatusCode(
                kind="http",
                code=202,
                message=(
                    f"OBSERVED: retry delivery {notification_id} replaced final-failed "
                    f"exchange {current_id} with {successor_id}. NOT ESTABLISHED: "
                    "live visibility or recipient acknowledgement. NEXT: poll "
                    f"`scitex-cards dm get-status {successor_id} --json`."
                ),
            )
            record_exchange(
                ledger,
                exchange_id=successor_id,
                opened_at=opened_at,
                sender=values["initiator"],
                recipient=values["responder"],
                status=successor_status,
            )
            stored = json.loads(locked["record_json"])
            if not isinstance(stored, dict):
                raise TypeError("notification record_json is not an object")
            stored["exchange_id"] = successor_id
            changed = connection.execute(
                sql.SQL(
                    "UPDATE {} SET exchange_id = %s, record_json = %s "
                    "WHERE id = %s AND exchange_id = %s"
                ).format(notification_table),
                (successor_id, json.dumps(stored), notification_id, current_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError(
                    "notification exchange changed despite its FOR UPDATE lock"
                )
    return {**notification, "exchange_id": successor_id}


def persist_dm(
    sender: str,
    recipient: str,
    body: str,
    *,
    store=None,
    client_request_id: str,
) -> dict:
    """Persist one DM, its notification exchange, and the canonical HTTP 202."""
    from . import _threads

    if not isinstance(client_request_id, str) or not client_request_id.strip():
        raise ValueError("dm_send requires a non-empty client_request_id")
    exchange_id, opened_at = new_exchange()
    try:
        ledger = open_exchange_store(sender)
    except Exception as exc:
        status = failure_status(
            exc,
            exchange_id,
            persistence_known_absent=True,
            stage="config",
        )
        raise DmExchangeError(exchange_id, status) from None

    record = None
    try:
        with ledger:
            try:
                record = _threads.append_message(
                    sender,
                    recipient,
                    body,
                    store=store,
                    exchange_id=exchange_id,
                    client_request_id=client_request_id,
                )
            except Exception as exc:
                status = failure_status(exc, exchange_id, stage="persist")
                _record_failure(
                    ledger,
                    exchange_id,
                    opened_at,
                    sender,
                    recipient,
                    status,
                )
                raise DmExchangeError(exchange_id, status) from None
            if record.get("idempotent_replay"):
                original_exchange = record["exchange_id"]
                row = ledger.get({"exchange_id": original_exchange})
                if row is None:
                    status = persisted_without_exchange_status(
                        original_exchange, record["id"]
                    )
                    raise DmExchangeError(
                        original_exchange, status, message_id=record["id"]
                    )
                from ._inbox_postgres import notification_for_exchange

                notification = notification_for_exchange(original_exchange, store=store)
                if notification is None:
                    status = failure_status(
                        RuntimeError("notification absent"), original_exchange
                    )
                    raise DmExchangeError(
                        original_exchange, status, message_id=record["id"]
                    )
                payload = _exchange_payload(row)
                return {
                    **record,
                    "notification_id": notification["id"],
                    "exchange_id": original_exchange,
                    "client_request_id": client_request_id,
                    "status": payload["status"],
                }
            status = accepted_status(exchange_id, record["id"])
            try:
                record_exchange(
                    ledger,
                    exchange_id=exchange_id,
                    opened_at=opened_at,
                    sender=sender,
                    recipient=recipient,
                    status=status,
                )
            except Exception:
                status = persisted_without_exchange_status(exchange_id, record["id"])
                raise DmExchangeError(
                    exchange_id, status, message_id=record["id"]
                ) from None
    except DmExchangeError:
        raise
    except Exception as exc:
        if record is None:
            status = failure_status(
                exc,
                exchange_id,
                persistence_known_absent=True,
                stage="config",
            )
            raise DmExchangeError(exchange_id, status) from None
        status = persisted_without_exchange_status(exchange_id, record["id"])
        raise DmExchangeError(exchange_id, status, message_id=record["id"]) from None
    return {
        **record,
        "exchange_id": exchange_id,
        "client_request_id": client_request_id,
        "status": status.to_dict(),
    }


def _record_failure(ledger, exchange_id, opened_at, sender, recipient, status):
    """Preserve the primary failure even if its ledger append also fails."""
    try:
        record_exchange(
            ledger,
            exchange_id=exchange_id,
            opened_at=opened_at,
            sender=sender,
            recipient=recipient,
            status=status,
        )
    except Exception:
        return


def persisted_without_exchange_status(exchange_id: str, message_id: str):
    """Status for a durable DM whose ledger write was not observed."""
    return StatusCode(
        kind="http",
        code=500,
        message=(
            f"OBSERVED: DM {message_id} persisted, but exchange {exchange_id} "
            "was not observed in the status ledger. NOT ESTABLISHED: live "
            "visibility or acknowledgement. NEXT: run `scitex-cards health`, "
            "inspect dm_messages by the quoted message id, and repair the "
            "status-ledger route before sending again."
        ),
    )


def get_exchange(exchange_id: str, *, sender: str) -> dict | None:
    """Read one exchange as a plain protocol payload."""
    with open_exchange_store(sender) as store:
        row = store.get({"exchange_id": exchange_id})
    if row is None:
        return None
    return _exchange_payload(row)


def _exchange_payload(row) -> dict:
    values = dict(row.values)
    return {
        "exchange_id": values["exchange_id"],
        "initiator": values["initiator"],
        "responder": values["responder"],
        "operation": values["operation"],
        "status": {
            "kind": values["kind"],
            "code": values["code"],
            "message": values["message"],
        },
        "final": values["final"],
        "opened_at": values["opened_at"],
        "updated_at": values["updated_at"],
    }


# EOF
