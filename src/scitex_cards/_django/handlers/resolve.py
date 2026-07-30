#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve handler — the BLOCKING-YOU panel's GUI→code loop (ADR-0006).

Extracted from ``crud.py`` to keep that module under the 512-line file-
size threshold AND to give ``tests/scitex_cards/_django/handlers/
test_resolve.py`` a matching src file (PS-204 mirror conformance).

The handler implements the operator-facing "Resolve" flow: clicking
Resolve on a BLOCKING-YOU row writes ``status: done`` + drops the
``blocker`` field + appends an audit-trail ``comments[]`` entry, then
publishes a notification so dependent agents auto-unblock within 5s
via AutoRefresh (or sub-second via push when the SacChannel adapter
lands per ADR-0006).

Idempotent on already-done rows so a double-click doesn't double-
publish or fail-noisily.
"""

from __future__ import annotations

import datetime
import logging
import os

from django.http import JsonResponse

from .crud import _parse_body

logger = logging.getLogger(__name__)


def handle_resolve(request, board):
    """POST resolve -> the BLOCKING YOU panel's resolve flow (ADR-0006 GUI→code).

    Body: ``{id, actor?}``. Flips the task to ``status=done, blocker=null``
    and appends a ``comments[]`` entry recording the resolution + actor.
    This is the load-bearing GUI→code loop the operator explicitly named
    (TG 9522 + 9667 + 9671 + 9674): clicking Resolve on a BLOCKING YOU
    row writes to the store so the dependent agent's depends_on
    auto-unblocks within 5s via AutoRefresh (or via push when the SacChannel
    notification adapter lands in the live-data PR per ADR-0006).

    The notification-port publish step is in scope but uses PR #55's
    default ``InProcessPubSub`` adapter today; the fleet adapter
    (SacChannelNotificationAdapter routing through the wake-generalize
    bus) drops in via dependency injection later.

    Unknown id -> 404. Already-resolved (status=done) -> 200 idempotent
    no-op so a double-click doesn't double-publish.
    """
    if request.method != "POST":
        return JsonResponse({"error": "resolve endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        return JsonResponse({"error": "resolve requires 'id'"}, status=400)

    actor = payload.get("actor")
    if not isinstance(actor, str) or not actor.strip():
        actor = os.environ.get("USER") or "operator"

    from scitex_cards import TaskValidationError
    from scitex_cards._model import _save_doc_unlocked, _store_lock
    from scitex_cards._store import _read_write_doc

    from ..services import _reset_cache

    # One flock'd read-modify-write against the GLOBAL store — never the
    # request-scoped board cache, whose staleness window used to clobber any
    # concurrent write landing between the cache read and the save. (The GUI's
    # ``_store.resolve_task`` sibling is not used here: this endpoint's pinned
    # contract differs — the audit comment names the prior status/blocker, and
    # the already-done noop must not write at all.)
    with _store_lock(board.store_path):
        doc, tasks = _read_write_doc(board.store_path)
        task = next(
            (t for t in tasks if isinstance(t, dict) and t.get("id") == task_id),
            None,
        )
        if task is None:
            return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

        # Idempotent: already-done = no-op success (a double-click on the FE
        # button shouldn't double-publish or fail-noisily). No write happens.
        if task.get("status") == "done":
            return JsonResponse(
                {
                    "id": task_id,
                    "status": "done",
                    "noop": True,
                    "store_path": str(board.store_path),
                }
            )

        # Capture pre-resolve state for the comment trail.
        prior_status = task.get("status")
        prior_blocker = task.get("blocker")

        task["status"] = "done"
        # Pop rather than set-None: the schema validator rejects blocker on
        # non-blocked rows, so leaving blocker present after status=done would
        # round-trip-fail the next load.
        task.pop("blocker", None)

        resolve_comment = {
            "ts": datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "author": actor.strip(),
            "text": (
                f"[RESOLVED via board-v3] flipped status={prior_status!r}→done"
                + (f", blocker={prior_blocker!r}→null" if prior_blocker else "")
                + ". Owning agent will pick up via AutoRefresh / NotificationPort.publish."
            ),
        }
        existing = task.get("comments")
        task["comments"] = ([*existing] if isinstance(existing, list) else []) + [
            resolve_comment
        ]

        try:
            _save_doc_unlocked(doc, board.store_path, tasks=tasks)
        except TaskValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    _reset_cache()

    # Notification publish — uses scitex_cards._adapters.InProcessPubSub default
    # from PR #55. The fleet wires SacChannelNotificationAdapter via
    # constructor injection in the create_board factory (deferred — ADR-0007
    # follow-up #5). Today the publish is a no-op-for-other-processes; the
    # YAML write is the durable path agents pick up via 5s AutoRefresh.
    try:
        from scitex_cards._adapters import InProcessPubSub

        # Local in-process bus — singleton per process. Other agents on
        # this process (if any) get the event; cross-process gets it via
        # the YAML write + AutoRefresh until SacChannel adapter lands.
        _BUS = getattr(handle_resolve, "_BUS", None)
        if _BUS is None:
            _BUS = InProcessPubSub()
            handle_resolve._BUS = _BUS  # type: ignore[attr-defined]
        channel = f"scitex-todo:task:{task.get('project', 'unknown')}/{task_id}"
        _BUS.publish(
            channel,
            {
                "task_id": task_id,
                "changes": {"status": "done", "blocker": None},
                "ts": resolve_comment["ts"],
                "actor": actor.strip(),
            },
        )
    except Exception:  # noqa: BLE001 — publish-failure is non-fatal
        logger.exception("[scitex-todo] resolve notify-publish failed (non-fatal)")

    logger.info(
        "[scitex-todo] RESOLVED %s by %s in %s",
        task_id,
        actor,
        board.store_path,
    )
    return JsonResponse(
        {
            "id": task_id,
            "status": "done",
            "prior_status": prior_status,
            "prior_blocker": prior_blocker,
            "comment": resolve_comment,
            "store_path": str(board.store_path),
        }
    )


# EOF
