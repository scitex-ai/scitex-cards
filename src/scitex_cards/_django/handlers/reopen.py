#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reopen handler — the board-v3 Undo affordance for /resolve (ADR-0006).

Extracted from ``crud.py`` to give ``tests/scitex_cards/_django/handlers/
test_reopen.py`` a matching src file (PS-204 mirror conformance) +
keep crud.py under the 512-line file-size threshold. Mirrors the
companion ``resolve.py`` extraction.

This is the load-bearing safety net for operator pain TG 9763 (a UI
test click made the clew card vanish destructively, with no way back).
Pairs with the v3 board's 2-click confirm + Undo-toast so Resolve
stops being a trap.
"""

from __future__ import annotations

import datetime
import logging
import os

from django.http import JsonResponse

from ..._comment_ids import stamp_comment_id
from .crud import _parse_body

logger = logging.getLogger(__name__)


def handle_reopen(request, board):
    """POST reopen -> undo a prior resolve (board-v3 Undo affordance).

    Body: ``{id, prior_status?, prior_blocker?, actor?}``. Reverses a previous
    ``/resolve``: restores ``status`` to ``prior_status`` (default ``"blocked"``)
    and ``blocker`` to ``prior_blocker`` (default ``"operator-decision"``).
    The schema validator rejects ``blocker`` on non-blocked rows, so when the
    restored status is anything other than ``"blocked"`` the field is dropped.
    Appends a ``[UNDONE via board-v3]`` comment and publishes a notification.
    Unknown id -> 404.

    Lossless today because notification fan-out is in-process — the
    dependent agent has not been told yet via SacChannel.
    """
    if request.method != "POST":
        return JsonResponse({"error": "reopen endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        return JsonResponse({"error": "reopen requires 'id'"}, status=400)

    actor = payload.get("actor")
    if not isinstance(actor, str) or not actor.strip():
        actor = os.environ.get("USER") or "operator"

    new_status = payload.get("prior_status") or "blocked"
    new_blocker = payload.get("prior_blocker")
    # Default blocker only when restoring to blocked AND caller didn't supply one.
    if new_status == "blocked" and not new_blocker:
        new_blocker = "operator-decision"

    from scitex_cards import TaskValidationError
    from scitex_cards._model import _save_doc_unlocked, _store_lock
    from scitex_cards._store import _read_write_doc

    from ..services import _reset_cache

    # One flock'd read-modify-write against the GLOBAL store — never the
    # request-scoped board cache, whose staleness window used to clobber any
    # concurrent write landing between the cache read and the save. (The
    # ``_store.reopen_task`` verb is not used here: it always forces
    # ``blocked``/``operator-decision``, while this endpoint's pinned contract
    # restores the caller-supplied prior_status / prior_blocker.)
    with _store_lock(board.store_path):
        doc, tasks = _read_write_doc(board.store_path)
        task = next(
            (t for t in tasks if isinstance(t, dict) and t.get("id") == task_id),
            None,
        )
        if task is None:
            return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

        pre_status = task.get("status")
        pre_blocker = task.get("blocker")

        task["status"] = new_status
        if new_status == "blocked" and new_blocker:
            task["blocker"] = new_blocker
        else:
            # Schema disallows blocker on non-blocked rows.
            task.pop("blocker", None)

        reopen_comment = stamp_comment_id(
            {
                "ts": datetime.datetime.now(datetime.timezone.utc)
                .replace(microsecond=0)
                .isoformat(),
                "author": actor.strip(),
                "text": (
                    f"[UNDONE via board-v3] re-opened by {actor.strip()}; "
                    f"status={pre_status!r}→{new_status!r}"
                    + (
                        f", blocker={pre_blocker!r}→{new_blocker!r}"
                        if (pre_blocker or new_blocker)
                        else ""
                    )
                    + ". Reverses the prior /resolve. Lossless: SacChannel wake "
                    "adapter not wired yet, dependent agent was never notified."
                ),
            }
        )
        existing = task.get("comments")
        task["comments"] = ([*existing] if isinstance(existing, list) else []) + [
            reopen_comment
        ]

        try:
            # Single-card: this handler flips one card's status and appends one
            # comment to it. Unlike `priority` next door, and unlike the
            # `delete_task` / `rescore_task` verbs, nothing here reaches
            # another row.
            _save_doc_unlocked(
                doc, board.store_path, tasks=tasks, touched_ids=[task_id]
            )
        except TaskValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    _reset_cache()

    # Mirror the resolve publish path so any in-process subscriber gets the
    # reopen as a first-class change event.
    try:
        from scitex_cards._adapters import InProcessPubSub

        _BUS = getattr(handle_reopen, "_BUS", None)
        if _BUS is None:
            _BUS = InProcessPubSub()
            handle_reopen._BUS = _BUS  # type: ignore[attr-defined]
        channel = f"scitex-todo:task:{task.get('project', 'unknown')}/{task_id}"
        _BUS.publish(
            channel,
            {
                "task_id": task_id,
                "changes": {"status": new_status, "blocker": new_blocker},
                "ts": reopen_comment["ts"],
                "actor": actor.strip(),
                "undo_of": "resolve",
            },
        )
    except Exception:  # noqa: BLE001 — publish-failure is non-fatal
        logger.exception("[scitex-todo] reopen notify-publish failed (non-fatal)")

    logger.info(
        "[scitex-todo] REOPENED %s by %s in %s (status %r->%r)",
        task_id,
        actor,
        board.store_path,
        pre_status,
        new_status,
    )
    return JsonResponse(
        {
            "id": task_id,
            "status": new_status,
            "blocker": new_blocker,
            "prior_status": pre_status,
            "prior_blocker": pre_blocker,
            "comment": reopen_comment,
            "store_path": str(board.store_path),
        }
    )


# EOF
