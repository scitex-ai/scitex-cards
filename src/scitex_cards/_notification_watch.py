#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public PostgreSQL notification doorbell: hints only, durable poll after."""

from __future__ import annotations

from typing import Any

from scitex_dev.status import StatusCode

from ._inbox_postgres import NOTIFICATION_CHANNEL, _connect

__all__ = ["NotificationWatchUnavailable", "watch_notifications"]


class NotificationWatchUnavailable(RuntimeError):
    """The shared PostgreSQL LISTEN door could not be opened."""

    def __init__(self, status: StatusCode):
        super().__init__(status.message)
        self.status = status


class _NotificationWatch:
    """A ready-on-return iterator over recipient-specific doorbell hints."""

    def __init__(self, recipient_ids: set[str], *, timeout: float | None, store: Any):
        self._recipient_ids = recipient_ids
        try:
            self._conn = _connect(store)
            self._conn.execute(f"LISTEN {NOTIFICATION_CHANNEL}")
            self._conn.commit()
            self._notices = self._conn.notifies(timeout=timeout)
        except Exception as exc:
            raise NotificationWatchUnavailable(
                StatusCode(
                    kind="http",
                    code=503,
                    message=(
                        "OBSERVED: the PostgreSQL notification doorbell could "
                        "not be opened. NEXT: run `scitex-cards health`; keep "
                        "durable poll_notifications as the jittered fallback."
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
    MUST keep a long, jittered :func:`poll_notifications` fallback.
    """
    if not isinstance(agent, str) or not agent.strip():
        raise ValueError("watch_notifications requires a non-empty agent")
    from ._inbox_confirm import recipient_keys

    return _NotificationWatch(
        set(recipient_keys(agent, store)), timeout=timeout, store=store
    )


# EOF
