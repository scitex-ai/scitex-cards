#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task-store path resolution — the store IS the SQLite database.

There is ONE store identity and it is ``$SCITEX_CARDS_DB`` (the database path).
:func:`resolve_tasks_path` returns that path; there is no separate, YAML-named
identity any more. It used to resolve a ``tasks.yaml`` PATH from a dedicated
``…_TASKS_YAML_SHARED`` variable, stamp that path into the database, and refuse a
write when the two disagreed — two identity axes that could drift apart, which is
exactly how the fleet went read-only on 2026-07-19/20. Collapsing to the single
``$SCITEX_CARDS_DB`` axis removes that failure class rather than guarding it.

The database path is USER-CANONICAL: it resolves to the same user file from any
working directory (see :func:`scitex_cards._db.resolve_db_path`), never a
per-repo copy. There is DELIBERATELY no project-scope layer for the data store —
a process run with cwd inside ANY repo must reach the same canonical store.
(Incident 2026-07-06: a board run from a repo silently read a week-stale project
copy that shadowed the canonical user store.) The reminders ``config`` — CONFIG,
not data — keeps its project-override layer in :mod:`scitex_cards._config`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: package short name (``scitex-cards`` with the ``scitex-`` prefix stripped).
#: It names the user-scope directory: ``~/.scitex/<PKG_SHORT>``.
PKG_SHORT = "cards"


def _user_root() -> Path:
    """User-scope ``.scitex/cards`` root, honouring ``$SCITEX_DIR``."""
    base = os.environ.get("SCITEX_DIR")
    root = Path(base).expanduser() if base else Path.home() / ".scitex"
    return root / PKG_SHORT


def _find_git_root(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a ``.git`` directory."""
    cur = start.resolve()
    for parent in (cur, *cur.parents):
        if (parent / ".git").exists():
            return parent
    return None


def resolve_tasks_path(explicit: str | Path | None = None) -> Path:
    """Resolve the non-task YAML CONTAINER path — NOT the store identity.

    The store IDENTITY is ``$SCITEX_CARDS_DB`` (the SQLite database); see
    :func:`scitex_cards._db.resolve_db_path`, and the ownership guard in
    :mod:`scitex_cards._dual_write` / :mod:`scitex_cards._store_backend` which
    stamps and compares THAT path. Card DATA lives in the database.

    This function returns the YAML container that BESIDES the database still
    holds the non-task sections — ``users:``, ``groups:`` — read by
    :mod:`scitex_cards._users` and :mod:`scitex_cards._groups` (the
    ``inboxes:`` and ``threads:`` sections have already migrated out, to
    their own ``inboxes.json`` / ``threads.json`` sidecars). That container
    is a SIDECAR pending further migration into the database; it is not a
    second store of record for tasks. Callers also use its ``.parent`` as the
    store directory (pidfiles, the delivery ledger, reminder state live there).

    Resolution: an explicit path wins outright; otherwise the container is the
    ``tasks.yaml`` beside the resolved database (``$SCITEX_CARDS_DB``'s dir), so
    there is no separate, YAML-named identity variable.
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    from ._db import resolve_db_path

    return resolve_db_path(None).parent / "tasks.yaml"


def refuse_ambient_store_creation(
    resolved: str | Path, explicit: str | Path | None = None
) -> None:
    """Refuse to MANUFACTURE a store at a path nobody named.

    A write against a store that does not exist is ambiguous: "first run, please
    bootstrap" or "I resolved the wrong path". Creating the store always assumes
    the first, and the second is the one that costs a board.

    Measured 2026-07-20, before this guard: three sac cron jobs, each reading a
    missing store as "no cards yet" and calling ``add_task``, grew a five-card
    document at an ambiently-resolved path; the hourly snapshot then imported it
    as canonical and reconcile deleted the 2160 cards absent from it.

    So an EXPLICIT destination may be created (naming a path states intent — how
    tests, imports and deliberate bootstraps work). An AMBIENT one may not:
    nothing named it, so a missing file there is far more likely to mean the
    resolution is wrong than that the fleet has no board yet. A set
    ``$SCITEX_CARDS_DB`` counts as naming it.

    Raises
    ------
    RuntimeError
        When ``resolved`` does not exist and nothing named it.
    """
    from ._db import ENV_DB

    path = Path(resolved)
    if path.exists() or explicit is not None or os.environ.get(ENV_DB):
        return
    raise RuntimeError(
        f"REFUSING to create a task store at {path}: it does not exist, and "
        f"nothing named it — the path came from the ambient default "
        f"(~/.scitex/{PKG_SHORT}/cards.db). Writing here would MANUFACTURE a "
        f"new board, which then looks like a real store to anything that reads "
        f"it.\n"
        f"If you meant to write to the fleet board, your store resolution is "
        f"wrong: set ${ENV_DB} to the real database, or pass the path "
        f"explicitly.\n"
        f"If you genuinely want a NEW empty board here, create it deliberately "
        f"first: `scitex-cards init-store`."
    )


#: Subdirectory of the store dir holding NON-git-tracked runtime state
#: (pidfiles, the delivery ledger, the reminder sidecar). scitex convention:
#: runtime state lives under ``runtime/`` (gitignored), never scattered in the
#: store root. Superseded files go to ``.old/<timestamp>/`` instead.
RUNTIME_DIRNAME = "runtime"


def runtime_dir(store: str | Path | None = None, *, create: bool = True) -> Path:
    """Return ``<store_dir>/runtime`` — the home for non-tracked runtime state.

    ``<store_dir>`` is the parent of the resolved store (the database's
    directory), so the runtime dir tracks whichever scope the store resolved to.
    Created on demand (``create=True``) so callers can write into it without a
    prior mkdir.
    """
    d = resolve_tasks_path(store).parent / RUNTIME_DIRNAME
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


# EOF
