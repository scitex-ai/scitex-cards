#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Owner-liveness + creator-resolution helpers for the reminder engine.

Slice 3 of the delivery rail: when a stale card's OWNER (assignee) is not
alive, the assignee will never act, so the card escalates to its CREATOR.
These helpers wrap :func:`scitex_cards._users.is_alive` /
:func:`scitex_cards._users.resolve_user` and stay fail-soft — a registry
lookup error must NEVER break a sweep (an unresolvable owner is a dead owner
for escalation purposes). Extracted from :mod:`scitex_cards._reminders` (the
sweep state machine) so that module stays under the file-size budget.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _owner_liveness(
    resolve_user: Callable[[str], Any],
    owner: str,
    *,
    now: _dt.datetime,
    ttl_seconds: int,
) -> "tuple[Any, dict]":
    """Resolve the owner and classify liveness off the registry, fail-soft.

    Returns ``(user, liveness)`` where ``user`` is the resolved record (or
    ``None`` when the owner is NOT a registered user — a free-form owner
    string, the pre-registry back-compat case) and ``liveness`` is the
    :func:`scitex_cards._users.is_alive` dict. A resolution error must NEVER
    break the sweep → it degrades to ``(None, {"status": "unknown", ...})``.

    The caller escalates only when ``user is not None`` and the liveness
    status is not ``"alive"``: liveness is a property of a REGISTERED member,
    so a non-user owner has no signal to act on (and must not be nagged).
    """
    from scitex_cards._users import is_alive

    try:
        user = resolve_user(owner)
    except Exception as exc:  # noqa: BLE001 — a lookup failure must not break the sweep
        logger.warning("reminders: user resolution for %r failed: %s", owner, exc)
        user = None
    return user, is_alive(user, now=now, ttl_seconds=ttl_seconds)


def _card_creator(card: dict, owner: str, operator: str) -> str:
    """The recipient for a liveness escalation: the card's CREATOR.

    Uses the card's ``created_by`` (the strictly-resolved creating user stamped
    by ``add_task``). Falls back to the operator when the creator is absent OR
    is the (dead) owner itself — escalating to a dead self is pointless.
    """
    creator = card.get("created_by")
    if not (isinstance(creator, str) and creator) or creator == owner:
        return operator
    return creator


__all__ = [
    "_card_creator",
    "_owner_liveness",
]

# EOF
