#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``POST /nudge`` — operator-clickable per-agent push relay.

Lead a2a ``f16b0d2acb8946f88f2daffc4038228d`` (operator TG12608,
2026-06-12): the board needs a one-click "催促" (nudge) wire on every
agent column so the operator can ping an agent's lane mid-stall without
typing the body manually.

Wire shape (per lead spec):
  1. UI button on each column header → POST /nudge with body
     ``{"agent": "<name>"}``
  2. backend: compose a one-line summary of the agent's open
     in_progress tasks ("<short-id> <title 60c> <Nd>") + the standing
     ask "push or BLOCKED within 15 min"
  3. dispatch via ``sac agents send <agent> <body>`` subprocess;
     stdout fallback when ``sac`` unavailable
  4. UI gets a toast back (success / fail) via the JSON return
  5. per-agent cooldown (5 min, lead-spec'd) to prevent operator
     button-mashing. Cooldown lives in this module's process-local
     state — fine for the single-process Django dev server today;
     when the board scales, this lifts to redis / DB. Until then a
     restart resets the cooldown, which is OK (operator restart =
     intentional reset).

Reuse: the open-list composition is the same one ``stats --notify``
uses in v0.6.0. We delegate to :func:`scitex_cards._throughput.build_notify_body`
so the wire stays in lock-step between the operator-driven nudge and
the cron-driven hourly notify.
"""

from __future__ import annotations

import json
import logging
import time

from django.http import JsonResponse

logger = logging.getLogger(__name__)

# In-process cooldown registry. ``{agent: last_send_unix_ts}``.
_LAST_SENT_AT: dict[str, float] = {}

# Lead-spec'd cooldown window (seconds).
COOLDOWN_SECONDS = 5 * 60


def _nudge_body(agent: str, tasks: list[dict]) -> str:
    """Build the per-agent nudge body. Same shape as
    ``stats --notify`` so the operator-button + cron paths emit
    consistent text."""
    from ..._throughput import build_notify_body

    body = build_notify_body(agent, tasks)
    # Append the standing operator ask so the recipient knows the
    # action contract: either push (= produce progress) OR mark
    # BLOCKED with a reason; both must land within 15 min.
    return body + (
        "\n————————\n"
        "ACTION: push (commit/PR/comment with progress) OR mark "
        "status=blocked with the explicit blocker — within 15 min."
    )


def _parse_body(request):
    try:
        return json.loads(request.body or b"{}"), None
    except json.JSONDecodeError as e:
        return None, JsonResponse(
            {"error": f"invalid JSON body: {e}"}, status=400
        )


def handle_nudge(request, board):
    """POST nudge → push a 催促 line to the named agent.

    Body: ``{"agent": "<name>"}``. Returns
    ``{ok, agent, wire, cooldown_remaining_s, body_chars}``.
    """
    if request.method != "POST":
        return JsonResponse(
            {"error": "nudge endpoint requires POST"}, status=405
        )
    payload, err = _parse_body(request)
    if err:
        return err

    agent = payload.get("agent")
    if not isinstance(agent, str) or not agent.strip():
        return JsonResponse(
            {"error": "nudge requires non-empty 'agent'"}, status=400
        )
    agent = agent.strip()

    now = time.time()
    last = _LAST_SENT_AT.get(agent, 0.0)
    if (now - last) < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        return JsonResponse(
            {
                "ok": False,
                "agent": agent,
                "wire": "cooldown",
                "cooldown_remaining_s": remaining,
                "error": f"per-agent cooldown active ({remaining}s remaining)",
            },
            status=429,
        )

    body = _nudge_body(agent, list(board.tasks))
    from ..._push import deliver  # noqa: PLC0415

    result = deliver(
        agent, body,
        kind="nudge",
        store_path=str(board.store_path),
    )
    if result.get("ok"):
        _LAST_SENT_AT[agent] = now

    logger.info(
        "[scitex-cards] nudge → %s wire=%s reason=%s (%d chars)",
        agent, result.get("wire"), result.get("reason"), len(body),
    )
    return JsonResponse(
        {
            "ok": result.get("ok", False),
            "agent": agent,
            "wire": result.get("wire"),
            "reason": result.get("reason"),
            "status": result.get("status"),
            "cooldown_remaining_s": COOLDOWN_SECONDS if result.get("ok") else 0,
            "body_chars": len(body),
        },
        # 200 even on "no-turn-url-configured" because the UI handles
        # the {ok:false, reason:"..."} toast; reserve 502 for actual
        # transport / HTTP failures from a configured URL.
        status=200 if (result.get("ok") or result.get("reason") in {
            "no-turn-url-configured",
        }) else 502,
    )


__all__ = ["handle_nudge", "COOLDOWN_SECONDS"]
