#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite backend for CARDS + COMMENTS (Phase 2 of the store migration).

Why
---
Phase 1 (:mod:`scitex_todo._inbox_sqlite`) moved the per-recipient INBOX onto
SQLite — a 5 s digest poll went from a ~1 s whole-store YAML parse to a
3-5 ms indexed lookup. CARDS and COMMENTS still live in the SAME monolithic
YAML document (``~/.scitex/todo/tasks.yaml``): every ``add_task`` /
``update_task`` / ``comment_task`` / ``reassign_task`` re-serialises the
ENTIRE store under an flock. On the live store (measured 2026-07-10) a bulk
reassignment runs at ~2.5 minutes PER CARD because of this — the direct
cause of card ``todo-board-comment-post-slow-20260705``.

This module does for cards/comments what Phase 1 did for inboxes: two
tables (``tasks`` + ``comments``) in the SAME runtime SQLite DB
(``<store_dir>/runtime/todo.db``), with indexes on ``comments(task_id)`` /
``tasks(status)`` / ``tasks(agent)`` so a single-card mutation touches O(1)
rows instead of O(whole-store).

This is a THIN ORCHESTRATOR — the 512-line-per-file hook forced the actual
implementation into three focused modules, re-exported here so callers keep
writing ``from scitex_todo import _store_sqlite as sq; sq.add_task(...)``
exactly as if this were one file:

* ``_store_sqlite_schema.py``  — DB path / connection / DDL / row<->dict.
* ``_store_sqlite_migrate.py`` — the lazy one-time YAML->SQLite migration.
* ``_store_sqlite_crud.py``    — the 8 public CRUD verbs.

Scope (Slice A only)
---------------------
This backend implements ONLY the CRUD surface + lazy migration + closed-enum
validation parity with the YAML store. It deliberately does NOT replicate
every side effect :mod:`scitex_todo._store` performs on a mutation: no
card-event emission (the hook bus), no active-unblock drive, no liveness
heartbeat, no WIP gate on ``add_task``. These are legitimate gaps versus
full production parity, not bugs — the backend defaults OFF (see
:mod:`scitex_todo._store_backend`) so nothing here changes fleet behaviour
until a deliberate follow-up flips the default and wires those in (mirrors
how Phase 1 only flipped its default after proving the storage swap alone).

FAIL LOUD: no silent fallback to YAML on a SQLite error. Any exception here
propagates to the caller.
"""

from __future__ import annotations

from ._store import TaskNotFoundError  # noqa: F401 — re-exported for parity
from ._store_sqlite_crud import (
    add_task,
    comment_task,
    complete_task,
    delete_task,
    get_task,
    list_tasks,
    reassign_task,
    update_task,
)
from ._store_sqlite_migrate import ensure_ready as _ensure_ready  # noqa: F401
from ._store_sqlite_migrate import migrate_into_conn as _migrate_into_conn  # noqa: F401
from ._store_sqlite_migrate import migrate_to_sqlite
from ._store_sqlite_schema import (
    ENV_STORE_DB,
    SCHEMA_VERSION,
    init_schema,
    open_connection,
    store_db_path,
)

__all__ = [
    "ENV_STORE_DB",
    "SCHEMA_VERSION",
    "TaskNotFoundError",
    "add_task",
    "comment_task",
    "complete_task",
    "delete_task",
    "get_task",
    "init_schema",
    "list_tasks",
    "migrate_to_sqlite",
    "open_connection",
    "reassign_task",
    "store_db_path",
    "update_task",
]

# EOF
