#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read path of the user registry — section load, read cache, resolution.

Split out of ``_store.py`` (line-budget refactor, 2026-07-18) together with
``_store_write.py``; ``_store.py`` remains the thin orchestrator re-exporting
the public API. Everything here is READ-ONLY against the store file; the
write path (``_store_write``) keeps its own UNCACHED section reads under the
store lock and is never served from the cache below.
"""

from __future__ import annotations

import copy
from pathlib import Path

from .._paths import resolve_tasks_path
from ._model import User, UserValidationError, validate_user


def _resolved_store(store: str | Path | None) -> Path:
    """Resolve a store path through the same chain the task API uses."""
    return resolve_tasks_path(store) if store is None else Path(store).expanduser()


def _load_users_section(path: Path) -> list[dict]:
    """Read the raw ``users:`` list off disk (absent / non-list → []).

    Uses the fast safe loader (:func:`scitex_cards._yaml.safe_load`) — this is a
    READ-only snapshot re-parsed on every ``resolve_user``, so the libyaml
    speedup matters; the ruamel round-trip is only needed on the WRITE path to
    preserve comments. Validates each row via :func:`validate_user` so a
    malformed registry fails loud on read.
    """
    if not path.exists():
        return []
    from .._yaml import safe_load

    with path.open(encoding="utf-8") as handle:
        data = safe_load(handle) or {}
    users = data.get("users")
    if not isinstance(users, list):
        return []
    out: list[dict] = []
    for row in users:
        if not isinstance(row, dict):
            raise UserValidationError(f"{path}: each user must be a mapping: {row!r}")
        validate_user(row)
        out.append(row)
    return out


"""Validated ``users:`` rows per store path, guarded by ``(mtime_ns, size)``.

Same shape as ``_threads._READ_CACHE`` (the ``services.get_board`` pattern):
any store write rolls the mtime forward, so no reader is served stale rows
across a write. The registry is tiny (a handful of rows) but lives inside the
multi-MB shared store file — the cache skips the FULL-STORE parse (measured
2.7–4.0 s per call on the 8.8 MB live store; the GUI ``/dm/threads`` paid it
on every request), not the row validation, which runs once per cache fill.
READ-ONLY: :func:`load_users` deep-copies rows on the way out because
``User.from_dict`` aliases nested lists — handing out cached rows directly
would let a caller mutate the cache. Write paths use the uncached
:func:`_load_users_section` under the store lock and never read from here.
"""
_READ_CACHE: dict[str, tuple[int, int, list[dict]]] = {}


def _load_users_section_cached(path: Path) -> list[dict]:
    """:func:`_load_users_section` memoized on the file's ``(mtime_ns, size)``.

    Benign race (same as the threads cache): a write landing between our
    ``stat`` and the parse can cache newer content under the older key; the
    next call sees the fresh mtime and re-parses, so staleness never survives
    a subsequent read. Absent file → ``[]`` and nothing cached. A validation
    error propagates and caches nothing — the next call retries the parse.
    """
    try:
        stat = path.stat()
    except OSError:
        return []
    key = str(path)
    cached = _READ_CACHE.get(key)
    if (
        cached is not None
        and cached[0] == stat.st_mtime_ns
        and cached[1] == stat.st_size
    ):
        return cached[2]
    users = _load_users_section(path)
    _READ_CACHE[key] = (stat.st_mtime_ns, stat.st_size, users)
    return users


# --------------------------------------------------------------------------- #
# Public read API                                                             #
# --------------------------------------------------------------------------- #
def load_users(store: str | Path | None = None) -> list[User]:
    """Return all registered users (absent ``users:`` section → ``[]``).

    Read-only snapshot — does NOT lock. Rows are deep-copied before
    :meth:`User.from_dict` so mutating a returned user (or its nested lists)
    can never poison a cache.

    AMBIENT CALLS READ THE SHARED DATABASE; an explicit ``store`` keeps the
    local file. See :func:`scitex_cards._db_users.registry_is_database` for
    why the split falls there — in short, the registry is fleet identity, and
    the local path it used to resolve to is a per-container private overlay,
    so the answer it gave was never shared with anyone.

    The mtime-guarded cache below applies to the FILE path only. It is keyed
    on ``(mtime_ns, size)``, which a database has no equivalent of, and the
    thing it exists to skip — a multi-MB full-store YAML parse per call — is
    not what a one-table ``SELECT`` costs.
    """
    from .._db_users import load_users_rows, registry_is_database

    if registry_is_database(store):
        return [User.from_dict(copy.deepcopy(d)) for d in load_users_rows()]
    path = _resolved_store(store)
    return [User.from_dict(copy.deepcopy(d)) for d in _load_users_section_cached(path)]


def list_users(store: str | Path | None = None) -> list[User]:
    """Alias of :func:`load_users` — return every registered user."""
    return load_users(store)


def get_user(user_id: str, store: str | Path | None = None) -> "User | None":
    """Return the user with id ``user_id``, or ``None`` if not registered."""
    if not user_id:
        return None
    for user in load_users(store):
        if user.id == user_id:
            return user
    return None


def resolve_user(
    name_or_host_at_name: str,
    store: str | Path | None = None,
) -> "User | None":
    """Resolve a card-owner string to its registered :class:`User`.

    Resolution order:

    1. EXACT match against any user's ``names`` alias (so an OLD name still
       resolves after a rename, as long as the old name was kept in
       ``names``).
    2. Match against any user's ``host_at_name`` join key.
    3. CANONICALISED retry: run the raw string through
       :func:`scitex_cards._users.canonical_identity` (NON-strict) to collapse
       naming drift (``proj-scitex-dev`` -> ``scitex-dev``, ``sac`` ->
       ``scitex-agent-container``) and re-try steps 1/2 on the canonical
       form. This only ever ADDS a resolution that steps 1/2 missed — a
       string that resolved before still resolves the same way — so it is a
       pure, back-compatible widening.

    Returns ``None`` when the string maps to no registered user — callers
    fall back to the raw name string, which preserves back-compat with the
    pre-registry world where owners were just free-form strings.
    """
    if not name_or_host_at_name:
        return None
    users = load_users(store)

    def _exact(key: str) -> "User | None":
        for user in users:
            if key in user.names:
                return user
        for user in users:
            if user.host_at_name and user.host_at_name == key:
                return user
        return None

    hit = _exact(name_or_host_at_name)
    if hit is not None:
        return hit

    # (3) canonicalise (non-strict, reusing the snapshot already loaded) and
    # retry — never raises, only widens.
    from ._identity import canonical_identity

    canonical = canonical_identity(name_or_host_at_name, users=users, strict=False)
    if canonical != name_or_host_at_name:
        return _exact(canonical)
    return None


__all__ = [
    "get_user",
    "list_users",
    "load_users",
    "resolve_user",
]

# EOF
