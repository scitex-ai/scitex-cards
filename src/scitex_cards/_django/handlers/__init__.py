#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Handler package -- thin Django wrappers around the scitex-cards Python API.

Exports the ``HANDLERS`` dict consumed by the catch-all dispatcher in
``views.api_dispatch``. Each handler takes ``(request, board)`` and returns a
``JsonResponse``.
"""

from .crud import handle_comment, handle_create, handle_update
from .edge import handle_edge
from .graph import handle_graph, handle_ping, handle_rev, handle_tasks
from .nudge import handle_nudge
from .priority import handle_priority
from .reopen import handle_reopen
from .rescore import handle_rescore
from .resolve import handle_resolve
from .stale import handle_archive, handle_stale
from .undo import handle_delete, handle_restore

# endpoint string -> handler function
HANDLERS = {
    "graph": handle_graph,
    "tasks": handle_tasks,
    "ping": handle_ping,
    "rev": handle_rev,
    "priority": handle_priority,
    # Matrix-view drag -> re-score a card's urgency+importance; the rank
    # engine recomputes the whole order server-side (ADR-0011 §8). Delegates
    # to the locked `rescore_task` verb so the `rank_changed` event reaches
    # agents (a handler-flock write would be atomic but eventless).
    "rescore": handle_rescore,
    "create": handle_create,
    "update": handle_update,
    "delete": handle_delete,
    "comment": handle_comment,
    "edge": handle_edge,
    "restore": handle_restore,
    "resolve": handle_resolve,
    "reopen": handle_reopen,
    "nudge": handle_nudge,
    # Stale-cards review panel + archive button (2026-06-13, operator
    # via lead a2a). HTTP twin of CLI `close --reason` (PR #151).
    "stale": handle_stale,
    "archive": handle_archive,
}

# Endpoints that work without a loaded board (health checks).
NO_BOARD_ENDPOINTS = {"ping"}

__all__ = ["HANDLERS", "NO_BOARD_ENDPOINTS"]

# EOF
