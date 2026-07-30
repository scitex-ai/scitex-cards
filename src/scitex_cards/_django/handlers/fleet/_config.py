#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard configuration loader.

The fleet dashboard reads its watched-repo list from
``~/.scitex/cards/dashboard.json`` under the key path
``fleet.ci_status.repos`` (a list of ``owner/name`` GitHub slugs).
The env var ``SCITEX_TODO_FLEET_CI_REPOS=slug1,slug2`` is the override hook
(handy for CI tests and for the operator to flip the set without editing
a file).

Architectural principles in play:

- NO hardcoded proper nouns. There is no
  ``["scitex-todo","scitex-dev",…]`` literal in this module — the
  config is the only source of truth.
- Absence is NOT an error. A fresh install has no dashboard config; that
  means "no repos configured" and the UI hides the pills strip gracefully.
- A malformed config IS an error — fail-loud per the harness contract so
  the operator does not stare at a blank strip wondering why their
  config is being ignored.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ._errors import FleetAdapterError

# Canonical config path. Kept here as a module-level constant so tests
# can monkeypatch ``HOME`` and the function picks it up via
# ``Path.home()`` without having to redefine the literal.
_CONFIG_REL = Path(".scitex") / "cards" / "dashboard.json"

# Env override — comma-separated slugs. Whitespace around commas is
# tolerated (operator may copy-paste from a shell history).
_ENV_REPOS = "SCITEX_TODO_FLEET_CI_REPOS"


def _config_path() -> Path:
    """Resolve the canonical (JSON) config path. Pure function — testable."""
    return Path.home() / _CONFIG_REL


def _split_env(raw: str) -> list[str]:
    """Parse the env-override string into a clean slug list.

    Empty entries (trailing/leading commas, double commas) are dropped
    so the operator can be sloppy.
    """
    return [s.strip() for s in raw.split(",") if s.strip()]


def _load_config(path: Path) -> dict:
    """Parse the JSON dashboard config at ``path``; raise
    :class:`FleetAdapterError` on a broken file (caller checked it exists)."""
    import json

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FleetAdapterError(
            f"failed to read dashboard config {path}: {exc}"
        ) from exc

    try:
        data = json.loads(text) if text.strip() else None
    except json.JSONDecodeError as exc:
        raise FleetAdapterError(f"malformed dashboard config {path}: {exc}") from exc

    if data is None:
        # Empty file is the same as no file — "no repos configured".
        return {}
    if not isinstance(data, dict):
        raise FleetAdapterError(
            f"dashboard config {path} must be a mapping at the top level, "
            f"got {type(data).__name__}"
        )
    return data


# Ecosystem spin-out flag (operator opt-in). When truthy in the dashboard config
# (``fleet.ci_status.ecosystem: true``) or via this env var, the watched-repo
# list is UNIONed with the live SciTeX ecosystem registry.
_ENV_ECOSYSTEM = "SCITEX_TODO_FLEET_CI_ECOSYSTEM"

# The ecosystem roster changes rarely but ``fleet_config_load`` runs on every
# 30s poll — so resolve the registry at most once per TTL.
_ECO_TTL_SEC = 3600.0
_eco_cache: dict[str, Any] = {"ts": 0.0, "repos": []}


def _truthy(val: Any) -> bool:
    """Loose truthiness for a config / env flag (``true`` / ``1`` / ``yes`` / ``on``)."""
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _ecosystem_repos() -> list[str]:
    """``owner/name`` slugs for every SciTeX ecosystem package.

    Sourced from ``scitex-dev ecosystem list --json`` — the canonical
    registry — so the watch-list "spins out" across the whole ecosystem with
    NO hardcoded roster here (this module's "no proper nouns" contract).
    Loose-coupled BY DESIGN: a SUBPROCESS, not an import, so a missing / old
    scitex-dev degrades to an empty list (the operator's explicit repos still
    apply) instead of crashing the dashboard. Cached for ``_ECO_TTL_SEC``.
    """
    import time

    now = time.time()
    cached = _eco_cache.get("repos") or []
    if cached and now - float(_eco_cache.get("ts") or 0.0) < _ECO_TTL_SEC:
        return list(cached)

    repos: list[str] = []
    try:
        import json
        import shutil
        import subprocess

        exe = shutil.which("scitex-dev")
        if exe:
            proc = subprocess.run(
                [exe, "ecosystem", "list", "--json"],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode == 0 and (proc.stdout or "").strip():
                parsed: Any = json.loads(proc.stdout)
                packages = parsed.get("packages") if isinstance(parsed, dict) else None
                if isinstance(packages, list):
                    for pkg in packages:
                        if not isinstance(pkg, dict):
                            continue
                        slug = pkg.get("github_repo")
                        if isinstance(slug, str) and "/" in slug:
                            repos.append(slug)
    except Exception:  # noqa: BLE001 — discovery is best-effort, never fatal
        repos = []

    if repos:
        _eco_cache["ts"] = now
        _eco_cache["repos"] = list(repos)
    return repos


def fleet_config_load() -> dict[str, Any]:
    """Return the dashboard config as a nested ``dict``.

    Resolution order (later overrides earlier):

    1. ``~/.scitex/cards/dashboard.json`` if present (otherwise empty
       config, NOT an error)
    2. ``SCITEX_TODO_FLEET_CI_REPOS`` env var — when set, replaces
       ``fleet.ci_status.repos`` regardless of file contents
    3. ``fleet.ci_status.ecosystem: true`` (or env
       ``SCITEX_TODO_FLEET_CI_ECOSYSTEM``) — UNION the result with every
       SciTeX ecosystem repo from ``scitex-dev ecosystem list``, so the
       pills "spin out" across the whole ecosystem.

    The returned shape is always normalized to::

        {"fleet": {"ci_status": {"repos": [<slug>, ...]}}}

    so the view code can read it without defensive ``.get()`` chains.
    """
    path = _config_path()
    if path.is_file():
        data = _load_config(path)
    else:
        data = {}

    # Normalize the nested shape (assign-then-guard so each level narrows
    # cleanly — no re-evaluated `.get` chains).
    fleet_raw = data.get("fleet")
    fleet = fleet_raw if isinstance(fleet_raw, dict) else {}
    ci_raw = fleet.get("ci_status")
    ci = ci_raw if isinstance(ci_raw, dict) else {}
    repos_raw = ci.get("repos")
    repos_list = repos_raw if isinstance(repos_raw, list) else []
    repos = [str(r) for r in repos_list if isinstance(r, str) and r.strip()]

    env_override = os.environ.get(_ENV_REPOS)
    if env_override is not None:
        repos = _split_env(env_override)

    # Ecosystem spin-out (operator opt-in): union the explicit list with the
    # live SciTeX ecosystem registry when the flag is truthy. Order-stable +
    # de-duped (explicit repos lead, so a pinned repo keeps its position).
    if _truthy(ci.get("ecosystem")) or _truthy(os.environ.get(_ENV_ECOSYSTEM)):
        seen = set(repos)
        for slug in _ecosystem_repos():
            if slug not in seen:
                seen.add(slug)
                repos.append(slug)

    return {"fleet": {"ci_status": {"repos": repos}}}


__all__ = ["fleet_config_load"]

# EOF
