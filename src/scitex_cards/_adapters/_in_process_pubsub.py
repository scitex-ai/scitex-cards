#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Default :class:`NotificationPort` — in-process callback registry.

Suitable for a single-host single-process board. The fleet's a2a/
channel-bus adapter (out of package) replaces this with cross-host
push. Standalone installs need nothing more than this default.

Channel glob semantics (default impl scope, minimal):

- literal match: ``"scitex-cards:task:scitex-cards/quality-hygiene"`` matches itself only.
- suffix-``*`` glob: ``"scitex-cards:task:scitex-cards/*"`` matches every task
  in the ``scitex-cards`` project.
- bare ``"*"`` matches everything.

More elaborate glob/pattern matching is the fleet adapter's
responsibility (e.g. routing via an external channel router).
"""

from __future__ import annotations

import fnmatch
from collections import defaultdict
from typing import Any, Callable


class InProcessPubSub:
    """In-process publish/subscribe with minimal glob support.

    Implements :class:`scitex_cards._ports.NotificationPort`.

    Thread-safety: NOT thread-safe (single-process callback registry).
    Subscribers are invoked synchronously in registration order on the
    publisher's stack. Exceptions raised by handlers are logged via
    ``warnings.warn`` (per scitex coding rule: never silently swallow
    in a delivery loop) and do not stop other handlers from running.

    Examples
    --------
    >>> bus = InProcessPubSub()
    >>> seen = []
    >>> bus.subscribe("scitex-cards:task:demo/*", seen.append)
    >>> bus.publish("scitex-cards:task:demo/foo", {"task_id": "demo/foo", "changes": {}})
    >>> seen[0]["task_id"]
    'demo/foo'
    """

    def __init__(self) -> None:
        # pattern → list[handler]. Ordered insertion = ordered delivery.
        self._subs: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        """Dispatch payload to every subscriber whose pattern matches."""
        import warnings

        for pattern, handlers in list(self._subs.items()):
            if fnmatch.fnmatchcase(channel, pattern):
                for h in handlers:
                    try:
                        h(payload)
                    except Exception as e:  # noqa: BLE001 — see docstring
                        warnings.warn(
                            f"InProcessPubSub: handler {h!r} raised on "
                            f"channel {channel!r}: {e!r}",
                            stacklevel=2,
                        )

    def subscribe(
        self, channel: str, handler: Callable[[dict[str, Any]], None]
    ) -> None:
        """Register handler for the channel pattern.

        Idempotent on (channel, handler) — duplicate subscribes are
        silently coalesced so the same handler is invoked at most once
        per event.
        """
        existing = self._subs[channel]
        if handler not in existing:
            existing.append(handler)
