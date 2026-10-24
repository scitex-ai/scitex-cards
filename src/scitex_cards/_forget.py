#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Forgetting sweep — configurable auto-close of stale cards.

Operator doctrine 2026-10-22: forgetting is a feature. Triaging stale cards
one by one costs more than recreating them on demand, so AGE ALONE is a
valid reason to close a blocked/deferred/goal card with no recent activity.
Nothing is deleted — the close reason is stamped in ``comments[]`` via the
``close`` verb, and a fresh card is recreated on demand.

Design (mirrors :mod:`scitex_cards._reconcile_prs` and :mod:`scitex_cards._config`)
------------------------------------------------------------------------------------
* **Pure decision core.** :func:`decide_forget_action` is a pure function
  over ``(task, config)`` → ``"close"`` / ``"keep"``. No I/O. This is what
  the tests assert against directly (no store, no mocks).
* **Configurable horizon, OFF by null.** The forgetting horizon is
  ``forgetting.days`` in the layered ``config.json``
  (:mod:`scitex_cards._config`), overridden by the ``--days`` CLI flag and
  the ``SCITEX_CARDS_FORGET_DAYS`` env var. ``null`` / ``"none"`` / empty at
  ANY layer disables the sweep — a forgotten sweep is a sweep that never
  fires, and the off switch must be one word, not a code change.
* **Never touches live work.** Only ``blocked`` / ``deferred`` / ``goal``
  cards are candidates. ``in_progress`` is NEVER closed by age — a card
  somebody is working can be a year stale and still be live. Terminal states
  (``done`` / ``failed`` / ``cancelled``) are already closed.
* **Unknown age is not stale.** A card with no ``created_at`` and no
  ``last_activity`` is skipped. Guessing an age for an undated card is how
  a sweep closes something it never measured.
* **Strictly older wins.** A card exactly on the horizon is kept — the
  fencepost belongs to the card, not the sweep.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass

from ._config import load_config

#: The ``forgetting:`` knobs — the auto-close horizon for stale cards.
FORGET_SECTION = "forgetting"

__all__ = [
    "FORGET_SECTION",
    "ENV_FORGET_DAYS",
    "DEFAULT_FORGET_DAYS",
    "FORGETTABLE_STATUSES",
    "ForgetConfig",
    "load_forget_config",
    "decide_forget_action",
    "select_forget_candidates",
    "forget_reason",
]

#: Env override for the forgetting horizon. ``"none"`` (or empty) disables.
ENV_FORGET_DAYS = "SCITEX_CARDS_FORGET_DAYS"

#: Default horizon (days) when no layer overrides it. Operator-confirmed
#: 2026-10-22: three days of silence is forgotten, not pending.
DEFAULT_FORGET_DAYS = 3

#: Statuses the sweep may close. ``in_progress`` is deliberately absent —
#: live work is never aged out. Terminal states need no closing.
FORGETTABLE_STATUSES = frozenset({"blocked", "deferred", "goal"})

#: Values that mean OFF at any layer (config ``null`` arrives as None).
_OFF_SPELLINGS = frozenset({"none", "off", "false", "disabled", ""})


@dataclass(frozen=True)
class ForgetConfig:
    """The resolved forgetting horizon.

    ``days`` is the age (in days) past which a stale card is forgotten, or
    ``None`` when the sweep is OFF. ``enabled`` is the one predicate every
    caller checks — a disabled sweep selects nothing.
    """

    days: int | float | None = DEFAULT_FORGET_DAYS

    @property
    def enabled(self) -> bool:
        """Whether the sweep may close anything."""
        return self.days is not None


def _coerce_days(value: object) -> int | float | None:
    """Coerce a horizon value; ``None`` (OFF) on off-spellings and junk.

    ``None`` (config ``null``), ``"none"``/``"off"``/``""`` and any
    non-positive / non-numeric value all mean OFF — a misconfigured horizon
    must disarm the sweep, never widen it to close everything.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if value > 0 else None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _OFF_SPELLINGS:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        return number if number > 0 else None
    return None


def load_forget_config(
    explicit_days: int | float | str | None = "unset",
    cfg: dict | None = None,
) -> ForgetConfig:
    """Resolve the forgetting horizon: explicit flag > env > config > default.

    ``explicit_days`` is the ``--days`` CLI flag (``None`` = OFF by word,
    ``"unset"`` = flag absent). The env var beats the file so an operator
    can disarm one host without editing shared config. Read at CALL time
    (not import) so the operator can edit the knob and have the next tick
    pick it up — no restart, same as :mod:`scitex_cards._config`.
    """
    if explicit_days != "unset":
        return ForgetConfig(days=_coerce_days(explicit_days))
    raw_env = os.environ.get(ENV_FORGET_DAYS)
    if raw_env is not None:
        return ForgetConfig(days=_coerce_days(raw_env))
    data = load_config() if cfg is None else cfg
    section = data.get(FORGET_SECTION)
    if isinstance(section, dict):
        return ForgetConfig(days=_coerce_days(section.get("days")))
    if section is None:
        return ForgetConfig(days=DEFAULT_FORGET_DAYS)
    return ForgetConfig(days=_coerce_days(section))


def _parse_iso(value: object) -> datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _last_activity(task: dict) -> datetime.datetime | None:
    """The freshest timestamp the card carries, or ``None`` when undated."""
    stamps = [
        _parse_iso(task.get("last_activity")),
        _parse_iso(task.get("created_at")),
    ]
    dated = [s for s in stamps if s is not None]
    return max(dated) if dated else None


def decide_forget_action(task: dict, config: ForgetConfig) -> str:
    """``"close"`` if ``task`` is forgotten under ``config``, else ``"keep"``.

    Pure: no I/O, no clock reads of its own — ``now`` is taken once per call
    so a batch pass can pin it. A disabled config keeps everything.

    Age is compared in WHOLE DAYS (floor, like ``_stale._age_days``): a card
    closes only when its age exceeds the horizon. The fencepost belongs to
    the card — an exactly-on-the-horizon card is kept, and the comparison
    never turns on a microsecond.
    """
    if not config.enabled:
        return "keep"
    if task.get("status") not in FORGETTABLE_STATUSES:
        return "keep"
    seen = _last_activity(task)
    if seen is None or config.days is None:
        return "keep"
    now = datetime.datetime.now(datetime.timezone.utc)
    age_days = (now - seen).days
    return "close" if age_days > config.days else "keep"


def select_forget_candidates(
    tasks: list[dict], config: ForgetConfig
) -> list[dict]:
    """The stale cards :func:`decide_forget_action` would close, in order."""
    return [t for t in tasks if decide_forget_action(t, config) == "close"]


def forget_reason(days: int | float) -> str:
    """The audit comment stamped on every forgotten card."""
    return (
        f"stale: no activity in >{days}d; closed by the forgetting sweep "
        "(age alone as reason — recreate on demand if needed)"
    )


# EOF
