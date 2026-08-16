#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Edge handler — add / remove a dependency edge between two tasks.

Extracted from ``crud.py`` (512-line file cap). Delegates the write to the
locked :func:`scitex_cards._store.set_edge` verb: the old path read the
board's cached task list, mutated it in memory and saved the WHOLE list
back, so any concurrent write landing between the cache read and the save
was silently clobbered (lost update).
"""

from __future__ import annotations

import logging

from django.http import JsonResponse

from .crud import _parse_body

logger = logging.getLogger(__name__)


def handle_edge(request, board):
    """POST edge -> add or remove a dependency edge between two tasks.

    Body: ``{action: "add"|"remove", kind: "depends_on"|"blocks", source, target}``
    where ``source``/``target`` use the graph-payload orientation:
      - ``depends_on``: edge points dependency(source) -> dependent(target), so
        the field lives on ``target`` as ``target.depends_on += [source]``.
      - ``blocks``: edge points blocker(source) -> blocked(target), so the
        field lives on ``source`` as ``source.blocks += [target]``.

    Add is idempotent; remove drops the reference. Both endpoints validate that
    the two ids exist (404 otherwise). The write DELEGATES to
    :func:`scitex_cards._store.set_edge`, which ALSO subscribes the waiting
    card's owner to the card they wait on (the 2026-07-13 fix this GUI path
    had been silently missing). ``set_edge`` hangs the field on ITS ``source``,
    so for ``kind=depends_on`` the GUI payload's source/target are SWAPPED on
    the way in (the on-disk placement stays identical to the old handler's).
    """
    if request.method != "POST":
        return JsonResponse({"error": "edge endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    action = payload.get("action")
    if action not in ("add", "remove"):
        return JsonResponse(
            {"error": "edge 'action' must be 'add' or 'remove'"}, status=400
        )
    kind = payload.get("kind")
    if kind not in ("depends_on", "blocks"):
        return JsonResponse(
            {"error": "edge 'kind' must be 'depends_on' or 'blocks'"},
            status=400,
        )
    source = payload.get("source")
    target = payload.get("target")
    if not (isinstance(source, str) and source and isinstance(target, str) and target):
        return JsonResponse(
            {"error": "edge requires string 'source' and 'target'"}, status=400
        )
    if source == target:
        return JsonResponse({"error": "edge source and target must differ"}, status=400)

    # 404 fast-path on the cached union, in the field-owner-first order the
    # old handler used so the error payloads are unchanged. The verb
    # re-validates both ids under its lock.
    ids = {t.get("id") for t in board.tasks if isinstance(t, dict)}
    owner_id, other = (target, source) if kind == "depends_on" else (source, target)
    if owner_id not in ids:
        return JsonResponse({"error": f"no task with id {owner_id!r}"}, status=404)
    if other not in ids:
        return JsonResponse({"error": f"no task with id {other!r}"}, status=404)

    from scitex_cards._store import TaskNotFoundError, set_edge

    from ..services import _reset_cache

    # ORIENTATION: set_edge mutates tasks[source][kind] adding target. The
    # GUI's depends_on payload hangs the field on the GUI *target*
    # (target.depends_on += [source]), so source/target swap for
    # kind=depends_on; for kind=blocks the orientations already agree.
    if kind == "depends_on":
        verb_source, verb_target = target, source
    else:
        verb_source, verb_target = source, target
    try:
        set_edge(
            board.store_path,
            action=action,
            kind=kind,
            source=verb_source,
            target=verb_target,
        )
    except TaskNotFoundError as exc:
        # The id passed the cached-union fast-path but is absent from the
        # GLOBAL store the verb writes (a lane-only card, or a race). Match
        # the message PREFIX, not a bare substring — the missing id's repr
        # appears after the prefix, so an id containing "source" must not
        # flip the answer.
        missing = (
            verb_source
            if str(exc).startswith("set_edge: unknown source id ")
            else verb_target
        )
        return JsonResponse({"error": f"no task with id {missing!r}"}, status=404)
    _reset_cache()
    logger.info(
        "[scitex-cards] edge %s %s %s->%s in %s",
        action,
        kind,
        source,
        target,
        board.store_path,
    )
    # Response deliberately excludes set_edge's `subscribed` key — the FE
    # contract for this endpoint predates it.
    return JsonResponse(
        {
            "action": action,
            "kind": kind,
            "source": source,
            "target": target,
            "store_path": str(board.store_path),
        }
    )


# EOF
