#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stale-cards review handler — derives the standing "operator should
archive" list from the loaded board + the SAME criteria as the
``STALE_CARDS_FOR_REVIEW.md`` generator (proj-scitex-cards
2026-06-13, operator-direct via lead a2a).

Two endpoints:

  GET  /stale    -> ``{stale: [...], total: N, by_project: {...},
                      criteria: {days, include_no_timestamp}}``
                   The standing operator-review feed for the board's
                   "Stale Review" panel.

  POST /archive  -> body ``{id, reason, by?}``; closes one card WITH
                   the reason recorded (mirrors the CLI ``close``
                   verb shipped in PR #151): appends a ``[CLOSED]``
                   comment, flips status to ``cancelled`` (terminal,
                   twin-parity with ``close --reason`` + ``project_board``
                   ``ARCHIVE_STATUS``; ``deferred`` is OPEN per the
                   2026-07-10 ruling so it must not carry ``closed_at``),
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
import scitex_logging as slogging
import os
from collections import defaultdict

from django.http import JsonResponse

from ..._backlog_triage import BACKLOG_STATUS
from ..._comment_ids import stamp_comment_id

logger = slogging.getLogger(__name__)


#: A SECOND COPY of `_cli/_stale.py`'s `_DEFAULT_DAYS`. Same number, no shared
#: source: change one and the board and the CLI disagree silently.
#: 14 -> 7 ON THE OPERATOR'S INSTRUCTION, 2026-08-19 (「はい7日でお願いします」),
#: after he was told what it costs: THIS copy feeds the /stale panel behind the
#: ARCHIVE BUTTON, so the widening offers ~a week more of everyone's cards for
#: archiving. He decided with that in front of him. See: cards-auto-expiry-is-a-report-nothing-schedules-and-three-horizons-disagree-20260817
_DEFAULT_DAYS = 7


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
    """Return the list of "why-flagged" reasons; empty list = not stale.

    The age window named in the reason strings is the LIVE cutoff
    (``(now - cut).days``) — the same expression the CLI ``list-stale``
    twin uses — never a hardcoded number: the default moved 14 -> 7 on
    the operator's 2026-08-19 instruction and the endpoint accepts
    ``?days=``. A hardcoded window disagrees with the CLI twin silently
    (this module is a SECOND COPY of the CLI number — see _DEFAULT_DAYS).
    """
    reasons: list[str] = []
    window = (now - cut).days
    created = _parse_iso(task.get("created_at"))
    last_act = _parse_iso(task.get("last_activity"))

    if created is not None and created < cut:
        reasons.append(f"created_at>{window}d ({created.date()})")
    elif last_act is not None and last_act < cut:
        reasons.append(f"last_activity>{window}d ({last_act.date()})")
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

    - ``days`` (int, default 7): age cutoff in days for created_at /
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
        # ``deferred`` is the backlog status since ``pending`` was abolished
        # (2026-07-10): the same single source the CLI ``list-stale`` verb
        # reads (``_backlog_triage.BACKLOG_STATUS``). The hardcoded
        # ``"pending"`` this replaced matched no card in any live store, so
        # the /stale panel served zero rows — the HTTP twin disagreeing with
        # its CLI twin. See BACKLOG_STATUSES in ``_stale/active.py``.
        if t.get("status") != BACKLOG_STATUS:
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
      3. Flip ``status`` to ``"cancelled"`` — the terminal "closed as not
         planned" state, matching the CLI ``close --reason`` twin
         (``_cli/_close.py``) and ``project_board.ARCHIVE_STATUS``.
         Leaving ``blocked`` MUST clear the gate: the endpoint takes ANY
         id (no status precondition), so it can archive a ``blocked``
         card, and the schema validator REJECTS a real blocker variant
         on a non-``blocked`` row (``_validate.py`` rule (b)) — without
         this the close of a gated card 400s on its own gate.
         Operator ruling 2026-07-10: "deferred は終了ではない" —
         ``deferred`` is OPEN, so stamping ``closed_at`` on it mints a
         CLOSED-and-OPEN row the ``terminal_state_honest`` health check
         FAILs on. ``close`` writes ``cancelled``; so does this twin.
         The flip ALSO clears ``_log_meta.completed_{at,by}`` via
         :func:`scitex_cards._store_lifecycle.clear_completion_stamp`:
         the endpoint takes ANY id (no status precondition), so it can
         archive a ``done`` card, and throughput
         (``fleet/timing.py`` + ``timeline.py``) aggregates SOLELY on
         ``completed_at`` — a cancelled row keeping the stamp counts as
         delivered work forever, the same lie ``reopen_task`` was fixed
         for. The CLI twin is safe without this line (it composes
         ``update_task``, whose ``_clear_completion_stamp_on_leaving_done``
         clock fires on every exit from ``done``); this handler writes
         via ``_save_doc_unlocked`` directly and bypasses that clock,
         so it calls the helper itself.
      4. Stamp ``_log_meta.closed_{at,by}`` (UTC ISO-8601 + author
         precedence chain: body ``by`` -> ``$SCITEX_CARDS_AGENT_ID`` ->
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
        by = os.environ.get("SCITEX_CARDS_AGENT_ID") or os.environ.get("USER") or "user"
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
        task["status"] = "cancelled"
        # Leaving `blocked` MUST clear the gate: the endpoint takes ANY id,
        # so it can archive a `blocked` card, and the schema validator
        # REJECTS a real blocker variant on a non-`blocked` row
        # (`_validate.py` rule (b)) — without this pop the close of a gated
        # card 400s on its own gate. Same invariant the project board keeps
        # (`changes["blocker"] = ""`) and `handle_reopen` keeps
        # (`task.pop("blocker", None)` on every non-`blocked` restore).
        task.pop("blocker", None)
        # Leaving `done` must drop the completion stamp: throughput
        # (fleet/timing.py + timeline.py) believes `completed_at` alone and
        # never consults `status`, so a cancelled row keeping the stamp is
        # counted as delivered work forever — the same lie `reopen_task`
        # clears via this same helper. This handler bypasses `update_task`
        # (and its `_clear_completion_stamp_on_leaving_done` clock), so it
        # calls the helper itself. No-op on stamp-less rows.
        from ..._store_lifecycle import clear_completion_stamp

        clear_completion_stamp(task)

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
        "[scitex-cards] archive %s by %s (reason: %.80s) in %s",
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
