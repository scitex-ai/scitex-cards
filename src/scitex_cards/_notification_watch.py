#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public PostgreSQL notification doorbell: hints only, durable poll after."""

from __future__ import annotations

import os
import secrets
import time
from typing import Any

from scitex_dev.status import StatusCode

from ._inbox_postgres import NOTIFICATION_CHANNEL, _connect, resolve_dsn

__all__ = ["NotificationWatchUnavailable", "watch_notifications"]

_NOTIFY_DSN_ENV = "SCITEX_CARDS_NOTIFY_DSN"
_PROBE_CHANNEL = "scitex_cards_notification_probe"
_PROBE_TIMEOUT_S = 1.0
_FAILED_PROBE_RETRY_S = 60.0
_PROBE_RESULTS: dict[tuple[str, str], tuple[float, StatusCode | None]] = {}


class NotificationWatchUnavailable(RuntimeError):
    """The shared PostgreSQL LISTEN door could not be opened."""

    def __init__(self, status: StatusCode):
        super().__init__(status.message)
        self.status = status


def resolve_notification_dsn(store: Any = None) -> str:
    """Resolve the session-capable LISTEN endpoint without guessing a port."""
    configured = os.environ.get(_NOTIFY_DSN_ENV, "").strip()
    if configured:
        if configured.startswith(("postgres://", "postgresql://")):
            return configured
        raise ValueError(f"{_NOTIFY_DSN_ENV} must be a PostgreSQL DSN")
    return resolve_dsn(store)


def _doorbell_status(listener_dsn: str, writer_dsn: str) -> StatusCode | None:
    """Prove that a second connection can ring this LISTEN connection."""
    key = (listener_dsn, writer_dsn)
    cached = _PROBE_RESULTS.get(key)
    now = time.monotonic()
    if cached is not None:
        checked_at, status = cached
        if status is None or now - checked_at < _FAILED_PROBE_RETRY_S:
            return status
    token = secrets.token_hex(12)
    listener = _connect(listener_dsn)
    try:
        listener.execute(f"LISTEN {_PROBE_CHANNEL}")
        listener.commit()
        with _connect(writer_dsn) as writer:
            writer.execute("SELECT pg_notify(%s, %s)", (_PROBE_CHANNEL, token))
            writer.commit()
        deadline = time.monotonic() + _PROBE_TIMEOUT_S
        observed = False
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            notice = next(listener.notifies(timeout=remaining), None)
            if notice is None:
                break
            if notice.payload == token:
                observed = True
                break
    finally:
        listener.close()
    status = None
    if not observed:
        status = StatusCode(
            kind="http",
            code=503,
            message=(
                "OBSERVED: the configured PostgreSQL endpoint accepted LISTEN "
                "but a transactional cross-connection pg_notify did not arrive. "
                "This is the measured behaviour of transaction-pooled endpoints. "
                f"NEXT: set {_NOTIFY_DSN_ENV} to an explicit direct or session-pooled "
                "PostgreSQL DSN; do not infer it from the data-port number. Durable "
                "poll_notifications remains the delivery guarantee."
            ),
        )
    _PROBE_RESULTS[key] = (now, status)
    return status


class _NotificationWatch:
    """A ready-on-return iterator over recipient-specific doorbell hints."""

    def __init__(self, recipient_ids: set[str], *, timeout: float | None, store: Any):
        self._recipient_ids = recipient_ids
        try:
            writer_dsn = resolve_dsn(store)
            listener_dsn = resolve_notification_dsn(store)
            status = _doorbell_status(listener_dsn, writer_dsn)
            if status is not None:
                raise NotificationWatchUnavailable(status)
            self._conn = _connect(listener_dsn)
            self._conn.execute(f"LISTEN {NOTIFICATION_CHANNEL}")
            self._conn.commit()
            self._notices = self._conn.notifies(timeout=timeout)
        except NotificationWatchUnavailable:
            raise
        except Exception as exc:
            raise NotificationWatchUnavailable(
                StatusCode(
                    kind="http",
                    code=503,
                    message=(
                        "OBSERVED: the PostgreSQL notification doorbell could "
                        "not be opened. NEXT: run `scitex-cards health`; keep "
                        "durable poll_notifications as the reconciling sweep."
                    ),
                )
            ) from exc

    def __iter__(self):
        return self

    def __next__(self) -> dict:
        for notice in self._notices:
            if notice.payload not in self._recipient_ids:
                continue
            return {
                "recipient_id": notice.payload,
                "hint": "poll_notifications",
                "status": StatusCode(
                    kind="http",
                    code=202,
                    message=(
                        "OBSERVED: a recipient-scoped doorbell arrived; it "
                        "carries no notification data. NEXT: call "
                        "`scitex_cards.poll_notifications(agent)` and coalesce "
                        "all returned rows into the recipient's current drain."
                    ),
                ).to_dict(),
            }
        raise StopIteration

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def watch_notifications(
    agent: str,
    *,
    timeout: float | None = None,
    store: Any = None,
):
    """Return a ready LISTEN iterator; each hint means poll the durable inbox.

    The hint carries only the recipient identity, never notification data.
    PostgreSQL does not retain hints for disconnected listeners, so callers
    MUST keep a bounded :func:`poll_notifications` reconciling sweep.
    """
    if not isinstance(agent, str) or not agent.strip():
        raise ValueError("watch_notifications requires a non-empty agent")
    from ._inbox_confirm import recipient_keys

    return _NotificationWatch(
        set(recipient_keys(agent, store)), timeout=timeout, store=store
    )


# EOF
