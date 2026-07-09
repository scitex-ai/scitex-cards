#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backend switch for the CARDS/COMMENTS store (Phase 2 of the SQLite migration).

Mirrors the shape of :mod:`scitex_todo._inbox`'s backend switch, with the
DEFAULT INVERTED: ``SCITEX_TODO_STORE_BACKEND=yaml`` (the DEFAULT, unset also
means yaml) keeps every call routed to :mod:`scitex_todo._store` (the
existing, proven YAML mutation API) — the LIVE FLEET's current behaviour,
UNCHANGED. Only ``SCITEX_TODO_STORE_BACKEND=sqlite`` opts a caller into
:mod:`scitex_todo._store_sqlite`.

Why the default is NOT flipped (yet)
-------------------------------------
Phase 1 (the inbox backend) only flipped ITS default to sqlite after the
storage swap had been proven correct in production for a period — it did
NOT flip on the same PR that introduced the sqlite implementation. This
module follows the same discipline: ship the sqlite backend opt-in first,
let it prove itself, then flip the default in a SEPARATE, deliberate
follow-up change. Do not flip ``_DEFAULT`` below as part of landing the
sqlite implementation itself.

Why this switch is a standalone module, not added to ``_store.py``
--------------------------------------------------------------------
``_store.py`` (the YAML mutation API) is 1601 lines — already over the
512-line hook-enforced per-file edit cap — so this switch could not be
added in-place. ``_store.py`` needs a split into focused modules before
further additive work (this switch, future card-event/liveness parity work
on the sqlite side, etc.) can land inside it directly; that split is a
separate task, not attempted here.

Nothing imports this module yet (deliberately) — wiring it into the CLI /
MCP surface is a follow-up once the sqlite backend has been exercised.
Until then this module has ZERO effect on runtime behaviour.
"""

from __future__ import annotations

import os

from . import _store, _store_sqlite

#: Env var selecting the CARDS/COMMENTS storage backend. DEFAULT IS "yaml" —
#: the opposite of the inbox backend's default. See module docstring.
ENV_STORE_BACKEND = "SCITEX_TODO_STORE_BACKEND"


def _use_sqlite() -> bool:
    """True ONLY when explicitly opted in via ``SCITEX_TODO_STORE_BACKEND=sqlite``.

    Unset, empty, or any value other than the literal ``sqlite`` routes to
    the YAML backend — the safe, unchanged default.
    """
    return (os.environ.get(ENV_STORE_BACKEND) or "yaml").strip().lower() == "sqlite"


def add_task(*args, **kwargs):
    impl = _store_sqlite if _use_sqlite() else _store
    return impl.add_task(*args, **kwargs)


def update_task(*args, **kwargs):
    impl = _store_sqlite if _use_sqlite() else _store
    return impl.update_task(*args, **kwargs)


def get_task(*args, **kwargs):
    impl = _store_sqlite if _use_sqlite() else _store
    return impl.get_task(*args, **kwargs)


def list_tasks(*args, **kwargs):
    impl = _store_sqlite if _use_sqlite() else _store
    return impl.list_tasks(*args, **kwargs)


def comment_task(*args, **kwargs):
    impl = _store_sqlite if _use_sqlite() else _store
    return impl.comment_task(*args, **kwargs)


def reassign_task(*args, **kwargs):
    impl = _store_sqlite if _use_sqlite() else _store
    return impl.reassign_task(*args, **kwargs)


def complete_task(*args, **kwargs):
    impl = _store_sqlite if _use_sqlite() else _store
    return impl.complete_task(*args, **kwargs)


def delete_task(*args, **kwargs):
    impl = _store_sqlite if _use_sqlite() else _store
    return impl.delete_task(*args, **kwargs)


__all__ = [
    "ENV_STORE_BACKEND",
    "add_task",
    "comment_task",
    "complete_task",
    "delete_task",
    "get_task",
    "list_tasks",
    "reassign_task",
    "update_task",
]

# EOF
