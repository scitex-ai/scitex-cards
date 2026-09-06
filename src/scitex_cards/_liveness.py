#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-cards's OWN liveness signal at the store boundary.

Two thin wrappers over :mod:`scitex_cards._users` that the mutating store
verbs (create / comment / update / reassign / inbox-poll) call:

    _heartbeat            Stamp ``last_seen = now(UTC)`` on the ACTING agent's
                          registry record (fail-soft — never breaks the write).
    _assignee_liveness    Classify a card's ASSIGNEE as alive / stale / unknown
                          so an assign can tell the caller "you just assigned
                          to a non-running agent."

STANDALONE constraint (SoC mandate): liveness is computed PURELY from
scitex-cards's own registry record's ``last_seen`` — stamped locally, read
locally. There is NO ``sac`` / ``scitex_agent_container`` import and NO
network probe to any external runtime here or anywhere it reaches.

These live OUTSIDE ``_store.py`` (already grandfathered over the line cap)
so the SSOT store only gains thin call lines, not net-new logic.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path


def _heartbeat(actor: str | None, store: str | Path | None) -> None:
    """Stamp ``last_seen`` on the acting agent's registry record (fail-soft).

    Whenever an agent touches the store we stamp ``last_seen = now(UTC)`` on
    its OWN scitex-cards registry record via the SAME identity seam the rest
    of the store uses (:func:`scitex_cards._users.touch_user`) — no second
    identity path. This is the signal :func:`scitex_cards._users.is_alive`
    reads to surface a non-running assignee.

    WRAPPED fail-soft: the heartbeat is SECONDARY to the primary write, which
    already succeeded. A stamping failure (unregistered actor → ``touch_user``
    returns ``None``; lock contention; disk error) is LOGGED, never raised —
    it must never break or roll back the user's action. An unregistered actor
    is not an error here: the actor itself was already fail-loud resolved by
    the primary write; it simply has no registry record to stamp yet.
    """
    if not actor:
        return
    try:
        from ._users import touch_user

        touch_user(actor, store=store)
    except Exception:  # noqa: BLE001 — heartbeat must never break the write
        import logging

        logging.getLogger(__name__).warning(
            "heartbeat: failed to stamp last_seen for actor %r",
            actor,
            exc_info=True,
        )


def _assignee_liveness(
    assignee: str | None, store: str | Path | None
) -> dict | None:
    """Liveness payload for a card's assignee, or ``None`` when unassigned.

    Resolves ``assignee`` through the registry identity seam
    (:func:`scitex_cards._users.resolve_user`) and classifies it with the pure
    :func:`scitex_cards._users.is_alive` helper. Returns
    ``{"status", "last_seen", "age_seconds"}`` — the signal a caller reads
    right after an assign to learn the assignee is (not) running. An
    UNREGISTERED assignee resolves to ``None`` → ``"unknown"`` status.

    Fail-soft: any error yields ``"unknown"`` rather than breaking the
    caller's already-durable write.
    """
    if not (isinstance(assignee, str) and assignee.strip()):
        return None
    try:
        from ._users import is_alive, resolve_user

        user = resolve_user(assignee.strip(), store=store)
        return is_alive(user, now=_dt.datetime.now(_dt.timezone.utc))
    except Exception:  # noqa: BLE001 — liveness read must not break the write
        import logging

        logging.getLogger(__name__).warning(
            "assignee_liveness: failed to classify %r", assignee, exc_info=True
        )
        return {"status": "unknown", "last_seen": None, "age_seconds": None}


__all__ = ["_assignee_liveness", "_heartbeat"]

# EOF
