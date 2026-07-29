#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop-hook idle guard — refuse to let an agent abandon claimed work.

INCIDENT (operator, 2026-06-30, SERIOUS): "agents stop even with their card not
completed yet." The board is the fleet's direction system; an agent that marks a
card ``in_progress`` and then silently stops without finishing, reassigning, or
explicitly blocking it is the abandonment this guard prevents — DETERMINISTICALLY
(constitution: deterministic systems over agentic; hooks over prompts).

Mechanism
---------
A Claude Code **Stop hook** runs ``python -m scitex_cards._idle_guard``. If the
agent owns a STALE ``in_progress`` card (claimed but untouched past the
stale-active threshold), the hook BLOCKS the stop (exit code 2) and tells the
agent EXACTLY how to clear it: finish it, reassign it, or set it ``blocked`` with
a blocker + a comment. Those are the only honest dispositions — none of them is
"silently stop". When the agent acts, the card leaves the stale set and the guard
allows the stop, so the loop is bounded by genuine disposition, not by force.

Why STALE ``in_progress`` specifically (not all open cards)
-----------------------------------------------------------
* PENDING backlog is surfaced by the recurring digest nudge, not the stop guard —
  blocking stop on a whole backlog would trap an agent that legitimately has many
  queued cards.
* A freshly-TOUCHED ``in_progress`` card means the agent is actively working and
  is not the abandonment case (it will not be stopping mid-keystroke).
* A ``blocked`` card is a legitimate park (waiting on operator/dependency).

So the abandonment signal is precisely: **claimed (``in_progress``) + not
progressing (stale) + trying to stop.** That reuses the SAME detector the nag
engine + freshness campaign use (:func:`scitex_cards._stale_active.detect_stale_active`).

Wiring (the fleet-wide enforcement)
-----------------------------------
Add a Stop hook to each agent's Claude settings so the guard runs whenever the
agent tries to go idle (``SCITEX_TODO_AGENT_ID`` + ``SCITEX_TODO_TASKS_YAML_SHARED`` are already
in the agent's env)::

    "hooks": {
      "Stop": [
        {"hooks": [{"type": "command",
                    "command": "/uvwork/venv-agent/bin/python -m scitex_cards._idle_guard"}]}
      ]
    }

Exit 2 blocks the stop and feeds the reason back to the agent; exit 0 lets it
stop. Pair it with the recurring digest nudge (notifyd, owner-allowlist) — the
digest RE-ENGAGES an idle agent with its assigned-card list, the Stop hook
PREVENTS a silent stop with claimed work. Together they are the deterministic
"never idle while tasks remain" enforcement (constitution §4).

Fail-soft
---------
A bug in the guard must NEVER permanently trap an agent: any unexpected error is
logged to stderr and the guard ALLOWS the stop (exit 0). It fails loud but open.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

#: Env var naming the current agent (the owner whose claimed work we guard).
ENV_AGENT = "SCITEX_TODO_AGENT_ID"

#: Cap on cards listed in the block reason (keep the message bounded).
_REASON_CARD_CAP = 15


def stale_in_progress(
    agent: str,
    tasks: list[dict],
    *,
    now: _dt.datetime | None = None,
    stale_hours: float | None = None,
) -> list:
    """The agent's ``in_progress`` cards that are STALE (claimed but untouched).

    Thin filter over :func:`scitex_cards._stale_active.detect_stale_active` (the
    SSOT staleness detector): take the agent's stale-active bucket and keep only
    ``status == "in_progress"`` (drop ``blocked`` — a legitimate park). Returns a
    list of ``StaleCard`` (id / title / status / age_hours), oldest-first.
    """
    from ._stale_active import detect_stale_active

    if not agent:
        return []
    buckets = detect_stale_active(tasks, now=now, stale_hours=stale_hours)
    return [c for c in buckets.get(agent, []) if c.status == "in_progress"]


def _reason(agent: str, cards: list) -> str:
    """Compose the Stop-hook block message naming the abandoned cards + the escape."""
    shown = cards[:_REASON_CARD_CAP]
    lines = []
    for c in shown:
        age = "" if c.age_hours is None else f", untouched ~{c.age_hours:.0f}h"
        title = (c.title or "").strip() or "(untitled)"
        lines.append(f"  - {c.id} [in_progress{age}] \"{title}\"")
    if len(cards) > _REASON_CARD_CAP:
        lines.append(f"  - (+{len(cards) - _REASON_CARD_CAP} more)")
    body = "\n".join(lines)
    return (
        f"DO NOT STOP — you ({agent}) still own {len(cards)} in-progress card(s) "
        f"you marked as being worked but have not finished:\n{body}\n\n"
        "Silently stopping with claimed work is the abandonment incident the board "
        "exists to prevent. For EACH card, pick one honest disposition now:\n"
        "  1. Finish it  → scitex-cards close <id>  (or complete it).\n"
        "  2. Hand it off → scitex-cards reassign <id> --to <owner>.\n"
        "  3. Genuinely can't proceed → set it blocked with a reason: "
        "scitex-cards update <id> --status blocked --blocker <operator-decision|dependency> "
        "and comment WHY.\n"
        "  4. Not actually working it → scitex-cards update <id> --status deferred.\n"
        "Once none of your in-progress cards are stale, you may stop."
    )


def evaluate(
    agent: str,
    *,
    store: str | Path | None = None,
    now: _dt.datetime | None = None,
    stale_hours: float | None = None,
) -> tuple[bool, str]:
    """Return ``(block, reason)``: block the stop iff the agent abandons claimed work."""
    from ._model import load_tasks
    from ._paths import resolve_tasks_path

    # Resolve BEFORE load: load_tasks(None) trips on Path(None); the precedence
    # chain ($SCITEX_TODO_TASKS_YAML_SHARED → project → user) yields a concrete path.
    tasks = load_tasks(resolve_tasks_path(store))
    cards = stale_in_progress(agent, tasks, now=now, stale_hours=stale_hours)
    if not cards:
        return (False, "")
    return (True, _reason(agent, cards))


def main(argv: list[str] | None = None) -> int:
    """Stop-hook entry point. Exit 2 (block) when claimed work is abandoned, else 0.

    Resolves the agent from ``--agent`` or :data:`ENV_AGENT`; reads the store from
    the standard precedence (``$SCITEX_TODO_TASKS_YAML_SHARED`` …). Reads — but does not
    require — the Stop-hook JSON on stdin. Fail-soft: any error allows the stop.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    agent = ""
    if "--agent" in argv:
        i = argv.index("--agent")
        if i + 1 < len(argv):
            agent = argv[i + 1]
    agent = agent or os.environ.get(ENV_AGENT, "")

    # Drain stdin (the Stop-hook payload) so the hook never blocks on a full pipe;
    # we do not need its contents — the decision is purely the board state.
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()
    except Exception:  # noqa: BLE001 — stdin quirks must not break the guard
        pass

    if not agent:
        # No identity → cannot attribute claimed work; never trap. Allow stop.
        return 0

    try:
        block, reason = evaluate(agent)
    except Exception as exc:  # noqa: BLE001 — a guard bug must NOT trap the agent
        logger.warning("idle-guard: evaluation failed (%s); allowing stop", exc)
        print(f"idle-guard: evaluation failed ({exc}); allowing stop", file=sys.stderr)
        return 0

    if block:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess/hook
    sys.exit(main())

# EOF
