#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Delete-with-Undo handlers — remove a task / re-insert it losslessly.

Extracted from ``crud.py`` (512-line file cap). These two endpoints cannot
delegate to the ``_store`` verbs: the frontend's Undo round-trips
``refs = [{id, field}, ...]`` (which OTHER task pointed at the deleted one,
and through WHICH field), while ``_store.delete_task`` returns bare id
strings and ``_store.restore_task`` does not re-apply refs at all. So each
handler instead holds ``_store_lock`` across a FRESH read-modify-write of
the global store — the same chokepoint every ``_store`` verb uses — closing
the old lost-update window (cache read -> in-memory mutate -> whole-list
save, clobbering any concurrent write in between).
"""

from __future__ import annotations

import scitex_logging as slogging

from django.http import JsonResponse

from .crud import _check_status, _parse_body

logger = slogging.getLogger(__name__)


def handle_delete(request, board):
    """POST delete -> remove a task and scrub references to it.

    Body: ``{id}``. The task is removed, and any other task's ``depends_on`` /
    ``blocks`` entry pointing at it is dropped, and any ``parent`` equal to it
    is cleared — so the store never keeps a dangling edge. Unknown id -> 404.
    """
    if request.method != "POST":
        return JsonResponse({"error": "delete endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        return JsonResponse({"error": "delete requires 'id'"}, status=400)

    from scitex_cards import TaskValidationError
    from scitex_cards._model import _save_doc_unlocked, _store_lock
    from scitex_cards._store import _read_write_doc

    from ..services import _reset_cache

    with _store_lock(board.store_path):
        doc, tasks = _read_write_doc(board.store_path)
        removed = next((t for t in tasks if t.get("id") == task_id), None)
        if removed is None:
            return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

        # Record the references we scrub from OTHER tasks so an undo (restore)
        # can put them back exactly — making delete fully reversible.
        scrubbed_refs: list[dict] = []
        remaining = [t for t in tasks if t.get("id") != task_id]
        for t in remaining:
            for edge in ("depends_on", "blocks"):
                refs = t.get(edge)
                if isinstance(refs, list) and task_id in refs:
                    pruned = [r for r in refs if r != task_id]
                    if pruned:
                        t[edge] = pruned
                    else:
                        t.pop(edge, None)
                    scrubbed_refs.append({"id": t["id"], "field": edge})
            if t.get("parent") == task_id:
                t.pop("parent", None)
                scrubbed_refs.append({"id": t["id"], "field": "parent"})

        try:
            # Name the id we intentionally removed: the writer is upsert-only and the
            # mirror never INFERS a delete from absence (the board-wipe fix), so an
            # explicit single-card removal must say what it removed or the row
            # survives. `remaining` omitting it is not enough on its own.
            _save_doc_unlocked(
                doc, board.store_path, tasks=remaining, deleted_ids=[task_id]
            )
        except TaskValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    _reset_cache()
    logger.info("[scitex-cards] deleted task %s from %s", task_id, board.store_path)
    return JsonResponse(
        {
            "deleted": task_id,
            # `removed` (the full task dict) + `refs` let the frontend offer a
            # lossless Undo via the `restore` endpoint.
            "removed": dict(removed),
            "refs": scrubbed_refs,
            "store_path": str(board.store_path),
        }
    )


def handle_restore(request, board):
    """POST restore -> re-insert a previously deleted task (undo for delete).

    Body: ``{task: {<full task dict>}, refs: [{id, field}, ...]}``. Appends the
    task (unless its id already exists) and re-adds each scrubbed reference
    (``other[field]`` gains the task id back, or ``parent`` is restored).
    Returns the restored id. A ``task.status`` outside ``VALID_STATUSES`` —
    including the abolished ``"pending"`` (abolished 2026-07-10) — is a
    400 via the same ``crud._check_status`` contract the create/update and
    reopen verbs use, raised BEFORE the store lock so a rejected restore
    never touches the store (the save path itself only warns on bad values,
    so without this check the Delete→Undo affordance could mint a row the
    enum forbids from a crafted ``task`` mapping). A mapping with NO status
    defaults to ``"deferred"`` (the create-twin default), never a
    status-less row. A restored status other than ``"blocked"`` drops the
    ``blocker`` gate (the schema validator rejects a real blocker on a
    non-``blocked`` row — without this a status-less+gated mapping
    defaulted to ``deferred`` fails on its own gate, the same 400-on-own-
    gate the ``/archive`` + ``close --reason`` twins were fixed for).
    A snapshot stamp does not survive the restore unless
    the card re-enters AS ``done`` — ``completed_at`` on a live row would
    count as delivered forever (throughput aggregates never consult
    ``status``), so the stamp is cleared for every other final status.
    """
    if request.method != "POST":
        return JsonResponse({"error": "restore endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task = payload.get("task")
    if not isinstance(task, dict) or not task.get("id"):
        return JsonResponse(
            {"error": "restore requires a 'task' mapping with an id"},
            status=400,
        )
    tid = task["id"]

    # The board is a SOURCE, so it stays strict like create/update and
    # reopen: a restored status outside VALID_STATUSES (including abolished
    # "pending") is a 400 here, BEFORE the store lock — the save path only
    # warns, so without this a crafted `task` mapping could mint a
    # forbidden row through the Delete→Undo affordance. A mapping with NO
    # status at all defaults to "deferred" first (the handle_create twin
    # default), so a crafted status-less mapping lands in the backlog
    # instead of minting a status-less row the save path would only warn
    # about (value faults warn per the 2026-07-10 ruling).
    if not task.get("status"):
        task["status"] = "deferred"
    err = _check_status({"status": task.get("status")})
    if err is not None:
        return err

    if task.get("status") != "blocked":
        # Leaving `blocked` MUST clear the gate: the endpoint takes ANY
        # mapping (no status precondition — missing defaults to `deferred`
        # above), so it can restore a gated snapshot to a non-`blocked`
        # status, and the schema validator REJECTS a real blocker on a
        # non-`blocked` row (`_validate.py` rule (b)) — without this pop
        # the restore of a gated card fails on its own gate. Same
        # invariant the `/archive` twin keeps (`task.pop("blocker", None)`)
        # and `handle_reopen` keeps on every non-`blocked` restore. A
        # verbatim `blocked` snapshot keeps its gate.
        task.pop("blocker", None)

    # A restored card re-enters as LIVE work, so a snapshot stamp must not
    # survive on it: throughput (fleet/timing.py + timeline.py) believes
    # `completed_at` alone and never consults `status`, so restoring a
    # done+stamped snapshot to a live status would count phantom delivery
    # forever — the same lie `reopen_task` clears via `clear_completion_stamp`.
    # Restored AS `done` keeps its genuine stamp (it really was completed).
    if task.get("status") != "done":
        from ..._store_lifecycle import clear_completion_stamp

        clear_completion_stamp(task)

    from scitex_cards import TaskValidationError
    from scitex_cards._model import _save_doc_unlocked, _store_lock
    from scitex_cards._store import _read_write_doc

    from ..services import _reset_cache

    with _store_lock(board.store_path):
        doc, tasks = _read_write_doc(board.store_path)
        by_id = {t.get("id"): t for t in tasks if isinstance(t, dict)}
        if tid not in by_id:
            tasks.append(dict(task))

        for ref in payload.get("refs") or []:
            if not isinstance(ref, dict):
                continue
            owner = by_id.get(ref.get("id"))
            field = ref.get("field")
            if owner is None:
                continue
            if field == "parent":
                owner["parent"] = tid
            elif field in ("depends_on", "blocks"):
                lst = owner.get(field)
                lst = list(lst) if isinstance(lst, list) else []
                if tid not in lst:
                    lst.append(tid)
                owner[field] = lst

        try:
            _save_doc_unlocked(doc, board.store_path, tasks=tasks)
        except TaskValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    _reset_cache()
    logger.info("[scitex-cards] restored task %s in %s", tid, board.store_path)
    return JsonResponse({"restored": tid, "store_path": str(board.store_path)})


# EOF
