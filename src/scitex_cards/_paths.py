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

    A SERVER STORE HAS NO DIRECTORY, and that is the whole reason this function
    stopped deriving from the database unconditionally. ``$SCITEX_CARDS_DB`` may
    now name a PostgreSQL server, and ``resolve_db_path`` RAISES on one rather
    than coerce it (``Path("postgresql://h/db")`` silently collapses to the
    relative ``postgresql:/h/db``). Every caller here — the users/groups sidecar,
    pidfiles, the delivery ledger, reminder state — wants a LOCAL directory, and
    wants one just as much when the cards live on a server. Deriving it from the
    store identity welded the two together, so pointing the fleet at PostgreSQL
    made the whole query side raise before it ever opened a connection.

    Measured 2026-07-31, the failure this removes::

        SCITEX_CARDS_DB=postgresql:///scitex_cards  scitex-cards list-tasks
        StoreTargetIsNotAPath: names a PostgreSQL server, not a file path

    Card DATA never needed this path: :func:`scitex_cards._model.load_doc` calls
    ``_read_canonical_db_or_raise()`` with NO argument and interpolates the path
    into an error message only. So the two axes are genuinely independent, and
    are now resolved independently:

    - store IDENTITY — ``$SCITEX_CARDS_DB``; a path OR a server URL
    - local state DIR — always a real directory, whatever the backend

    On a server store the local root is ``~/.scitex/cards`` (``$SCITEX_DIR``
    aware), the same ambient default a fresh install uses.
    """
    from ._store_url import is_postgres_url, reject_attempted_dsn

    # A MALFORMED DSN IS NOT A PATH EITHER, and the paragraph below is the
    # reason this line exists rather than an extra spelling in that predicate.
    # `is_postgres_url` is TWO-VALUED: it separates "a server" from "everything
    # else", and a DSN that has been through Path() -- "postgresql:/host/db",
    # one slash -- lands in "everything else" alongside genuine filenames. So
    # the redirect below catches the well-formed case the 2026-08-02 incident
    # was about and misses the mangled form that incident actually produced.
    #
    # Measured on develop 2026-08-12, WITH the connect-door guards of #815
    # already merged:
    #     SCITEX_CARDS_DB='postgresql:/scitex_cards@127.0.0.1:55432/…'
    #     inbox_db_path() -> postgresql:/scitex_cards@…/runtime/todo.db
    #     and the directory tree was created under the process's CWD.
    # The guards were downstream: runtime_dir() mkdirs during PATH DERIVATION,
    # before any connect happens, so a check at the connect door cannot see it.
    #
    # RAISES rather than redirecting, and the asymmetry with the valid-DSN
    # branch is deliberate. A well-formed DSN is a legitimate deployment whose
    # runtime state simply belongs locally. A malformed one is a configuration
    # error with no correct interpretation -- there is no deployment for which
    # SCITEX_CARDS_DB=":55432" is right -- and quietly serving it a local
    # directory would hide the misconfiguration behind working software.
    reject_attempted_dsn(explicit)

    if explicit is not None:
        # An EXPLICIT server store gets the same answer as an ambient one.
        # Without this the two branches disagreed: ambient returned the local
        # root, while explicit fell through to Path(), which does not reject a
        # DSN — it COLLAPSES it to a relative path
        # (``postgresql:/scitex_cards@127.0.0.1:5432/scitex_cards``). Callers
        # then created that tree under their own CWD and wrote there.
        #
        # The failure was a silent SUCCESS, which is why it survived. Measured
        # 2026-08-02: enqueue(store=<DSN>) returned a notification id and left a
        # phantom store at ``<CWD>/postgresql:/…/runtime/todo.db``. Nothing
        # raised, so the fail-soft caller logged nothing, and the notification
        # was unreachable because nobody polls a directory named after a DSN.
        #
        # Every caller of this function wants a LOCAL directory (pidfiles, the
        # delivery ledger, reminder state, the inbox sidecar) and wants one just
        # as much when the cards live on a server — that is this function's
        # stated contract above. So a DSN resolves to the local root here too,
        # rather than raising: raising would break the board, which legitimately
        # threads its store through to the inbox rail.
        if is_postgres_url(str(explicit)):
            return _user_root() / "tasks.yaml"
        return Path(explicit).expanduser()

    from ._store_target import StoreTargetNotConfigured, resolve_store_target

    # NO STORE CONFIGURED IS NOT NO LOCAL STATE, and this is the one place that
    # distinction has to be made in code rather than in the docstring above.
    # Since 2026-08-13 the zero-config SQLite default RAISES instead of naming a
    # database, so the derivation at the bottom of this function has nothing
    # left to derive from -- but pidfiles, the delivery ledger, reminder state
    # and the users/groups sidecar all still want a real local directory, and
    # want one just as much when nobody has chosen a board yet as when the cards
    # live on a server. The two axes this function's contract calls independent
    # stay independent when the store axis has no answer at all.
    #
    # This is NOT the abolished fallback wearing a different hat: the answer is
    # a DIRECTORY, the same `_user_root()` a server store already gets. It names
    # no database, opens nothing, and holds no cards -- so it cannot become a
    # second board the way `~/.scitex/cards/cards.db` did. Raising here instead
    # would take down the whole query side exactly as the DSN coercion did on
    # 2026-07-31, for a question the caller never asked.
    try:
        ambient = resolve_store_target(None)
    except StoreTargetNotConfigured:
        return _user_root() / "tasks.yaml"

    # The AMBIENT branch needs the same check as the explicit one above: a
    # malformed $SCITEX_CARDS_DB reaches here with explicit=None, so guarding
    # only the argument would leave the commonest configuration mistake --
    # a typo in the environment -- on the unguarded path.
    reject_attempted_dsn(ambient)
    if is_postgres_url(ambient):
        return _user_root() / "tasks.yaml"

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
    from ._store_url import is_postgres_url

    # A SERVER TARGET CANNOT BE MANUFACTURED BY A WRITE, so the question this
    # guard asks does not arise. The hazard it exists for is filesystem-shaped:
    # an ambiently-resolved PATH that does not exist gets CREATED, and the empty
    # store then looks real to everything that reads it. Connecting to a
    # PostgreSQL server creates no database — the server either has it or the
    # connection fails loudly. Returning early is therefore not a relaxation;
    # asking `Path(dsn).exists()` would be, because it is False for every DSN
    # and would make this refuse every server write unconditionally.
    if is_postgres_url(resolved):
        return

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
