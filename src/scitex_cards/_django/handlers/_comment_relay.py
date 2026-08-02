#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comment-delivery toast — describe where a board comment was QUEUED.

Extracted from :mod:`crud` (the file hit the line cap) so the comment-
delivery concern stays a small, self-contained module. Called by
``handle_comment`` AFTER the comment is persisted and the canonical
``commented`` card-event is emitted.

## Why this is no longer a direct POST (operator P1, 2026-06-26)

This module USED to host ``maybe_relay_comment``, which delivered a
comment by a SYNCHRONOUS HTTP POST to the owner's ``/v1/turn`` turn-URL.
That POST could NEVER reach a *containerized* owner — the agent
subscribes outbound to a bus and refuses a direct inbound POST
(connection refused) — so a comment never arrived. It also put a
slow/unreachable network call on the critical path of the comment write.

Comments now flow through the standalone PULL-inbox like every other
card-event: :func:`scitex_cards._store.comment_task` emits ``commented``,
and the C4 dispatcher (:func:`scitex_cards._notify.dispatch_notifications`)
ENQUEUES that notification into each resolved recipient's inbox (owner +
collaborators + subscribers per the C3 default, minus the actor). The
recipient PULLs it via the ``poll_notifications`` MCP tool — the
always-works rail, no network on the write path.

:func:`comment_inbox_toast` recomputes that SAME recipient set with the
SSOT the dispatcher uses (:func:`scitex_cards._notify.resolve_recipients`),
resolves each id to a human NAME, and returns a dict the ``/comment`` JSON
carries under ``relay`` so the board JS can toast "queued to N
recipient(s)" instead of the old connection error.

NB: the polling ``scitex-todo.wake-watcher`` may ALSO POST the owner's
``/v1/turn`` when it sees ``len(comments)`` grow. That background path is
out of scope here; it likely also cannot reach a containerized owner (so
it is harmless), and it is a redundant best-effort accelerator — not a
double of the inbox delivery the recipient actually reads.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _recipient_names(task: dict, author: str, *, store=None) -> list[str]:
    """Resolve the comment's inbox recipients to display NAMES (actor dropped).

    Mirrors the dispatcher: the C3 ``commented`` recipient set (owner +
    collaborators + subscribers by default) resolved on the card, minus the
    actor (the author is never notified of their own comment), with each id
    mapped back to a human name (or the raw-name fallback verbatim).

    ``store`` MUST be the board's store path — the SAME store the comment was
    written to and the C4 dispatcher enqueued against. Passing ``None`` would
    resolve users against the default-precedence store (the large canonical
    store), which is both WRONG (different user/notify-prefs set) and SLOW
    (re-resolving a 100s-of-card store on the comment's critical path — the
    cause of the multi-second board-comment stall).
    """
    from ..._notify import resolve_recipients
    from ..._users import get_user, resolve_user

    recipients = set(resolve_recipients({"type": "commented"}, task, store=store))
    # Drop the actor exactly as dispatch_notifications does.
    actor_user = resolve_user(author, store=store)
    recipients.discard(actor_user.id if actor_user is not None else author)
    recipients.discard(author)

    names: list[str] = []
    for uid in recipients:
        user = get_user(uid, store=store)
        if user is None:
            # Unregistered raw-name fallback — it IS already the name.
            if uid:
                names.append(uid)
            continue
        name = next(
            (n for n in (user.names or ()) if isinstance(n, str) and n),
            user.host_at_name or None,
        )
        if name:
            names.append(name)
    return sorted(set(names))


def comment_inbox_toast(task: dict, author: str, *, store=None) -> dict:
    """Build the ``relay`` toast describing the comment's INBOX QUEUE.

    Returns a dict the ``/comment`` JSON response includes under ``relay`` so
    the board JS can render a toast::

        {"sent": True, "wire": "inbox", "queued": [<name>, ...],
         "target": "<owner-name>"}

    ``target`` is the card OWNER (:func:`scitex_cards._owner.card_owner`,
    ``agent`` falling back to ``assignee``); ``queued`` is every recipient the
    ``commented`` notification was enqueued to (owner + collaborators +
    subscribers, minus the author). A self-comment naturally yields an empty
    ``queued`` (the author is the only recipient and is dropped) — that is the
    correct "nobody else to notify" signal, NOT a failure.

    ``store`` MUST be the board's store path (what the comment was written to);
    see :func:`_recipient_names` for why ``None`` is both wrong and slow.

    FAIL-SOFT: the comment is ALREADY on disk and ALREADY enqueued by the
    emit, so any resolver hiccup degrades to ``queued: []`` and is logged —
    this NEVER raises and NEVER fails the write.
    """
    from ..._owner import card_owner

    target = card_owner(task) or ""
    try:
        queued = _recipient_names(task, author, store=store)
    except Exception:  # noqa: BLE001 — toast is best-effort, never fails write
        logger.warning(
            "[scitex-todo] comment toast recipient-resolve failed for %r",
            task.get("id"),
            exc_info=True,
        )
        queued = []
    logger.info(
        "[scitex-todo] comment on %s queued to inbox for %s (target=%s)",
        task.get("id"),
        queued,
        target,
    )
    return {"sent": True, "wire": "inbox", "queued": queued, "target": target}


__all__ = ["comment_inbox_toast"]

# EOF
