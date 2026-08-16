#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rescore handler — a matrix-view drag sets a card's two axes.

ADR-0011 §8: humans DRAG a card in the urgency×importance matrix to update
its axes; the rank engine recomputes the whole order server-side and the new
order is immediately shared with agents. This handler is the HTTP entry for
that drag.

It owns NO logic of its own — it DELEGATES to the locked ``rescore_task``
store verb (fresh read-modify-write under the store flock), which is the only
writer of ``rank`` and the only emitter of the ``rank_changed`` card-event. A
handler-local flock write would be atomic but EVENTLESS — invisible to agents
— so the re-score must go through the verb (the decisive finding on card
``scitex-cards-gui-matrix-view-20260717``).

Rank is COMPUTED, never asserted (ADR-0011 §1): the client sends only the two
axis values it dropped a card onto; the server computes rank. Axes are
validated 1..5 fail-loud by the verb (a bad axis surfaces as 400).
"""

from __future__ import annotations

import logging

from django.http import JsonResponse

from .crud import _parse_body

logger = logging.getLogger(__name__)

# The matrix is the human instrument (ADR-0011 §8: "humans update by
# DRAGGING"), so a drag is attributed to the operator, not to the GUI
# server's own agent identity — the latter would falsely claim the server
# agent made a judgement a human made. Reviewable: scitex-cards may prefer a
# request-supplied actor once more than one human uses the board.
_GUI_DRAG_ACTOR = "operator"


def handle_rescore(request, board):
    """POST rescore -> set one card's urgency+importance, recompute rank.

    Body: ``{id, urgency, importance}`` (axes are ints 1..5). Returns
    ``{task, rank, of, store_path}`` from the verb. Unknown id -> 404,
    bad/out-of-range axis -> 400, non-POST -> 405.
    """
    if request.method != "POST":
        return JsonResponse({"error": "rescore endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        return JsonResponse({"error": "rescore requires 'id'"}, status=400)

    if "urgency" not in payload or "importance" not in payload:
        return JsonResponse(
            {"error": "rescore requires 'urgency' and 'importance'"}, status=400
        )
    urgency = payload["urgency"]
    importance = payload["importance"]
    # Shape guard: reject non-ints (and bools, which ARE ints in Python) here
    # so a `"4"`-string or `null` axis is a clean 400 rather than a 500 out of
    # the verb's type check. The verb still owns the 1..5 RANGE check.
    if (
        not isinstance(urgency, int)
        or isinstance(urgency, bool)
        or not isinstance(importance, int)
        or isinstance(importance, bool)
    ):
        return JsonResponse(
            {"error": "urgency and importance must be integers"}, status=400
        )

    # 404 fast-path on the cached union (mirrors handle_update) so an unknown
    # id never reaches the verb; the verb re-checks under its lock anyway.
    if not any(isinstance(t, dict) and t.get("id") == task_id for t in board.tasks):
        return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

    from scitex_cards._store import TaskNotFoundError, rescore_task

    from ..services import _reset_cache

    try:
        result = rescore_task(
            board.store_path,
            task_id,
            urgency=urgency,
            importance=importance,
            by=_GUI_DRAG_ACTOR,
        )
    except TaskNotFoundError:
        # Passed the cached-union fast-path but absent from the GLOBAL store
        # the verb writes (a lane-only card, or a delete race) -> clean 404.
        return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)
    except ValueError as exc:
        # Axis out of the 1..5 scale (the verb's fail-loud validator) -> 400.
        return JsonResponse({"error": str(exc)}, status=400)
    _reset_cache()
    logger.info(
        "[scitex-cards] rescored %s -> u=%s i=%s rank=%s/%s in %s",
        task_id,
        urgency,
        importance,
        result.get("rank"),
        result.get("of"),
        board.store_path,
    )
    return JsonResponse(
        {
            "task": result["task"],
            "rank": result["rank"],
            "of": result["of"],
            "store_path": str(board.store_path),
        }
    )
