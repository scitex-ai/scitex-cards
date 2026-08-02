#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``comment_task`` — the card's Issue-activity log (append-only).

Split out of ``_store`` (PURE MOVE — no behaviour change), which re-exports
``comment_task`` so ``from ._store import comment_task`` keeps working. It sits
in its OWN module rather than with the lifecycle verbs because the two together
exceed the 512-line cap: a comment is not a state transition — it is the card's
conversation, and it carries its own fan-out (the ``card-message`` bus dispatch
with the owner / collaborators / subscribers snapshot, PLUS the canonical
``commented`` card-event).

The shared helpers (``_read_write_doc`` / ``_utc_now_iso`` / ``_default_agent``
/ ``TaskNotFoundError``) stay in ``_store`` and are imported inside the function
body — deferred, because ``_store`` imports this module at module level to
re-export the verb and a top-level import back would cycle.
"""

from __future__ import annotations

from pathlib import Path

from ._store_events import _emit_card_event
from ._store_list import _resolved_store


def comment_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    text: str | None = None,
    by: str | None = None,
    kind: str | None = None,
    entry_points=None,
) -> dict:
    """Append an entry to ``task.comments[]`` (the established Issue-
    activity-log shape from skill 30, Gitea-compatible field).

    `by` overrides the $SCITEX_TODO_AGENT_ID → $USER precedence used by
    add_task / complete_task.

    `kind` is an optional feedback-ring / event tag (e.g. ``push`` /
    ``done`` / ``card-message``) stamped onto the entry so the board can
    render "how the card was routed" (operator 2026-06-17). Lenient: the
    model only requires ``text``, so the extra key round-trips cleanly.

    `entry_points` is forwarded to :func:`scitex_cards._hooks.dispatch_event`
    for the ``card-message`` bus emit below: an explicit iterable of
    entry-point-shaped objects to receive the event instead of the ones
    discovered from packaging metadata. ``None`` (the default) uses the
    real installed plugins. This is the in-process injection seam used by
    in-process consumers and by no-mock tests (PA-306-compliant) that
    observe the emitted event via a real fake handler.
    """
    from . import _model, _task
    from ._store import TaskNotFoundError, _default_agent, _read_write_doc, _utc_now_iso

    tasks_path = _resolved_store(store)
    if not task_id:
        raise ValueError("comment_task: 'task_id' is required")
    if not text or not str(text).strip():
        raise ValueError("comment_task: 'text' is required")
    author = _default_agent(by)
    entry = {
        "author": author,
        "ts": _utc_now_iso(),
        "text": str(text),
    }
    if kind:
        entry["kind"] = str(kind)
    with _model._store_lock(tasks_path):
        doc, tasks = _read_write_doc(tasks_path)
        # See `_task._is_tombstoned`: a deleted card's row is retained
        # forever but must behave as ABSENT here.
        target = _task._find_live_task(tasks, task_id)
        if target is None:
            raise TaskNotFoundError(f"task id {task_id!r} not found in {tasks_path}")
        comments = target.setdefault("comments", [])
        # Pre-append snapshot of comment authors — forms the
        # `collaborators` list of the card-message event below.
        prior_authors = [
            c.get("author")
            for c in comments
            if isinstance(c, dict) and isinstance(c.get("author"), str)
        ]
        comments.append(entry)
        # A comment IS activity. Without this stamp, an actively-discussed
        # card reads as "untouched" to every staleness signal (idle_guard,
        # list-stale, digests) — found 2026-07-10 when the idle guard kept
        # flagging a card that had received progress comments minutes earlier.
        target["last_activity"] = entry["ts"]
        _model._save_doc_unlocked(doc, tasks_path, tasks=tasks)
        owner = target.get("agent") or target.get("assignee")
        # Persistent role lists (ADR-0009) — captured under the lock so
        # the bus emit below works off a consistent snapshot.
        persistent_collaborators = [
            c for c in (target.get("collaborators") or []) if isinstance(c, str) and c
        ]
        persistent_subscribers = [
            s for s in (target.get("subscribers") or []) if isinstance(s, str) and s
        ]

    # card-message bus emit (lead a2a `1e8e33d0`, 2026-06-14) — done
    # OUTSIDE the file lock so a slow bus handler can't extend the
    # lock-hold and starve other writers. Comment is already on disk;
    # bus errors are caught + logged so an external handler failure
    # (e.g. SAC unreachable) never bubbles up to the producer.
    try:
        from . import _hooks

        collaborators: list[str] = []
        seen: set[str] = set()
        if owner:
            seen.add(owner)
        seen.add(author)
        for a in list(prior_authors) + persistent_collaborators:
            if a and a not in seen:
                collaborators.append(a)
                seen.add(a)

        # Effective notify list (ADR-0009): the card's explicit
        # subscribers if any, else default to owner + collaborators.
        # P2's consumer fans the card-message to these. (Creator-auto-
        # subscribe is a later phase — needs an author param on add_task.)
        subscribers: list[str] = []
        sub_seen: set[str] = set()
        candidate_subs = persistent_subscribers or (
            ([owner] if owner else []) + collaborators
        )
        for s in candidate_subs:
            if s and s not in sub_seen:
                subscribers.append(s)
                sub_seen.add(s)

        _hooks.dispatch_event(
            {
                "kind": "card-message",
                "card_id": task_id,
                "author": author,
                "body": str(text),
                "owner": owner,
                "collaborators": collaborators,
                "subscribers": subscribers,
                "created_at": entry["ts"],
            },
            entry_points=entry_points,
        )
    except Exception:  # noqa: BLE001 — bus must not break comment_task
        import logging

        logging.getLogger(__name__).warning(
            "comment_task: card-message bus dispatch failed for %r",
            task_id,
            exc_info=True,
        )
    # C5: ALSO emit the canonical `commented` card-event — the foundation
    # path, IN ADDITION to the legacy `card-message` dispatch above (NOT a
    # replacement; any double-notify is C4's dedup concern). Fail-soft,
    # post-persist; reuses the comment's own ts so a downstream timeline
    # can correlate. `extra` carries the comment body (no stable comment-
    # id exists on the entry shape, so body is the available payload).
    # (hook-bypass: line-limit)
    _emit_card_event(
        "commented",
        task_id,
        actor=author,
        ts=entry["ts"],
        extra={"body": entry["text"]},
        store=tasks_path,  # hook-bypass: line-limit
        entry_points=entry_points,
    )
    # Liveness heartbeat: the comment author just touched the store.
    # Fail-soft; reuses the already-resolved actor (no second identity path).
    from ._liveness import _heartbeat

    _heartbeat(author, tasks_path)
    return {"task_id": task_id, "comment": entry}


__all__ = ["comment_task"]

# EOF
