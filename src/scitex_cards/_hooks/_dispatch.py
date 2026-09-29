#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Top-level event dispatch for the hook bus.

Split out of the original flat ``_hooks.py`` (C2 refactor). Wires the
built-in per-kind handler (:mod:`._handlers`) + the bounded entry-point
plugin runner (:mod:`._plugins`) and returns the summary dict that the
HTTP / CLI wrappers send back to producers.

The return-summary SHAPE is unchanged from the original module — callers
+ HTTP/CLI responses depend on it.
"""

from __future__ import annotations

from typing import Any, Iterable

from .._events import CARD_EVENT_KIND
from ._handlers import _handle_done, _handle_push, _handle_unblock
from ._plugins import _run_plugins


def dispatch_event(
    event: dict,
    *,
    store: Any | None = None,
    entry_points: Iterable | None = None,
) -> dict:
    """Run the built-in handler + every entry-point plugin for ``event``.

    Returns a summary dict::

        {
            "kind": <event kind>,
            "card_writes": [
                {"card_id", "action": "comment-appended"|"already-recorded"|"completed"|"noop"},
                ...
            ],
            "plugin_count": <N>,
            "plugin_errors": [
                {"plugin": <name>, "error": <str>},
                ...
            ]
        }

    The return shape is the HTTP / CLI response body.

    Parameters
    ----------
    event : dict
        Already-validated event (call :func:`event_validate` first).
    store : Path-like, optional
        Override the task-store path; ``None`` resolves via the
        normal precedence chain.
    entry_points : iterable, optional
        Explicit set of plugin entry points to run instead of the ones
        discovered from packaging metadata. Each item must be entry-
        point-shaped: a ``.name`` attribute and a ``.load()`` method
        returning the handler callable. ``None`` (the default) reads the
        real ``scitex_cards.hooks`` group. This is the in-process
        injection seam (mirrors scitex-dev's
        ``load_plugins(entry_points_iter=...)``): in-process producers
        that can't ship packaging metadata, and tests that need a real
        fake handler, pass a concrete list here — no monkeypatch of
        ``importlib.metadata`` required (PA-306-compliant).
    """
    kind = event["kind"]
    card_writes: list[dict] = []
    notify_summary: dict | None = None
    if kind == "push":
        # `entry_points` is forwarded so the C6 git-link card-events the
        # push handler emits (committed/pushed) route through the SAME
        # injected plugin set the dispatcher uses — letting tests capture
        # them via the documented in-process seam (no monkeypatch).
        card_writes = _handle_push(event, store=store, entry_points=entry_points)
    elif kind == "done":
        card_writes = _handle_done(event, store=store)
    elif kind == "unblock":
        card_writes = _handle_unblock(event, store=store)
    elif kind == CARD_EVENT_KIND:
        # C4: the built-in NOTIFY consumer for canonical card-events.
        # Resolve recipients (C3) and deliver per-recipient via the push
        # wire — no fleet-spam. Lazy-imported INSIDE the function (not at
        # module top) to avoid an import cycle: `_hooks` -> `_notify._dispatch`
        # -> `_users` / `_store`; this mirrors how `_store` lazy-imports the
        # bus for its card-message emit. Wrapped FAIL-SOFT so a delivery
        # hiccup can NEVER make `dispatch_event` (and therefore `emit()`)
        # raise — `emit()` must stay non-raising for every producer.
        try:
            from .._notify._dispatch import dispatch_notifications

            notify_summary = dispatch_notifications(event, store=store)
        except Exception:  # noqa: BLE001 — notify must never break the producer
            import scitex_logging as slogging

            slogging.getLogger(__name__).warning(
                "scitex_cards._hooks: card-event notify dispatch failed",
                exc_info=True,
            )

    plugin_count, plugin_errors = _run_plugins(event, entry_points=entry_points)
    summary: dict = {
        "kind": kind,
        "card_writes": card_writes,
        "plugin_count": plugin_count,
        "plugin_errors": plugin_errors,
    }
    # Additive: expose the C4 notify result under a `notify` key WITHOUT
    # changing the existing summary keys (callers / HTTP / CLI depend on the
    # original shape). Present only for card-events that ran the consumer.
    if notify_summary is not None:
        summary["notify"] = notify_summary
    return summary


# EOF
