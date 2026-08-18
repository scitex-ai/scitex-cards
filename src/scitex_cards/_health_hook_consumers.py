#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Is a card-event consumer registered under the DEAD entry-point group?

WHY A CHECK AND NOT JUST A LOG LINE. The dead-group report in
``_hooks._plugins`` is already at ERROR level, which felt like enough. sac
named why it is not, 2026-08-18:

    "An ERROR with no reader is the same silence in a louder font. I have
     watched four periodic jobs on this fleet fail every single tick for weeks
     while every health check stayed green, because the failure had no
     consumer."

They are right, and the history backs them. On 2026-08-17 the fleet's only
registered card-event consumer sat under ``scitex_todo.hooks`` while dispatch
read ``scitex_cards.hooks``. Nothing raised, nothing logged, every check was
green, and the push rail was simply silent until someone went looking. The
2026-08-18 hard cut (removing the alias on the operator's ruling) re-creates
exactly that state for any producer that has not migrated — so the condition
needs an instrument, not just a message in a stream nobody tails.

WHY IT IS A DELIVERY FAULT. The store is fine and the cards are correct; what
fails is that events never reach the consumer that was supposed to act on
them. That is the same class as an undelivered notification.

THREE-VALUED, like every check here. "No stragglers" and "I could not look"
are different facts, and collapsing the second into the first is how the
original defect stayed invisible.

*** THIS CHECK IS PER-HOST, AND SAYS SO IN ITS OWN DETAIL LINE. *** It reads
INSTALLED METADATA, which exists per machine, so it answers "is THIS host's
producer migrated" and NOT "has the fleet migrated". sac named the hazard
while using it as their deployment instrument, 2026-08-18:

    "If you run it on compute-04 and it goes green, compute-02, compute-03 and
     the laptop can still be red and you will not see it from there. A green
     check is exactly the kind of result nobody re-measures."

So every verdict carries the hostname it was measured on. A reader who finds
"ok" still has to ask "on which machine", and the answer is in the sentence
rather than in someone's memory of how the check works. Contrast
`_health_placeholder_authors`, which queries the SHARED store and therefore
does speak for the fleet — the two look alike and their scopes are not.
"""

from __future__ import annotations

import socket

#: Reported when a straggler exists, so the operator gets the edit rather than
#: a description of the problem.
_HINT = (
    "Each straggler must move in its OWN pyproject — cards cannot fix this "
    'from here. Add [project.entry-points."scitex_cards.hooks"] with the same '
    "target, drop the scitex_todo.hooks entry, and reinstall. Until then that "
    "producer's card events are not delivered."
)


def _host() -> str:
    """This machine's name, for the per-host scoping note in every verdict."""
    try:
        return socket.gethostname()
    except Exception:  # noqa: BLE001 — a health check must not raise
        return "unknown-host"


def check_hook_consumers_registered() -> dict:
    """Are any card-event hooks stranded in the retired entry-point group?

    Returns the standard ``{ok, detail, hint}``. ``ok`` is three-valued:
    True (none stranded), False (a straggler), None (could not tell).

    EVERY verdict names the host it was measured on: this reads installed
    metadata, so it speaks for THIS machine and not for the fleet.
    """
    try:
        import importlib.metadata

        from ._hooks._plugins import (
            DEAD_ENTRY_POINT_GROUP,
            ENTRY_POINT_GROUP,
            _select_group,
        )
    except Exception as exc:  # noqa: BLE001 — a health check must not raise
        return {
            "ok": None,
            "detail": f"cannot inspect hook entry points: {exc}",
            "hint": "This build may predate the dead-group split; verify by hand.",
        }

    try:
        eps = importlib.metadata.entry_points()
        dead = _select_group(eps, DEAD_ENTRY_POINT_GROUP)
        live = _select_group(eps, ENTRY_POINT_GROUP)
    except Exception as exc:  # noqa: BLE001 — packaging surprises
        return {
            "ok": None,
            "detail": f"entry-point discovery failed: {exc}",
            "hint": "UNKNOWN is not OK: a consumer may be stranded. Re-run.",
        }

    host = _host()
    if not dead:
        return {
            "ok": True,
            # The host is in the PASSING line too, deliberately. A green verdict
            # is the one nobody re-measures, so it is the one that most needs to
            # say how far it reaches.
            "detail": (
                f"on {host}: no hooks in the dead group "
                f"{DEAD_ENTRY_POINT_GROUP!r}; {len(live)} registered under "
                f"{ENTRY_POINT_GROUP!r}. This host only — other machines are "
                f"not covered by this result."
            ),
            "hint": None,
        }

    names = ", ".join(sorted(ep.name for ep in dead))
    return {
        "ok": False,
        "detail": (
            f"on {host}: {len(dead)} card-event consumer(s) registered under "
            f"the DEAD entry-point group {DEAD_ENTRY_POINT_GROUP!r} and NOT "
            f"called: {names}. Their card events are not being delivered. "
            f"This host only — run the check on each machine."
        ),
        "hint": _HINT,
    }


__all__ = ["check_hook_consumers_registered"]

# EOF
