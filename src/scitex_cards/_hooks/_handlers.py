#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Built-in event handlers for the hook bus.

Split out of the original flat ``_hooks.py`` (C2 refactor). Holds the
idempotent built-in handlers for the ``push`` / ``done`` / ``unblock``
kinds plus their dedupe helpers. Logic is byte-for-byte the original —
C2 changed only the validator + the plugin runner.

There is intentionally NO ``card-event`` built-in handler here: C2
keeps scope to bus mechanics, so a validated ``card-event`` flows
straight to plugins (the built-in card-event handler is C5).
"""

from __future__ import annotations

import scitex_logging as slogging
from typing import Any, Iterable

from .. import _store
from .._git_link import TRIGGER_COMMIT

logger = slogging.getLogger(__name__)


def _handle_push(
    event: dict, *, store: Any | None, entry_points: Iterable | None = None
) -> list[dict]:
    """Built-in `push` handler — idempotently append a comment per card.

    C6 (git-link event producers): on a GENUINELY NEW link (a freshly
    appended comment, NOT an ``already-recorded`` noop) the handler ALSO
    emits a canonical card-event onto the bus — ``committed`` when the
    producer flagged ``trigger="commit"`` (the ``post-commit`` git hook),
    else ``pushed`` (the historical ``pre-push`` behaviour). The emit is
    purely additive: there is intentionally no consumer yet (C4 dispatcher
    is a separate card), so an emit with no plugin registered is a harmless
    noop. The idempotency boundary is shared with the comment write — an
    ``already-recorded`` commit_sha emits NOTHING, so a re-link never
    re-emits.
    """
    out: list[dict] = []
    commit_sha = event["commit_sha"]
    msg = event.get("message") or ""
    author = event.get("author") or "<unknown>"
    repo = event["repo"]
    branch = event.get("branch")
    trigger = event.get("trigger")
    # Include the FULL commit_sha as a stable token (NOT just the
    # short prefix) so the idempotency check below can find it via
    # substring match. The short prefix is for human readability;
    # the full sha is the dedupe key.
    text = (f"[push] {repo} @ {commit_sha[:10]}: {msg} [sha={commit_sha}]").strip()
    for card_id in event["card_ids"]:
        # Idempotency: if any existing comment text mentions this
        # commit_sha, the push has already been recorded — noop.
        if _push_already_recorded(card_id, commit_sha, store=store):
            out.append({"card_id": card_id, "action": "already-recorded"})
            continue
        try:
            _store.comment_task(
                store=store, task_id=card_id, text=text, by=author, kind="push"
            )
            out.append({"card_id": card_id, "action": "comment-appended"})
            # Genuinely NEW link → emit the matching canonical card-event.
            # Fail-soft: a hiccup here must never break the comment write.
            _emit_git_event(
                card_id=card_id,
                repo=repo,
                branch=branch,
                commit_sha=commit_sha,
                author=author,
                trigger=trigger,
                entry_points=entry_points,
            )
        except _store.TaskNotFoundError:
            # An unknown card id is NOT a producer error (the producer
            # just hinted at a card the operator hasn't created yet);
            # we record a soft noop so the producer can spot it.
            out.append({"card_id": card_id, "action": "card-not-found"})
    return out


def _emit_git_event(
    *,
    card_id: str,
    repo: str,
    branch: str | None,
    commit_sha: str,
    author: str | None,
    trigger: str | None,
    entry_points: Iterable | None = None,
) -> None:
    """Emit a ``committed`` / ``pushed`` card-event for a freshly-linked sha.

    Fail-soft: any error building the envelope or reaching the bus is
    swallowed so a git-link run is never broken by event emission. (The
    underlying :func:`scitex_cards._events.emit` already never raises, but
    we wrap the envelope construction defensively too.)
    """
    try:
        from .._events import Event, emit

        if trigger == TRIGGER_COMMIT:
            ev = Event.committed(
                card_id, repo=repo, sha=commit_sha, branch=branch, actor=author
            )
        else:
            ev = Event.pushed(
                card_id,
                repo=repo,
                branch=branch,
                sha=commit_sha,
                actor=author,
            )
        emit(ev, entry_points=entry_points)
    except Exception:  # noqa: BLE001 — emit must never break the producer
        logger.warning(
            "scitex_cards._hooks: git-link event emit failed for "
            "card_id=%r trigger=%r",
            card_id,
            trigger,
            exc_info=True,
        )


def _handle_done(event: dict, *, store: Any | None) -> list[dict]:
    """Built-in `done` handler — idempotent done + pr_url stamp per card."""
    out: list[dict] = []
    pr_url = event["pr_url"]
    by = event.get("author") or "<unknown>"
    for card_id in event["card_ids"]:
        try:
            existing = _store.get_task(store=store, task_id=card_id)
        except (AttributeError, _store.TaskNotFoundError):
            existing = None
        if existing is None:
            out.append({"card_id": card_id, "action": "card-not-found"})
            continue
        # Idempotency: already done AND already carrying this pr_url
        # → noop. Other states are flipped through (handle "race" with
        # operator-manual done that didn't carry pr_url).
        if existing.get("status") == "done" and existing.get("pr_url") == pr_url:
            out.append({"card_id": card_id, "action": "noop"})
            continue
        try:
            _store.update_task(store=store, task_id=card_id, pr_url=pr_url)
            _store.complete_task(store=store, task_id=card_id, by=by)
            out.append({"card_id": card_id, "action": "completed"})
        except _store.TaskNotFoundError:
            out.append({"card_id": card_id, "action": "card-not-found"})
    return out


def _handle_unblock(event: dict, *, store: Any | None) -> list[dict]:
    """Built-in `unblock` handler — idempotent `[unblocked]` comment per card.

    Records on each newly-runnable dependent that ``unlocker_id`` (the
    card that just finished) cleared its last blocking dependency. The
    actual *notification* of the dependent's assignee + subscribers is a
    consumer concern (SAC's plugin); this only writes the durable trail
    so the board shows why a card became runnable even with no consumer.
    """
    out: list[dict] = []
    unlocker_id = event["unlocker_id"]
    by = event.get("author") or "<unknown>"
    # The unlocker id is the dedupe token — re-emitting the same unblock
    # (e.g. a `done` event replayed) must not append duplicate comments.
    token = f"[unblocked by {unlocker_id}]"
    for card_id in event["card_ids"]:
        if _comment_token_present(card_id, token, store=store):
            out.append({"card_id": card_id, "action": "already-recorded"})
            continue
        try:
            _store.comment_task(
                store=store, task_id=card_id, text=token, by=by, kind="unblock"
            )
            out.append({"card_id": card_id, "action": "comment-appended"})
        except _store.TaskNotFoundError:
            out.append({"card_id": card_id, "action": "card-not-found"})
    return out


def _comment_token_present(
    card_id: str,
    token: str,
    *,
    store: Any | None,
) -> bool:
    """True iff some existing comment on ``card_id`` contains ``token``."""
    try:
        existing = _store.get_task(store=store, task_id=card_id)
    except (AttributeError, _store.TaskNotFoundError):
        return False
    if existing is None:
        return False
    for c in existing.get("comments") or ():
        if not isinstance(c, dict):
            continue
        if token in (c.get("text") or ""):
            return True
    return False


def _push_already_recorded(
    card_id: str,
    commit_sha: str,
    *,
    store: Any | None,
) -> bool:
    try:
        existing = _store.get_task(store=store, task_id=card_id)
    except (AttributeError, _store.TaskNotFoundError):
        return False
    if existing is None:
        return False
    for c in existing.get("comments") or ():
        if not isinstance(c, dict):
            continue
        text = c.get("text") or ""
        if commit_sha in text:
            return True
    return False


# EOF
