#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django view: ``GET /fleet/timing``.

Operator-direct ask (TG, relayed by lead a2a ``74db4f2d`` + ``10afa799``,
2026-06-14): "record what took how long → self-improvement". This
endpoint serves the Phase-4 timing telemetry payload that the Phase-5
chart UI will consume. The pure compute lives in
:func:`scitex_cards._django.handlers.fleet.timing.compute_timing`; this
view just wires the registry-sourced task store to it and returns JSON.

Endpoint shape::

    GET /fleet/timing?window_days=30

Response (200) — see :func:`compute_timing` for the per-bucket aggregate
shape::

    {
      "window_days": N,
      "window_start": "<ISO>",
      "window_end":   "<ISO>",
      "per_agent":    {...},
      "per_project":  {...},
      "per_group":    {...},
      "n_tasks_in_window": M,
      "n_tasks_missing_timestamps": K
    }

Design principles (HARD):

- **fail-loud** — task-store read errors bubble into Django's 500
  handler. We DO NOT degrade silently to an empty payload.
- **registry-sourced** — reads via ``resolve_tasks_path`` +
  ``load_tasks`` (the same pattern Phase 1 / 2 / 3 use).
- **read-only** — POST returns 405.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse

from .timing import compute_timing

#: Default window length (days) when ``window_days`` is absent or
#: unparseable. Matches the brief; also matches the chart's default
#: "last 30 days" toggle in Phase 5.
_DEFAULT_WINDOW_DAYS: int = 30


def _parse_window_days(raw: str | None) -> int:
    """Return ``window_days`` as a positive int, defaulting on bad input.

    Floor: never raise on a bad value — the FE may pass "" or a stale
    token; fall back to :data:`_DEFAULT_WINDOW_DAYS` so the operator
    always sees something. Cap at one year to bound the response size.
    """
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_WINDOW_DAYS
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_WINDOW_DAYS
    if v <= 0:
        return _DEFAULT_WINDOW_DAYS
    return min(v, 365)


def fleet_timing_view(request: HttpRequest) -> HttpResponse:
    """Serve the Phase-4 timing telemetry JSON.

    Method violations return ``405``; store-read errors bubble into
    Django's 500 handler (fail-loud, per the harness contract).
    """
    if request.method != "GET":
        return JsonResponse(
            {"error": f"method {request.method} not allowed"},
            status=405,
        )

    window_days = _parse_window_days(request.GET.get("window_days"))

    # Cached board read — a bare load_tasks re-parses the whole 5 MB store on
    # every poll (1.22 s live). See handlers/timeline.py for the measurement.
    from ...services import get_board

    board = get_board()
    tasks = board.tasks

    payload = compute_timing(tasks, window_days=window_days)
    return JsonResponse(payload, json_dumps_params={"default": str})


__all__ = ["fleet_timing_view"]

# EOF
