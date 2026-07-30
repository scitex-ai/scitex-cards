#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Notify-config LOADING + validation: the global layer (built-ins + sidecar).

The config half of foundation C3 (see :mod:`scitex_cards._notify`). Holds the
fail-loud coercion helpers (shared by the sidecar reader AND the per-card
parser in :mod:`._resolver`), the ``notify.json`` sidecar reader, and
:func:`load_notify_config` which merges the built-in
:data:`~scitex_cards._notify._rules.DEFAULT_NOTIFY_RULES` with an optional
sidecar. Fail-loud on a malformed sidecar; zero-config returns the built-ins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .._paths import resolve_tasks_path
from ._rules import (
    DEFAULT_NOTIFY_RULES,
    EVENT_TYPES,
    NOTIFY_SIDECAR_NAME,
    VALID_ROLES,
    NotifyConfig,
    NotifyConfigError,
)


# --------------------------------------------------------------------------- #
# Validation / coercion helpers (fail-loud) — shared with the per-card parser #
# --------------------------------------------------------------------------- #
def validate_event_type(event_type: object, *, where: str) -> str:
    """Return ``event_type`` if it is a known event type, else fail loud."""
    if not (isinstance(event_type, str) and event_type in EVENT_TYPES):
        raise NotifyConfigError(
            f"{where}: unknown event type {event_type!r}; must be one of "
            f"{sorted(EVENT_TYPES)}"
        )
    return event_type


def validate_roles(value: object, *, where: str) -> list[str]:
    """Coerce + validate a role list (every item must be a known role)."""
    if not isinstance(value, (list, tuple)):
        raise NotifyConfigError(f"{where}: role list must be a list, got {value!r}")
    out: list[str] = []
    for role in value:
        if not (isinstance(role, str) and role in VALID_ROLES):
            raise NotifyConfigError(
                f"{where}: invalid role {role!r}; must be one of {sorted(VALID_ROLES)}"
            )
        out.append(role)
    return out


def coerce_rules_mapping(raw: object, *, where: str) -> dict[str, list[str]]:
    """Validate a ``{event_type: [role, ...]}`` mapping, returning a copy.

    Used for both the ``notify.json`` rules section and a per-card ``events``
    override — the shape is identical (event_type → role list).
    """
    if not isinstance(raw, Mapping):
        raise NotifyConfigError(
            f"{where}: must be a mapping of event_type -> [role, ...], got {raw!r}"
        )
    coerced: dict[str, list[str]] = {}
    for event_type, roles in raw.items():
        et = validate_event_type(event_type, where=where)
        coerced[et] = validate_roles(roles, where=f"{where}[{et!r}]")
    return coerced


def coerce_id_list(value: object, *, where: str) -> list[str]:
    """Coerce + validate a list of user ids / names (non-empty strings)."""
    if not isinstance(value, (list, tuple)):
        raise NotifyConfigError(
            f"{where}: must be a list of user ids/names, got {value!r}"
        )
    out: list[str] = []
    for item in value:
        if not (isinstance(item, str) and item):
            raise NotifyConfigError(
                f"{where}: each entry must be a non-empty string, got {item!r}"
            )
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# notify.json sidecar                                                         #
# --------------------------------------------------------------------------- #
def _read_sidecar(path: Path) -> dict[str, list[str]]:
    """Read + validate the notify sidecar's JSON rule overrides.

    Accepts two shapes for forward-compat / brevity:

    * ``{"rules": {event_type: [role, ...]}}`` — explicit ``rules:`` key.
    * ``{event_type: [role, ...]}`` — a bare top-level mapping (when no
      ``rules:`` key is present, the whole document is the rules map).

    Returns the validated overrides (an empty dict for an empty document).
    Raises :class:`NotifyConfigError` on a malformed file (fail-loud).
    """
    import json

    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else None
    except Exception as exc:  # noqa: BLE001 — malformed sidecar — fail loud
        raise NotifyConfigError(f"{path}: notify sidecar is not valid ({exc})") from exc

    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise NotifyConfigError(
            f"{path}: notify sidecar top level must be a mapping, got {data!r}"
        )
    raw_rules = data["rules"] if "rules" in data else data
    return coerce_rules_mapping(raw_rules, where=f"{path} rules")


def notify_sidecar_path(store: str | Path | None) -> Path | None:
    """Resolve the ``notify.json`` path that sits next to the task store.

    Reuses :func:`scitex_cards._paths.resolve_tasks_path` so the sidecar
    tracks the SAME store the tasks live in. Returns ``None`` only if the
    store path cannot be resolved (defensive; in practice
    ``resolve_tasks_path`` always returns a path).
    """
    try:
        tasks_path = (
            resolve_tasks_path(store) if store is None else Path(store).expanduser()
        )
    except Exception:  # noqa: BLE001 — never break on a path-resolution edge
        return None
    return tasks_path.parent / NOTIFY_SIDECAR_NAME


def load_notify_config(store: str | Path | None = None) -> NotifyConfig:
    """Return the GLOBAL notify layer: built-in defaults merged with sidecar.

    The built-in :data:`~scitex_cards._notify._rules.DEFAULT_NOTIFY_RULES` is
    the SSOT baseline. If a ``notify.json`` sidecar exists next to the
    resolved task store, its ``{event_type: [role, ...]}`` entries
    OVERRIDE the built-in entry for those event types (per-event replacement,
    not a deep merge). Events the sidecar does not mention keep their built-in
    default. With no sidecar the built-ins are returned unchanged (zero-config
    works out of the box).

    Parameters
    ----------
    store : str | pathlib.Path | None
        Store path override. ``None`` resolves through
        :func:`scitex_cards._paths.resolve_tasks_path` (the same chain the
        task API uses). The sidecar is looked up in the store's directory.

    Returns
    -------
    NotifyConfig
        The merged global config.

    Raises
    ------
    NotifyConfigError
        If the sidecar exists but is malformed (bad JSON, non-mapping top
        level, unknown event type, or invalid role).
    """
    merged: dict[str, list[str]] = {
        et: list(roles) for et, roles in DEFAULT_NOTIFY_RULES.items()
    }
    sidecar = notify_sidecar_path(store)
    if sidecar is not None:
        if sidecar.exists():
            for event_type, roles in _read_sidecar(sidecar).items():
                merged[event_type] = roles
    return NotifyConfig(rules=merged)


__all__ = [
    "coerce_id_list",
    "coerce_rules_mapping",
    "load_notify_config",
    "notify_sidecar_path",
    "validate_event_type",
    "validate_roles",
]

# EOF
