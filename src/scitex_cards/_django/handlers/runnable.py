#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.4 — `/runnable` + `/blocked-batch` Django endpoints.

Lead a2a `74db4f2d`, 2026-06-14 — TRACK 1 dispatch backbone, HTTP
surface. The parallelism dispatcher (lead-side) consumes JSON over
HTTP instead of shelling out to `scitex-cards runnable` / `blocked`.

Endpoints:

  GET /runnable  -> the FULL runnable set + diagnostic counts.
                    Query params:
                      ?agent=<name>           filter by agent
                      ?group=<G>              filter by T1.1 group
                                              ("" = ungrouped-only)
                    Returns:
                      {
                        "tasks": [<task dict>...],
                        "candidate_count": int,
                        "blocked_by_deps_count": int
                      }

  GET /blocked-batch -> the FULL not-runnable set with WHY.
                    Same query params. Returns:
                      {
                        "tasks": [
                          {"id","title","reason","chain"},
                          ...
                        ],
                        "total": int,
                        "by_reason": {<reason>: count, ...}
                      }

Both endpoints are READ-ONLY GETs. Method violations return 405.
The Phase-0 fail-loud principle applies: if the underlying task
store is unreadable (load_tasks raises), let the exception bubble
into Django's 500-handler — never silently degrade to an empty set.
"""

from __future__ import annotations

import json

from django.http import HttpRequest, HttpResponse, JsonResponse


def runnable_view(request: HttpRequest) -> HttpResponse:
    """Serve the dispatcher-facing JSON `runnable` query."""
    if request.method != "GET":
        return JsonResponse(
            {"error": "method-not-allowed", "method": request.method},
            status=405,
        )

    from ..._runnable import runnable_tasks

    agent = request.GET.get("agent") or None
    # Group is special: empty string is the RESIDUAL "ungrouped-only"
    # filter (per the T1.2 runnable_tasks contract). So we distinguish
    # "no group param" (None) from "group param with empty value" ("").
    group = request.GET["group"] if "group" in request.GET else None

    # Read through the board's mtime-keyed cache, NOT a bare load_tasks: a
    # direct read re-parses the whole 5 MB store on every poll (1.22 s on the
    # live 1,352-card store). get_board re-reads only when a source has
    # actually changed. It also returns the same UNION the board page renders,
    # so the surfaces cannot disagree about which cards exist.
    from ..services import get_board

    board = get_board()
    path = board.store_path
    tasks = board.tasks
    result = runnable_tasks(tasks, agent=agent, group=group)

    payload = {
        "tasks": result.tasks,
        "candidate_count": result.candidate_count,
        "blocked_by_deps_count": result.blocked_by_deps_count,
    }
    return JsonResponse(payload, safe=False, json_dumps_params={"default": str})


def blocked_batch_view(request: HttpRequest) -> HttpResponse:
    """Serve the dispatcher-facing JSON `blocked-batch` query."""
    if request.method != "GET":
        return JsonResponse(
            {"error": "method-not-allowed", "method": request.method},
            status=405,
        )

    from ..._runnable import blocked_tasks

    agent = request.GET.get("agent") or None
    group = request.GET["group"] if "group" in request.GET else None

    # Read through the board's mtime-keyed cache, NOT a bare load_tasks: a
    # direct read re-parses the whole 5 MB store on every poll (1.22 s on the
    # live 1,352-card store). get_board re-reads only when a source has
    # actually changed. It also returns the same UNION the board page renders,
    # so the surfaces cannot disagree about which cards exist.
    from ..services import get_board

    board = get_board()
    path = board.store_path
    tasks = board.tasks
    result = blocked_tasks(tasks, agent=agent, group=group)

    payload = {
        "tasks": [
            {
                "id": bt.id,
                "title": bt.title,
                "reason": bt.reason,
                "chain": list(bt.chain),
            }
            for bt in result.tasks
        ],
        "total": result.total,
        "by_reason": result.by_reason,
    }
    return JsonResponse(payload, safe=False, json_dumps_params={"default": str})
