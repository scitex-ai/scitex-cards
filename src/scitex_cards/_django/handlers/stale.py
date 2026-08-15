#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stale-cards review handler — derives the standing "operator should
archive" list from the loaded board + the SAME criteria as the
``STALE_CARDS_FOR_REVIEW.md`` generator (proj-scitex-todo
2026-06-13, operator-direct via lead a2a).

Two endpoints:

  GET  /stale    -> ``{stale: [...], total: N, by_project: {...},
                      criteria: {days, include_no_timestamp}}``
                   The standing operator-review feed for the board's
                   "Stale Review" panel.

  POST /archive  -> body ``{id, reason, by?}``; closes one card WITH
                   the reason recorded (mirrors the CLI ``close``
                   verb shipped in PR #151): appends a ``[CLOSED]``
                   comment, flips status to ``deferred`` (sentinel),
                   stamps ``_log_meta.closed_{at,by}``. The operator
                   clicks "archive" on a stale-panel row -> the
                   front-end POSTs here with a confirmed reason.

Provenance:
- Operator TG (via lead a2a 2026-06-13) — "make stale review a
  recurring board feature, not a one-off MD".
- CLI close-verb gap closed in PR #151; this is the HTTP twin so
  the operator can drive the same write from the board UI without
  shelling out.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from collections import defaultdict

from django.http import JsonResponse

from ..._comment_ids import stamp_comment_id

logger = logging.getLogger(__name__)


_DEFAULT_DAYS = 14


def _parse_iso(s):
    """Lenient ISO-8601 parser — accepts ``Z`` suffix as ``+00:00``."""
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:  # pragma: no cover — bad data, treat as missing
        return None


def _is_unclear(task: dict) -> bool:
    """Title/owner-orphaned heuristic — same as the MD generator.

    A row counts as unclear if title is empty/very-short AND there's no
    assignee / repo / project to anchor ownership.
    """
    title = (task.get("title") or "").strip()
    if not title or len(title) < 12:
        if not (
            task.get("assignee")
            or task.get("agent")
            or task.get("repo")
            or task.get("project")
        ):
            return True
    return False


def _stale_reasons(task: dict, now, cut) -> list[str]:
    """Return the list of "why-flagged" reasons; empty list = not stale."""
    reasons: list[str] = []
    created = _parse_iso(task.get("created_at"))
    last_act = _parse_iso(task.get("last_activity"))

    if created is not None and created < cut:
        reasons.append(f"created_at>14d ({created.date()})")
    elif last_act is not None and last_act < cut:
        reasons.append(f"last_activity>14d ({last_act.date()})")
    if created is None and last_act is None:
        reasons.append("no created_at + no last_activity")
    if _is_unclear(task):
        reasons.append("vague/orphaned (no clear title/owner)")

    return reasons


def _age_days(task: dict, now) -> int | None:
    """Days since creation (preferred) or last_activity. ``None`` if neither."""
    created = _parse_iso(task.get("created_at"))
    if created is not None:
        return (now - created).days
    last_act = _parse_iso(task.get("last_activity"))
    if last_act is not None:
        return (now - last_act).days
    return None


def _includes_no_timestamp(reasons: list[str]) -> bool:
    """True if THIS row was flagged ONLY because it has no timestamps."""
    return reasons == ["no created_at + no last_activity"]


def handle_stale(request, board):
    """GET /stale -> standing stale-cards feed for the board's review panel.

    Query params:

    - ``days`` (int, default 14): age cutoff in days for created_at /
      last_activity. Smaller value surfaces more cards.
    - ``include_no_timestamp`` (bool, default ``true``): set to
      ``false`` to HIDE rows whose ONLY reason is missing timestamps
      (~70% of the default flagged set per the 2026-06-13 sweep — the
      operator may want to see the truly old subset only).

    Returns: ``{stale: [...], total: N, by_project: {pkg: count, ...},
    criteria: {days, include_no_timestamp}, store_path}``.
    """
    if request.method != "GET":
        return JsonResponse({"error": "stale endpoint requires GET"}, status=405)

    days = _DEFAULT_DAYS
    raw_days = request.GET.get("days")
    if raw_days:
        try:
            days = int(raw_days)
            if days < 0:
                raise ValueError
        except ValueError:
            return JsonResponse(
                {"error": f"'days' must be a non-negative int, got {raw_days!r}"},
                status=400,
            )

    raw_inc = (request.GET.get("include_no_timestamp") or "true").lower()
    include_no_timestamp = raw_inc not in ("false", "0", "no", "off")

    now = datetime.datetime.now(datetime.timezone.utc)
    cut = now - datetime.timedelta(days=days)

    stale: list[dict] = []
    by_project: dict[str, int] = defaultdict(int)

    for t in board.tasks:
        if t.get("status") != "pending":
            continue
        reasons = _stale_reasons(t, now, cut)
        if not reasons:
            continue
        if not include_no_timestamp and _includes_no_timestamp(reasons):
            continue
        proj = t.get("project") or "(none)"
        by_project[proj] += 1
        stale.append(
            {
                "id": t["id"],
                "title": t.get("title") or "",
                "project": proj,
                "assignee": t.get("assignee") or t.get("agent") or "",
                "priority": t.get("priority"),
                "created_at": t.get("created_at"),
                "last_activity": t.get("last_activity"),
                "age_days": _age_days(t, now),
                "reasons": reasons,
            }
        )

    # Sort oldest-first (None ages last — same convention as the MD generator).
    stale.sort(
        key=lambda r: (0, -r["age_days"]) if r["age_days"] is not None else (1, 0)
    )

    return JsonResponse(
        {
            "stale": stale,
            "total": len(stale),
            "by_project": dict(by_project),
            "criteria": {"days": days, "include_no_timestamp": include_no_timestamp},
            "store_path": str(board.store_path),
        }
    )


def _parse_body(request):
    """Mirror of ``crud._parse_body`` — kept local to avoid a cycle."""
    try:
        return json.loads(request.body or b"{}"), None
    except json.JSONDecodeError as exc:
        return None, JsonResponse({"error": f"invalid JSON body: {exc}"}, status=400)


def handle_archive(request, board):
    """POST /archive -> close ONE stale card WITH the reason recorded.

    HTTP twin of the CLI ``close --reason`` verb (PR #151). Body:
    ``{id, reason, by?}``. Behavior:

      1. Look up the task. Unknown id -> 404.
      2. Append a ``[CLOSED] <reason>`` comment to ``comments[]``
         (same shape as the CLI verb + ``handle_comment``).
      3. Flip ``status`` to ``"deferred"`` (sentinel — no new enum
         value cascading into FE / tests today).
      4. Stamp ``_log_meta.closed_{at,by}`` (UTC ISO-8601 + author
         precedence chain: body ``by`` -> ``$SCITEX_TODO_AGENT_ID`` ->
         ``$USER`` -> ``"user"``).
      5. Persist via the board's existing save path.

    Returns ``{id, status, closed_at, closed_by, comments_count,
    store_path}`` on success.
    """
    if request.method != "POST":
        return JsonResponse({"error": "archive endpoint requires POST"}, status=405)

    payload, err = _parse_body(request)
    if err:
        return err

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        return JsonResponse({"error": "archive requires 'id'"}, status=400)

    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return JsonResponse(
            {"error": "archive requires a non-empty 'reason'"}, status=400
        )
    reason = reason.strip()

    by = payload.get("by")
    if not isinstance(by, str) or not by.strip():
        by = os.environ.get("SCITEX_TODO_AGENT_ID") or os.environ.get("USER") or "user"
    by = by.strip()

    from scitex_cards import TaskValidationError
    from scitex_cards._model import _save_doc_unlocked, _store_lock
    from scitex_cards._store import _read_write_doc

    from ..services import _reset_cache

    now_iso = (
        datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    )

    # No `_store` verb closes-with-reason today, so hold the store lock
    # across a FRESH read-modify-write of the global store — the same
    # chokepoint every `_store` verb uses. Mutating the request-scoped
    # board cache and saving the whole list (the old path) silently
    # clobbered any concurrent write landing in between.
    with _store_lock(board.store_path):
        doc, tasks = _read_write_doc(board.store_path)
        task = next(
            (t for t in tasks if isinstance(t, dict) and t.get("id") == task_id),
            None,
        )
        if task is None:
            return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

        comment = stamp_comment_id(
            {"ts": now_iso, "author": by, "text": f"[CLOSED] {reason}"}
        )
        existing = task.get("comments")
        task["comments"] = ([*existing] if isinstance(existing, list) else []) + [
            comment
        ]
        task["status"] = "deferred"

        log_meta = task.get("_log_meta")
        if not isinstance(log_meta, dict):
            log_meta = {}
        log_meta["closed_at"] = now_iso
        log_meta["closed_by"] = by
        task["_log_meta"] = log_meta

        try:
            _save_doc_unlocked(doc, board.store_path, tasks=tasks)
        except TaskValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    _reset_cache()
    logger.info(
        "[scitex-todo] archive %s by %s (reason: %.80s) in %s",
        task_id,
        by,
        reason,
        board.store_path,
    )

    return JsonResponse(
        {
            "id": task_id,
            "status": task["status"],
            "closed_at": now_iso,
            "closed_by": by,
            "comments_count": len(task["comments"]),
            "store_path": str(board.store_path),
        }
    )


# EOF
