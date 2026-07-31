#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve the store TARGET without assuming it is a filesystem path.

WHY THIS IS NOT IN ``_db.resolve_db_path``. That function is typed ``-> Path``
and every one of its callers expects a ``Path``, so it cannot represent a
``postgresql://`` URL. What it does instead is worse than failing::

    SCITEX_CARDS_DB=postgresql://host/db  ->  Path("postgresql:/host/db")

a RELATIVE path, silently, with no error. A store URL that resolves to a path
is not a slightly-wrong answer -- it is a different store, and the caller then
creates an empty SQLite file at that name and reports a healthy, empty board.
That is the two-stores-both-look-healthy failure this package already has scar
tissue from.

Measured 2026-07-31: the ``_backend_connect`` seam and its paramstyle layer are
implemented and tested, and NOTHING in the package imports them -- every read
and write still calls ``sqlite3.connect`` directly. Path resolution is the
reason. Until the resolver can carry a URL, no call site can reach PostgreSQL
no matter what else is ported, which makes this the smallest change that
unblocks the rest.

This module deliberately does NOT change ``resolve_db_path``. Callers that
genuinely need a filesystem path (snapshots, backups, the on-disk health
probes) keep using it; callers that can address either backend take
:func:`resolve_store_target` and hand the result to ``_backend_connect.connect``.
"""

from __future__ import annotations

import os
from pathlib import Path

from ._db import DEFAULT_DB_FILENAME, ENV_DB, ENV_DB_DEPRECATED, PKG_SHORT
from ._store_url import BACKEND_SQLITE, backend_of, is_postgres_url

__all__ = [
    "StoreTargetIsNotAPath",
    "resolve_store_target",
    "resolve_store_backend",
    "require_db_path",
]


class StoreTargetIsNotAPath(ValueError):
    """The resolved store is a URL, and the caller demanded a filesystem path.

    Raised instead of returning a mangled ``Path`` so the failure is loud at the
    call site that cannot cope, rather than silent at a call site that then
    creates the wrong store.
    """


def resolve_store_target(explicit: str | Path | None = None) -> str:
    """The store target AS WRITTEN -- a path or a URL, never coerced.

    Mirrors ``_db.resolve_db_path``'s precedence exactly (explicit argument,
    then ``$SCITEX_CARDS_DB``, then the deprecated ``$SCITEX_TODO_DB``, then the
    ecosystem user-canonical default) and differs only in refusing to turn the
    answer into a ``Path``.

    The deprecation warning is deliberately NOT re-emitted here -- ``_db``
    already warns on that tier, and warning twice for one resolution trains
    readers to ignore it.
    """
    if explicit is not None:
        return str(explicit)
    for env_name in (ENV_DB, ENV_DB_DEPRECATED):
        value = os.environ.get(env_name)
        if value:
            return value
    # Same final tier as _db.resolve_db_path, imported lazily for the same
    # reason: a caller with an explicit or env target must not hard-require
    # scitex_config to be importable.
    from scitex_config._ecosystem import local_state

    return str(local_state.user_path(PKG_SHORT, DEFAULT_DB_FILENAME))


def resolve_store_backend(explicit: str | Path | None = None) -> str:
    """Which backend the resolved target names, without opening anything."""
    return backend_of(resolve_store_target(explicit))


def require_db_path(explicit: str | Path | None = None) -> Path:
    """The resolved target as a ``Path``, or a loud refusal if it is a URL.

    For the callers that are genuinely filesystem-only. Use this rather than
    ``resolve_db_path`` wherever handing a URL to path logic would create a
    second store instead of erroring.
    """
    target = resolve_store_target(explicit)
    if is_postgres_url(target):
        raise StoreTargetIsNotAPath(
            f"the store resolves to a {backend_of(target)} URL, not a file; "
            "this caller requires a filesystem path. Use "
            "resolve_store_target() with _backend_connect.connect() instead of "
            "coercing the URL to a Path -- coercion yields a RELATIVE path and "
            "silently creates a different, empty store."
        )
    return Path(target).expanduser()


def _assert_sqlite_default() -> str:
    """Kept as a named check so the default cannot drift unnoticed."""
    return BACKEND_SQLITE


# EOF
