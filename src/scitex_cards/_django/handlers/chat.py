#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/chat/<card_id>`` Django endpoint — fleet CHAT-SURFACE.

Lead a2a ``74db4f2d`` + ``10afa799`` greenlight (TRACK-2 Phase 6,
2026-06-14): the operator wants a live operator-to-agent THREAD VIEW
sitting on top of the per-card ``comments[]`` substrate. The substrate
is already there (``_store.comment_task`` + the existing comment-section
in :mod:`NodeDetailPanel`); this view exposes it as a tiny GET/POST API
the FE chat panel polls every 30s.

Endpoint shape::

    GET /chat/<card_id>

      ->  200 + {
            "card_id": "<id>",
            "title":   "<task.title>",
            "comments": [{"ts": "<ISO>", "author": "<str>",
                          "text": "<str>"}, ...]
          }

      ->  404 if no task with that id.

    POST /chat/<card_id>  body = {"text": "<str>", "author?": "<str>"}

      ->  200 + the appended comment dict on success.
      ->  400 if ``text`` is missing / empty / non-string.
      ->  404 if no task with that id.

All other HTTP verbs return ``405``.

Design principles (HARD, from the operator brief):

- **fail-loud / no-silent-fallback** — a write failure (store error,
  bad payload) returns a structured error JSON; we DO NOT silently
  swallow + return 200. The FE displays the error as a toast.
- **registry-sourced** — read the existing card ``comments[]`` directly;
  write via the existing ``_store.comment_task`` API. No new schema,
  no parallel storage.
- **NO hardcoded proper nouns** — the ``author`` value flows in from
  the request body (set by the FE from ``SCITEX_CARDS_AGENT_ID`` env, with
  operator-typed override). The fallback used when neither is supplied
  is the closed sentinel string ``"<unknown>"`` — purely a display token,
  not a literal agent name.
- **read + write floor; NO RW-perm gating yet** — the operator may want
  operator-only writes later (TODO below); the floor allows all writes
  so the WRITE-BACK UI can land standalone.

Out of scope (deferred — flagged with TODOs):

- RW-perm gating (operator-write, agents-read).
- WebSocket push — polling at 30s is fine for the floor.
- Markdown rendering — plain text only.
- @-mentions, threading, reactions, attachments — YAGNI.
"""

from __future__ import annotations

import json

from django.http import HttpRequest, HttpResponse, JsonResponse


# ---------------------------------------------------------------------------
# Defaults — operator-stated floor.
# ---------------------------------------------------------------------------

#: Fallback display label when neither the request body's ``author`` field nor
#: the underlying ``_store.comment_task`` default (``$SCITEX_CARDS_AGENT_ID`` →
#: ``$USER``) is set. Purely a display string — never a literal agent name,
#: per the "no hardcoded proper nouns" principle.
_UNKNOWN_AUTHOR: str = "<unknown>"


# ---------------------------------------------------------------------------
# Helpers — pure / stateless so they're cheap to test.
# ---------------------------------------------------------------------------


def _parse_json_body(request: HttpRequest):
    """Decode the request body as JSON or return a structured 400 response.

    Mirrors the lenient parser used by ``handlers/nudge._parse_body``: an
    empty body decodes to ``{}`` (POST with no body shouldn't crash before
    the validator runs), invalid JSON becomes a 400.
    """
    try:
        return json.loads(request.body or b"{}"), None
    except json.JSONDecodeError as exc:
        return None, JsonResponse(
            {"error": f"invalid JSON body: {exc}"}, status=400
        )


def _find_task(tasks: list[dict], card_id: str) -> dict | None:
    """Return the first task matching ``card_id`` or ``None``.

    Linear scan — fine at the floor (the store is ``O(100s)`` of cards).
    Hoisting to an ``{id: task}`` index belongs in a perf pass, not the
    floor.
    """
    for t in tasks:
        if t.get("id") == card_id:
            return t
    return None


def _comments_of(task: dict) -> list[dict]:
    """Return the task's ``comments[]`` as a list of dicts.

    The schema validator gates the field as a list of dicts with the
    keys ``ts`` / ``author`` / ``text`` (see ``_store.comment_task``);
    we pass them through as-is so the FE renders verbatim. A task with
    no comments yet returns ``[]``.
    """
    raw = task.get("comments")
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


# ---------------------------------------------------------------------------
# Django view.
# ---------------------------------------------------------------------------


def chat_view(request: HttpRequest, card_id: str) -> HttpResponse:
    """Serve the operator-facing CHAT thread on a single card.

    GET returns the card's ``comments[]`` for polling; POST appends a
    new comment via ``_store.comment_task``. Method violations return
    ``405``. Store-read errors bubble into Django's 500-handler
    (fail-loud) — never silently degrade.
    """
    if request.method not in {"GET", "POST"}:
        return JsonResponse(
            {"error": "method-not-allowed", "method": request.method},
            status=405,
        )

    # Cached board read — a bare load_tasks re-parses the whole 5 MB store on
    # every request (1.22 s live). See handlers/timeline.py for the numbers.
    from ..services import get_board

    board = get_board()
    path = board.store_path
    tasks = board.tasks
    task = _find_task(tasks, card_id)
    if task is None:
        return JsonResponse(
            {"error": f"no task with id {card_id!r}"}, status=404
        )

    if request.method == "GET":
        return JsonResponse(
            {
                "card_id": card_id,
                "title": task.get("title") or task.get("id"),
                "comments": _comments_of(task),
            },
            json_dumps_params={"default": str},
        )

    # POST — write back.
    payload, err = _parse_json_body(request)
    if err is not None:
        return err
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        return JsonResponse(
            {"error": "chat write requires non-empty 'text'"}, status=400
        )
    author = payload.get("author") if isinstance(payload, dict) else None
    if not isinstance(author, str) or not author.strip():
        author = _UNKNOWN_AUTHOR
    else:
        author = author.strip()

    # Delegate to the existing store API — registry-sourced write, no
    # parallel schema. ``_store.comment_task`` re-validates the task id +
    # the text, stamps the ts, and persists via the standard YAML writer.
    from ..._store import comment_task

    result = comment_task(
        store=path, task_id=card_id, text=text, by=author,
    )
    return JsonResponse(
        {
            "card_id": card_id,
            "comment": result["comment"],
        },
        json_dumps_params={"default": str},
        status=200,
    )


__all__ = ["chat_view"]

# EOF
