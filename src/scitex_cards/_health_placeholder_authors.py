#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Do any cards record an unexpanded ``${VAR}`` as their author?

THE ARGUMENT FOR A CHECK RATHER THAN A REPAIR, made by sac on 2026-08-18 and
adopted because it is better than what it replaced:

    "The repair was real and a restore undid it. That means the useful
     artifact is not the repair, it is the DETECTOR. Otherwise the next
     restore silently reintroduces them and the next person to notice will be
     a year from now. I would rather see 15 rows on a dashboard forever than
     see 0 rows because nobody is counting."

That is exactly what happened. The 2026-07-19 incident wrote the literal text
``${SCITEX_CARDS_AGENT_ID}`` into ``created_by`` when a launcher exported only
the pre-rename env name. It was repaired on 2026-07-21 and the card closed on
"0 rows carry the literal env var (was 7)". Re-measured 2026-08-18:

    tasks total                                  5198
    created_by holding a literal '$'               15
    written AFTER the 2026-07-21 repair claim       0

Every one of the 15 predates the repair. Nothing recurred; a RESTORE brought
the rows back and no instrument was counting, so a closed card sat on a false
claim for a month.

THE ROWS ARE DELIBERATELY NOT REPAIRED. Replacing a non-answer with a
plausible guess is the worse failure: afterwards it is indistinguishable from
a real answer. The ``agent`` column names a plausible author for each row, but
that is inference, and writing an inference into a field readers treat as fact
is how a store stops being trustworthy. A visibly broken value tells the truth
about itself. So this check REPORTS them and never rewrites them.

ADVISORY, not delivery or blocking. Nothing is failing right now: the writes
work, the reads work, and no known consumer branches on ``created_by`` (sac
confirmed none of theirs does). What is damaged is attribution — the board
cannot say who wrote those cards. Worth surfacing permanently, not worth
waking anyone.

The WRITE-SIDE fix is separate and already landed: ``_store_identity`` now
refuses a leading ``$`` at the creator door, so no NEW row can be written this
way. This check covers what that guard cannot — rows that arrive by restore,
import, or any path that does not pass through the resolver.
"""

from __future__ import annotations

#: Both spellings the incident produced. The brace form is what .mcp.json
#: leaves behind when the referenced variable does not exist; the bare form is
#: what Claude Code never expands in the first place.
_HINT = (
    "These rows predate the creator-door guard and are NOT repaired "
    "automatically: the `agent` column names a plausible author, but writing "
    "an inference into an authorship field is worse than leaving a visibly "
    "broken one. Inspect with: SELECT id, created_by, agent FROM tasks WHERE "
    "created_by LIKE '%$%'. If this count RISES, a writer is bypassing "
    "_store_identity._resolve_creator_or_raise — that is the real defect."
)


def check_no_placeholder_authors(store=None) -> dict:
    """Are any card authors an unexpanded shell placeholder?

    Returns the standard ``{ok, detail, hint}``. ``ok`` is three-valued:
    True (none), False (some exist), None (could not tell).
    """
    try:
        from ._backend_connect import connect
        from ._store_target import resolve_store_target

        conn = connect(resolve_store_target(store))
    except Exception as exc:  # noqa: BLE001 — a health check must not raise
        return {
            "ok": None,
            "detail": f"cannot reach the store to count placeholder authors: {exc}",
            "hint": "Resolve the store first; this check depends on it.",
        }

    try:
        row = conn.fetchone(
            "SELECT count(*) FROM tasks WHERE created_by LIKE '%$%'"
        )
        count = int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": None,
            "detail": f"could not count placeholder authors: {exc}",
            "hint": (
                "UNKNOWN is not OK: a count that cannot be taken may be "
                "hiding rows whose authorship is unrecoverable."
            ),
        }

    if count == 0:
        return {
            "ok": True,
            "detail": "no card records an unexpanded placeholder as its author",
            "hint": None,
        }

    return {
        "ok": False,
        "detail": (
            f"{count} card(s) record an unexpanded shell placeholder as their "
            "author (created_by), so the board cannot say who wrote them"
        ),
        "hint": _HINT,
    }


__all__ = ["check_no_placeholder_authors"]

# EOF
