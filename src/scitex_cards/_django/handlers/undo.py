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

import logging

from django.http import JsonResponse

from .crud import _parse_body

logger = logging.getLogger(__name__)


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
            # Name the id we intentionally removed: SQLite is upsert-only and the
            # mirror never INFERS a delete from absence (the board-wipe fix), so an
            # explicit single-card removal must say what it removed or the row
            # survives. `remaining` omitting it is not enough on its own.
            _save_doc_unlocked(
                doc, board.store_path, tasks=remaining, deleted_ids=[task_id]
            )
        except TaskValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    _reset_cache()
    logger.info("[scitex-todo] deleted task %s from %s", task_id, board.store_path)
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
    Returns the restored id.
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
    logger.info("[scitex-todo] restored task %s in %s", tid, board.store_path)
    return JsonResponse({"restored": tid, "store_path": str(board.store_path)})


# EOF
