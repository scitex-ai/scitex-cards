#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical identity resolver — collapse naming drift to ONE name.

Card owners / creators / assignees are free-form strings, so the SAME
entity keeps showing up under many names and inflates the registry
(e.g. 45 "owners" for ~20 real entities). Two kinds of drift:

1. **Mechanical** (derivable) — ``proj-scitex-dev`` vs ``scitex-dev``,
   ``proj-paper-neurovista`` vs ``neurovista``, ``lead-ywata-note-win``
   vs ``lead``. Collapsed by stripping the known leading prefixes
   ``proj-paper-`` / ``proj-`` and a trailing ``-<host>`` for a known host.
2. **Synonym** (must be DECLARED) — ``sac`` ≡ ``scitex-agent-container``,
   ``orochi`` ≡ ``scitex-orochi``. Collapsed via the explicit
   :data:`IDENTITY_ALIASES` table.

This module is a PURE resolver: :func:`canonical_identity` takes the
registry snapshot + the alias / host tables as arguments and returns a
canonical name string. :func:`resolve_identity` is the thin, store-aware
convenience wrapper that loads the ``users:`` snapshot and reads the
``SCITEX_CARDS_STRICT_IDENTITY`` env gate for the ``strict`` default.

Standalone constraint: like the rest of :mod:`scitex_cards._users`, this
module imports NOTHING from any external agent runtime / fleet package. It
only reads the local registry snapshot the caller hands it.

Distinction from :func:`scitex_cards._ports.canonical_agent_id`
--------------------------------------------------------------
``canonical_agent_id`` normalises the ``host@name`` JOIN KEY only; it does
NOT strip prefixes or collapse synonyms. This resolver is the layer ABOVE
that: it maps a drifted display name to the entity's ONE canonical name.
The two are complementary, not interchangeable.
"""

from __future__ import annotations

import os
from typing import Iterable, Mapping

from ._model import User

#: Env flag that flips the DEFAULT of ``strict`` from OFF to ON for the
#: store-aware :func:`resolve_identity`. OFF by default on purpose: an
#: unregistered owner must NOT break resolution before the fleet is
#: registered. Enabling fail-loud-on-unknown is a deliberate flip AFTER
#: every real entity has a registry record (its ``names[]`` alias) or an
#: alias-table entry. Truthy values: ``1 / true / yes / on`` (case-insensitive).
ENV_STRICT_IDENTITY = "SCITEX_CARDS_STRICT_IDENTITY"

#: Explicit synonym table: canonical name <- {declared aliases}. SEEDED
#: exactly per the drift audit; extend by adding rows here (or by passing an
#: overriding ``aliases`` mapping to :func:`canonical_identity`). Keys are the
#: CANONICAL name; each value lists the aliases that collapse onto it.
#: Kept as canonical->aliases (not alias->canonical) so a human editing the
#: table sees, per entity, every name that folds into it.
IDENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "scitex-agent-container": ("sac",),
    "scitex-orochi": ("orochi",),
}

#: Known host suffixes stripped from a trailing ``-<host>`` during the
#: mechanical normalise step (e.g. ``lead-ywata-note-win`` -> ``lead``).
#: Extend as new hosts join the fleet.
KNOWN_HOSTS: tuple[str, ...] = (
    "ywata-note-win",
    "spartan",
    "orochi",
    "mba",
    "nas",
)

#: Leading prefixes stripped during the mechanical normalise step. ORDER
#: MATTERS: the LONGEST / most-specific prefix is tried first so
#: ``proj-paper-neurovista`` strips ``proj-paper-`` (-> ``neurovista``)
#: rather than only ``proj-`` (-> ``paper-neurovista``).
IDENTITY_PREFIXES: tuple[str, ...] = ("proj-paper-", "proj-")


class UnknownIdentityError(ValueError):
    """Raised (only under ``strict=True``) for a name that resolves to nothing.

    The message always echoes the offending value plus an actionable hint,
    per the fail-loud SciTeX convention.
    """


def _alias_to_canonical(aliases: Mapping[str, Iterable[str]]) -> dict[str, str]:
    """Invert a canonical->aliases table into a flat alias->canonical map.

    Fail-loud on a collision (the SAME alias declared under two different
    canonical names is an authoring bug, not a silent last-wins). A canonical
    name is also accepted as an alias of itself so ``canonical(canonical) ==
    canonical`` holds even when only the alias side was declared.
    """
    flat: dict[str, str] = {}
    for canonical, alias_list in aliases.items():
        for alias in (canonical, *alias_list):
            existing = flat.get(alias)
            if existing is not None and existing != canonical:
                raise ValueError(
                    f"identity alias {alias!r} is declared under two "
                    f"canonical names {existing!r} and {canonical!r}; "
                    f"fix the IDENTITY_ALIASES table"
                )
            flat[alias] = canonical
    return flat


def _registered_canonical(
    name: str, users: Iterable["User | Mapping"]
) -> str | None:
    """Exact match against any user's ``names`` / ``id`` / ``host_at_name``.

    Returns the user's CANONICAL display name — the FIRST entry of its
    ``names`` list (the convention: current name first, historical aliases
    after) — or ``None`` when no user matches. Never raises; a malformed row
    is skipped.
    """
    for user in users:
        d = user.to_dict() if isinstance(user, User) else user
        if not isinstance(d, Mapping):
            continue
        names = list(d.get("names") or [])
        if not names:
            continue
        if (
            name in names
            or name == d.get("id")
            or (d.get("host_at_name") and name == d.get("host_at_name"))
        ):
            return names[0]
    return None


def _mechanical_normalize(name: str, hosts: Iterable[str]) -> str:
    """Strip a known leading prefix and a trailing ``-<host>`` (idempotent-ish).

    Applies the LONGEST matching prefix from :data:`IDENTITY_PREFIXES` (one
    strip — prefixes are not stacked) and then the FIRST matching
    ``-<host>`` suffix. Returns the input unchanged when nothing matches.
    Never strips down to an empty string (a bare host/prefix stays as-is).
    """
    result = name
    for prefix in IDENTITY_PREFIXES:
        if result.startswith(prefix) and len(result) > len(prefix):
            result = result[len(prefix):]
            break
    for host in hosts:
        suffix = f"-{host}"
        if result.endswith(suffix) and len(result) > len(suffix):
            result = result[: -len(suffix)]
            break
    return result


def canonical_identity(
    name: str,
    *,
    users: Iterable["User | Mapping"] = (),
    aliases: Mapping[str, Iterable[str]] = IDENTITY_ALIASES,
    hosts: Iterable[str] = KNOWN_HOSTS,
    strict: bool = False,
) -> str:
    """Collapse a (possibly drifted) identity string to its ONE canonical name.

    PURE: reads only the arguments, mutates nothing, no I/O. The store-aware
    wrapper is :func:`resolve_identity`.

    Precedence
    ----------
    a. EXACT match against any registered user's ``names`` / ``id`` /
       ``host_at_name`` -> that user's canonical name (first of ``names``).
    b. else the ALIAS table (``sac`` -> ``scitex-agent-container`` etc.).
    c. else MECHANICAL normalise (strip ``proj-paper-`` / ``proj-`` and a
       trailing ``-<host>``), then RE-CHECK (a) then (b) on the normalised
       form (so ``proj-scitex-dev`` -> ``scitex-dev`` -> a registered hit if
       one exists, and ``lead-ywata-note-win`` -> ``lead``).
    d. else UNKNOWN: ``strict=True`` raises :class:`UnknownIdentityError`
       with an actionable hint; ``strict=False`` returns the input UNCHANGED
       (back-compat for unregistered owners before the fleet is registered).

    Parameters
    ----------
    name : str
        The raw identity string (a card ``agent`` / ``assignee`` / creator /
        collaborator name, or a ``u_*`` id / ``host@name``).
    users : iterable of User | Mapping
        The registry snapshot to match (a) against. Empty (the default) skips
        the registered-name step — pure alias + mechanical resolution.
    aliases : mapping
        canonical->aliases synonym table (default :data:`IDENTITY_ALIASES`).
    hosts : iterable of str
        Known host names whose trailing ``-<host>`` suffix is strippable
        (default :data:`KNOWN_HOSTS`).
    strict : bool
        Fail-loud on an unknown identity. OFF by default; the store-aware
        wrapper gates this on :data:`ENV_STRICT_IDENTITY`.

    Returns
    -------
    str
        The canonical name (or, under ``strict=False``, the input unchanged
        when nothing resolves).

    Raises
    ------
    UnknownIdentityError
        Only when ``strict=True`` and ``name`` resolves to nothing.
    """
    if not (isinstance(name, str) and name.strip()):
        raise UnknownIdentityError(
            f"identity must be a non-empty string (got {name!r})"
        )
    key = name.strip()
    users = list(users)
    flat_aliases = _alias_to_canonical(aliases)
    hosts = tuple(hosts)

    # (a) registered exact match on the raw key.
    hit = _registered_canonical(key, users)
    if hit is not None:
        return hit
    # (b) alias table on the raw key.
    if key in flat_aliases:
        return flat_aliases[key]

    # (c) mechanical normalise, then re-check (a) then (b).
    normalized = _mechanical_normalize(key, hosts)
    if normalized != key:
        hit = _registered_canonical(normalized, users)
        if hit is not None:
            return hit
        if normalized in flat_aliases:
            return flat_aliases[normalized]
        # A mechanical strip is itself a canonicalisation win even with no
        # registry/alias hit (e.g. `lead-ywata-note-win` -> `lead`): the
        # stripped form IS the canonical name.
        return normalized

    # (d) unknown.
    if strict:
        raise UnknownIdentityError(
            f"unknown identity {name!r} — register it (add it to a user's "
            f"names[] alias via scitex_cards._users.register_user / add_alias) "
            f"or add an alias to scitex_cards._users.IDENTITY_ALIASES; see "
            f"scitex_cards._users._identity"
        )
    return key


def strict_identity_enabled() -> bool:
    """Whether fail-loud-on-unknown is enabled via :data:`ENV_STRICT_IDENTITY`.

    Read at CALL time (not import time) so a deliberate flip takes effect
    without a re-import. Truthy: ``1 / true / yes / on`` (case-insensitive).
    Default (unset / anything else) is ``False`` — strict is OFF by default.
    """
    raw = os.environ.get(ENV_STRICT_IDENTITY)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def resolve_identity(
    name: str,
    *,
    store=None,
    strict: bool | None = None,
    aliases: Mapping[str, Iterable[str]] = IDENTITY_ALIASES,
    hosts: Iterable[str] = KNOWN_HOSTS,
) -> str:
    """Store-aware :func:`canonical_identity` — load users + gate ``strict``.

    Loads the ``users:`` snapshot from ``store`` (the resolved task store
    when ``None``) and forwards to :func:`canonical_identity`. When ``strict``
    is ``None`` (the default) the flag is read from
    :data:`ENV_STRICT_IDENTITY` via :func:`strict_identity_enabled` — so
    resolution is NON-raising by default and only fails loud after the
    deliberate env flip.
    """
    from ._store import load_users

    if strict is None:
        strict = strict_identity_enabled()
    return canonical_identity(
        name,
        users=load_users(store),
        aliases=aliases,
        hosts=hosts,
        strict=strict,
    )


__all__ = [
    "ENV_STRICT_IDENTITY",
    "IDENTITY_ALIASES",
    "IDENTITY_PREFIXES",
    "KNOWN_HOSTS",
    "UnknownIdentityError",
    "canonical_identity",
    "resolve_identity",
    "strict_identity_enabled",
]

# EOF
