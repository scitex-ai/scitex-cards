#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CRUD handlers — create / update / comment on a task in the YAML store.

These extend the first write path (``priority``) to full create/update/delete
driven by the board's right-click menu and editable detail drawer (delete /
restore live in ``undo.py``, edges in ``edge.py`` — 512-line file cap).

NO handler may read ``board.tasks`` (a request-scoped CACHE, and a UNION of
the global store + per-project lanes), mutate it in memory, and save the
whole list back: any concurrent write landing between the cache read and the
save is silently clobbered (lost update), and the stale union gets absorbed
into the global store. Every write here DELEGATES to a locked ``_store``
verb (``add_task`` / ``update_task`` / ``comment_task``), which holds the
store flock across its own fresh read-modify-write. ``board.tasks`` is used
only for read-only fast-paths (404 checks, the id-collision scan on create).

Field set mirrors the task model: ``id`` + ``title`` + ``status`` (required)
and optional ``repo`` / ``depends_on`` / ``blocks`` / ``note`` / ``priority``
/ ``parent``. The store validator is the single validation gate, so a bad
mutation surfaces as a 400 rather than corrupting the store.
"""

from __future__ import annotations

import json
import logging
import os
import re

from django.http import JsonResponse

logger = logging.getLogger(__name__)

# Fields a client may set on create / patch on update. ``id`` is server-owned
# (generated on create, immutable on update) so it is deliberately excluded.
_EDITABLE_FIELDS = (
    "title",
    "status",
    "priority",
    "note",
    "repo",
    "parent",
    "depends_on",
    "blocks",
    # Operator-co-designed fields (Task dataclass, PR #56). Operator was
    # losing edits silently — `project` (and the rest) were not in this
    # tuple, so handle_update returned 200 with the field untouched, and
    # the card-drag (PR #77, TG 385) became a visual no-op (operator TG
    # 453 reproducer: dragged 学会 GTM card business → calendar, toast
    # said success, card stayed put). Whitelist now mirrors every
    # writable field on the Task dataclass.
    "project",
    "agent",
    "task",
    "host",
    "goal",
    "pr_url",
    "issue_url",
    "last_activity",
    "created_at",
    "scope",
    "assignee",
    "blocker",
    "kind",
    "job_id",
    "command",
    "started_at",
    "finished_at",
)


def _parse_body(request):
    """Decode a JSON object body, or return (None, error_response)."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, JsonResponse({"error": f"invalid JSON body: {exc}"}, status=400)
    if not isinstance(payload, dict):
        return None, JsonResponse({"error": "body must be a JSON object"}, status=400)
    return payload, None


def _slug_id(title: str, taken: set[str]) -> str:
    """Derive a stable, unique task id from a title.

    Lowercase, non-alphanumerics collapsed to single hyphens, trimmed to a
    sane length; a numeric suffix is appended on collision so two cards with
    the same title never share an id.
    """
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
    base = base or "task"
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _check_status(payload: dict):
    """400 on a status outside VALID_STATUSES; None when absent or valid.

    The board is a SOURCE, and sources stay strict — a human picking from a
    select must never mint a value the enum doesn't know. The save path
    itself only WARNS on bad values now (operator ruling 2026-07-10: a
    status must never cost someone their card on the SHARED store, where
    the bad row may be another, newer agent's), so the 400 that used to
    fall out of save-side validation must be raised here on purpose.
    """
    from scitex_cards._model import VALID_STATUSES

    status = payload.get("status")
    if status is not None and status not in VALID_STATUSES:
        return JsonResponse(
            {
                "error": f"invalid status {status!r}; "
                f"choose one of {sorted(VALID_STATUSES)}"
            },
            status=400,
        )
    return None


def handle_create(request, board):
    """POST create -> append a new task. Body: ``{title, assignee, status?, ...}``.

    Operator constitution (no silent fallbacks): EVERY card has a creator AND an
    owner. This is the web sibling of :func:`scitex_cards._store.add_task`; it
    DELEGATES the write to ``add_task`` (instead of hand-building the dict — the
    old bug gave a UI card a BLANK creator + no required owner) so the UI path
    reuses the same fail-loud + ``agent==assignee`` lock-step + ``created_by``
    stamp. Owner (``assignee`` or ``agent``) REQUIRED -> 400 otherwise.
    ``created_by`` defaults payload -> ``$SCITEX_CARDS_AGENT_ID`` -> ``"operator"``
    (the board is the operator's surface), never blank. ``status`` defaults
    ``pending``; the id is unique across the union view (global + lanes).
    """
    if request.method != "POST":
        return JsonResponse({"error": "create endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        return JsonResponse(
            {"error": "create requires a non-empty 'title'"}, status=400
        )

    # REQUIRE an owner up front so the rejection is a clean 400 the modal toasts
    # (not the 500 api_dispatch turns add_task's TaskValidationError into).
    # Accept either field the form may send; add_task locks agent==assignee.
    def _clean(v):
        return v.strip() if isinstance(v, str) else v

    owner = _clean(payload.get("assignee")) or _clean(payload.get("agent"))
    if not owner:
        return JsonResponse(
            {"error": "assignee is required — pick an owner"}, status=400
        )
    # Default the creator to the operator (the board's identity) when neither
    # payload nor env names one — never blank. add_task re-validates this.
    created_by = (
        _clean(payload.get("created_by"))
        or os.environ.get("SCITEX_CARDS_AGENT_ID")
        or "operator"
    )
    # Unique id across the union view (global + lanes) the board renders, so a
    # UI id never collides with a lane task though the write lands global.
    taken = {t["id"] for t in board.tasks if isinstance(t, dict) and t.get("id")}
    new_id = _slug_id(title.strip(), taken)
    # Forward the other editable form fields (project/note/...) as extras,
    # dropping empties (UI clear-on-empty semantics keep the YAML sparse).
    _handled = {"title", "status", "assignee", "agent", "created_by"}
    extra_fields = {
        k: payload[k]
        for k in _EDITABLE_FIELDS
        if k in payload and k not in _handled and payload[k] not in (None, "", [])
    }

    err = _check_status(payload)
    if err:
        return err

    from scitex_cards import TaskValidationError
    from scitex_cards._store import add_task

    from ..services import _reset_cache

    try:
        task = add_task(
            board.store_path,
            id=new_id,
            title=title.strip(),
            status=payload.get("status") or "deferred",
            assignee=owner,
            created_by=created_by,
            **extra_fields,
        )
    except TaskValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    _reset_cache()
    logger.info("[scitex-cards] created task %s in %s", task["id"], board.store_path)
    return JsonResponse({"task": task, "store_path": str(board.store_path)})


def handle_update(request, board):
    """POST update -> patch an existing task. Body: ``{id, <fields...>}``.

    Only the provided editable fields change; ``id`` is immutable. Returns the
    updated task. Unknown id -> 404. DELEGATES to
    :func:`scitex_cards._store.update_task` (fresh read + write under the
    store flock), which also stamps ``last_activity`` and emits the canonical
    status-flip card-events the cache-write path never did.
    """
    if request.method != "POST":
        return JsonResponse({"error": "update endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        return JsonResponse({"error": "update requires 'id'"}, status=400)
    err = _check_status(payload)
    if err:
        return err
    # An EXPLICIT null status must not reach update_task: status is exempt
    # from the clear-map below, and the verb deletes any field passed as
    # None — leaving a status-less card that drops out of every lane, the
    # exact state the verb's own contract forbids ("status cannot be
    # cleared"). No GUI control emits null here; reject it loudly.
    if "status" in payload and payload["status"] is None:
        return JsonResponse(
            {"error": "status cannot be cleared — every card carries a decision"},
            status=400,
        )

    # 404 fast-path on the cached union (mirrors handle_comment) so an unknown
    # id never reaches the verb; the verb re-checks under its lock anyway.
    if not any(isinstance(t, dict) and t.get("id") == task_id for t in board.tasks):
        return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

    # Translate GUI PATCH semantics onto update_task's clearing contract: the
    # GUI clears an optional field on None / "" / [] (keeping the YAML free of
    # empty scaffolding), while update_task deletes on None only — a bare ""
    # on a free-text field would be written literally. title/status pass
    # through verbatim (the GUI never treated them as clear-on-empty).
    fields = {}
    for key in _EDITABLE_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if key not in ("title", "status") and value in (None, "", []):
            fields[key] = None
        else:
            fields[key] = value

    from scitex_cards import TaskValidationError
    from scitex_cards._store import TaskNotFoundError, update_task

    from ..services import _reset_cache

    try:
        task = update_task(board.store_path, task_id, **fields)
    except TaskNotFoundError:
        # Passed the cached-union fast-path but absent from the GLOBAL store
        # the verb writes (a lane-only card, or a delete race) -> clean 404.
        return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)
    except TaskValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    _reset_cache()
    # Transport-only annotation (owner-liveness) — not part of the persisted
    # task, and not part of this endpoint's response contract.
    task.pop("assignee_liveness", None)
    logger.info("[scitex-cards] updated task %s in %s", task_id, board.store_path)
    return JsonResponse({"task": task, "store_path": str(board.store_path)})


def handle_comment(request, board):
    """POST comment -> append a comment to a task's thread.

    Body: ``{id, text, author?}``. ``author`` defaults to the supplied value,
    else ``$USER``, else ``"user"``. Delegates the append to
    :func:`scitex_cards._store.comment_task` (the SSOT): append-only under the
    store lock — so concurrent comments from other agents are not clobbered —
    which ALSO emits the canonical ``commented`` card-event. The C4 dispatcher
    then ENQUEUES that comment into each resolved recipient's standalone
    PULL-inbox (the always-works rail; a containerized owner PULLs it via
    ``poll_notifications``) — there is NO direct turn-URL POST, which could
    never reach a containerized owner and slowed the write.

    Unknown id -> 404. Returns the appended comment, the new comment count,
    and a ``relay`` toast describing the INBOX QUEUE (the recipient names the
    comment was queued to) rather than a direct-POST result.
    """
    if request.method != "POST":
        return JsonResponse({"error": "comment endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        return JsonResponse({"error": "comment requires 'id'"}, status=400)

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return JsonResponse(
            {"error": "comment requires a non-empty 'text'"}, status=400
        )

    author = payload.get("author")
    if not isinstance(author, str) or not author.strip():
        author = os.environ.get("USER") or "user"
    author = author.strip()

    # 404 fast-path: an unknown id is a clean 404, not the TaskNotFoundError
    # comment_task would raise (which api_dispatch turns into a 500).
    if not any(t["id"] == task_id for t in board.tasks):
        return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

    from scitex_cards._store import TaskNotFoundError, comment_task

    from ..services import _reset_cache
    from ._comment_relay import comment_inbox_toast

    # SSOT append + `commented` emit (→ C4 enqueues to each recipient's inbox).
    try:
        result = comment_task(
            store=board.store_path, task_id=task_id, text=text.strip(), by=author
        )
    except TaskNotFoundError:
        # Passed the cached-union fast-path but absent from the GLOBAL store
        # (a lane-only card, or a delete race) -> clean 404, not a 500.
        return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)
    _reset_cache()
    comment = result["comment"]
    logger.info(
        "[scitex-cards] comment on %s by %s in %s", task_id, author, board.store_path
    )

    # Re-read the freshly-written card (comment_task wrote under its own lock,
    # so board.tasks is stale) for the count + the inbox-queue toast.
    fresh = next((t for t in board.tasks if t["id"] == task_id), None) or {}
    comments = fresh.get("comments")
    count = len(comments) if isinstance(comments, list) else 1

    return JsonResponse(
        {
            "comment": comment,
            "count": count,
            "store_path": str(board.store_path),
            "relay": comment_inbox_toast(fresh, author, store=board.store_path),
        }
    )


# EOF
