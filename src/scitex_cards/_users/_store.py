#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistence + registry API for the standalone user registry (orchestrator).

Users live in the SAME store file as tasks, under a top-level ``users:``
key (a sibling of ``tasks:``) — there is NO separate users file. The
implementation is split by data-flow direction (line-budget refactor,
2026-07-18):

    _store_read    section load + mtime-guarded read cache + load/list/get/
                   resolve
    _store_write   atomic section write + register/alias/notify/heartbeat
                   (uncached reads under the shared store lock)

This module re-exports the full public API (and the private helpers tests
exercise directly) so every existing ``from scitex_cards._users._store
import X`` keeps working unchanged.

Standalone constraint: ZERO external-runtime / fleet imports. The id format
is ``u_`` + 12 hex chars (48 bits, :func:`secrets.token_hex`); ids are
generated in ``_store_write``, stable for life, and never reused.
"""

from __future__ import annotations

from ._store_read import (  # noqa: F401  (public + test-pinned re-exports)
    _READ_CACHE,
    _load_users_section,
    _load_users_section_cached,
    _resolved_store,
    get_user,
    list_users,
    load_users,
    resolve_user,
)
from ._store_write import (  # noqa: F401  (public + test-pinned re-exports)
    _generate_user_id,
    _names_index,
    _save_users_unlocked,
    _utc_now_iso,
    add_alias,
    deterministic_user_id,
    register_user,
    set_notify,
    touch_user,
)

__all__ = [
    "add_alias",
    "deterministic_user_id",
    "get_user",
    "list_users",
    "load_users",
    "register_user",
    "resolve_user",
    "set_notify",
    "touch_user",
]

# EOF
