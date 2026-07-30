#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Layered ``config.json`` for scitex-cards knobs (nudge cadence, …).

The board is fleet infra, so its behaviour must be a KNOB the operator can
turn — not a constant baked into the code. Config lives in the same SciTeX
local-state convention as the task store (:mod:`scitex_cards._paths`), in a
``config.json`` resolved across two scopes and LAYERED:

    1. user scope:     ``$SCITEX_DIR/cards/config.json`` (default ``~/.scitex/cards``)
    2. project scope:  ``<git-root>/.scitex/cards/config.json``

The user file is the BASE; the project file OVERRIDES it key-by-key (a repo
can tighten/loosen a knob without touching the user default). A missing /
malformed file contributes nothing (fail-soft: a bad config must never break
a sweep). The merged mapping is returned; callers pick the section they need.

Today the only section is ``reminders:`` (the nag/digest cadence). The
resolved nudge interval has THREE layers, tightest-wins for the per-owner
digest:

    card-level override  >  config ``reminders.interval_minutes``  >  default

A per-card ``reminder_interval_minutes`` lets one urgent card pull its
owner's digest onto a tighter clock without changing anyone else's.

Read at CALL time (not import) so the operator can edit the knob and have the
running daemon pick it up on its next tick — no restart.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ._paths import PKG_SHORT, _find_git_root, _user_root

logger = logging.getLogger(__name__)

#: Config file name (in each scope's ``.scitex/cards`` dir).
CONFIG_NAME = "config.json"

#: The ``reminders:`` knobs.
REMINDERS_SECTION = "reminders"

#: Default flat re-nudge interval (minutes) when nothing overrides it.
DEFAULT_INTERVAL_MINUTES = 5.0

#: Per-card field that overrides the nudge interval for that card (minutes).
CARD_INTERVAL_FIELD = "reminder_interval_minutes"


def config_paths() -> list[Path]:
    """The config files to layer, BASE first (user), OVERRIDE last (project).

    The project file (when inside a git repo) is listed after the user file
    so a later merge lets project keys win. A scope with no repo simply omits
    the project entry.
    """
    paths = [_user_root() / CONFIG_NAME]
    git_root = _find_git_root(Path.cwd())
    if git_root is not None:
        paths.append(git_root / ".scitex" / PKG_SHORT / CONFIG_NAME)
    return paths


def _read_one(path: Path) -> dict:
    """Read one JSON config file → mapping; missing/malformed → ``{}`` (fail-soft)."""
    import json

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:  # noqa: BLE001 — unreadable config must not break a sweep
        logger.warning("config: cannot read %s: %s", path, exc)
        return {}
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        logger.warning("config: malformed %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def load_config() -> dict:
    """Layer the config files (user base, project override) into one mapping.

    One-level-deep merge per top-level section: a project ``reminders:`` block
    overrides the user ``reminders:`` block KEY-BY-KEY (not whole-section
    replace), so a repo can override a single knob and inherit the rest.
    """
    merged: dict[str, Any] = {}
    for path in config_paths():
        data = _read_one(path)
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
    return merged


def reminders_config() -> dict:
    """The merged ``reminders:`` section (``{}`` when absent)."""
    section = load_config().get(REMINDERS_SECTION)
    return section if isinstance(section, dict) else {}


def _positive_number(value: Any) -> float | None:
    """Coerce a config/card value to a positive float, else ``None``."""
    if isinstance(value, bool):  # bool is an int subclass — reject it explicitly
        return None
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def resolve_interval_minutes(card: dict | None, cfg: dict | None = None) -> float:
    """Resolve the nudge interval (minutes) for ``card``, tightest layer wins.

    Order: per-card ``reminder_interval_minutes`` > config
    ``reminders.interval_minutes`` > :data:`DEFAULT_INTERVAL_MINUTES`. A
    non-positive / non-numeric value at any layer is ignored (falls through).
    """
    if card is not None:
        override = _positive_number(card.get(CARD_INTERVAL_FIELD))
        if override is not None:
            return override
    cfg = reminders_config() if cfg is None else cfg
    configured = _positive_number(cfg.get("interval_minutes"))
    if configured is not None:
        return configured
    return DEFAULT_INTERVAL_MINUTES


__all__ = [
    "CONFIG_NAME",
    "REMINDERS_SECTION",
    "DEFAULT_INTERVAL_MINUTES",
    "CARD_INTERVAL_FIELD",
    "config_paths",
    "load_config",
    "reminders_config",
    "resolve_interval_minutes",
]

# EOF
