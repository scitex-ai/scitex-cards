#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Priority handler -- persist a drag-reorder back to the YAML task store.

The board UI is currently view-only; this handler is the first write path.
On drag-end the frontend POSTs ``{"order": ["id1", "id2", ...]}`` (graph node
ids ordered top-priority first); the handler assigns ``priority = 1..N`` to
those ids in order, leaves every other task's priority untouched, validates,
then calls ``scitex_cards.save_tasks`` to round-trip the YAML store (preserving
its hand-written comments via the ruamel writer).
"""

from __future__ import annotations

import json
import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)


def handle_priority(request, board):
    """POST priority -> reassign priorities from a list of task ids in order.

    Body shape: ``{"order": ["id1", "id2", ...]}`` — ids in priority order,
    top-priority first. The handler assigns priorities ``1..N`` to those ids
    in order and leaves every other task's priority untouched. Ids in
    ``order`` that don't exist in the store are silently ignored (the
    frontend can race against a concurrent edit that removed a node).

    Returns ``{"updated": [...], "store_path": "..."}`` on success. Any
    ``TaskValidationError`` raised by the store validator surfaces as a 400
    (the validator's message names the offending field).
    """
    from scitex_cards import TaskValidationError
    from scitex_cards._model import _save_doc_unlocked, _store_lock
    from scitex_cards._store import _read_write_doc

    from ..services import _reset_cache

    if request.method != "POST":
        return JsonResponse({"error": "priority endpoint requires POST"}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": f"invalid JSON body: {exc}"}, status=400)

    order = payload.get("order")
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        return JsonResponse(
            {"error": "body must be {'order': [task_id, ...]} of strings"},
            status=400,
        )

    # One flock'd read-modify-write against the GLOBAL store — never the
    # request-scoped board cache, whose staleness window used to clobber any
    # concurrent write landing between the cache read and the save. (One
    # locked whole-order write, not N ``update_task`` calls: per-id delegation
    # would rewrite the store N times per drag and stamp ``last_activity`` on
    # every reordered card, silently resetting the fleet's inactivity clocks.)
    with _store_lock(board.store_path):
        doc, tasks = _read_write_doc(board.store_path)
        by_id = {t.get("id"): t for t in tasks if isinstance(t, dict) and t.get("id")}

        updated: list[str] = []
        for rank, task_id in enumerate(order, start=1):
            task = by_id.get(task_id)
            if task is None:
                # Unknown id (frontend racing against an external edit) -> skip.
                continue
            task["priority"] = rank
            updated.append(task_id)

        try:
            # `updated`, NOT `order`. A drag legitimately renumbers many cards,
            # so there is no single id to declare here — and the two lists
            # differ: `order` may name ids the board does not have (the skip
            # above), while `updated` is exactly the rows this write changed.
            _save_doc_unlocked(
                doc, board.store_path, tasks=tasks, touched_ids=updated
            )
        except TaskValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

    # Invalidate the board cache so the next GET /graph re-reads from disk
    # (mtime would already trigger a reload, but resetting is explicit and
    # also covers the edge case where mtime resolution loses sub-second writes).
    _reset_cache()

    logger.info(
        "[scitex-todo] priority reorder: %d ids updated in %s",
        len(updated),
        board.store_path,
    )
    return JsonResponse({"updated": updated, "store_path": str(board.store_path)})


# EOF
