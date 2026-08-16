#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The WIP gate's ENFORCEMENT half — the add-path decision, in one place.

``_throughput.py`` owns the MEASUREMENT (``count_wip_for_agent`` /
:class:`~scitex_cards._throughput.WipReport` / ``is_warn`` / ``is_refuse``).
This module owns what the writer DOES with that measurement, so the policy —
including the emergency-recording exemption below — is readable in one screen
instead of buried inline in ``_store.add_task``. Consumed by ``_store.py`` the
same way ``_store_enums`` / ``_store_verify`` are.

WHY THE EXEMPTION EXISTS (incident 2026-07-12)
----------------------------------------------
The operator escalated a P0 (fleet-wide config/state-loss hazard). The agent
went to CARD it. The board REFUSED::

    Error: WIP gate refuses add: scitex-hub already has 40 open tasks
    (>= 2 × limit 20). Close existing tasks before adding more.

The incident was then buried as a comment on an unrelated card — the worst
outcome for the one class of card that most needs to be findable.

A WIP cap is a THROUGHPUT-SHAPING device and it was sitting on the
EMERGENCY-RECORDING path. Those have opposite requirements. "Your board is
untidy" must never mean "you may not record that production is on fire."

Worse, the perverse incentive: under outage pressure the cheapest way past the
gate is to CLOSE CARDS YOU HAVE NOT FINISHED. A cap that pressures agents to
falsify card state during an emergency is worse than no cap — and the old
refusal text ("Close existing tasks before adding more") said so out loud.

THE RULE
--------
A card at ``priority <= 1`` is NEVER gated. No flag to remember, no thinking
required mid-outage: filing a P0/P1 simply works.

* Priority is LOWER = MORE URGENT (the CLI: "Integer priority (lower =
  earlier)"). On the live store, 21 of 28 incident cards are priority 1 — so
  the predicate is ``<= 1``, not ``>= 8``.
* The exemption keys on PRIORITY and deliberately NOT on a new ``kind`` enum
  value. ``kind`` is a CLOSED enum (task / compute / decision / status) and
  agents still running 0.7.50 have no tolerant reader — one unknown value
  aborts the whole store load for them. That is exactly the 2026-07-10 fleet
  outage. Deploy-order rule: tolerant reader fleet-wide FIRST, new enum value
  SECOND. ``priority`` is an open integer field and needs neither.
* The bypass is LOUD, not silent: a card admitted over the refuse threshold is
  stamped with an audit comment naming the agent's WIP count and the limit
  (:func:`_override_comment`). Abuse is therefore self-reporting, and we can
  MEASURE whether P1 gets inflated once agents learn the rule. A silent bypass
  would be its own silent-absence bug.
"""

from __future__ import annotations

import sys

from ._comment_ids import stamp_comment_id
from ._throughput import WIP_STATUSES, evaluate_wip

# Cards at or below this priority are never gated. Lower = more urgent, so this
# is "P0 and P1" — the emergency band.
EXEMPT_PRIORITY_MAX = 1

# Tag on the audit comment, so the override is greppable / countable across the
# store ("did P1 get inflated once agents learned the rule?").
OVERRIDE_COMMENT_KIND = "wip-override"


def is_priority_exempt(priority: object) -> bool:
    """True when ``priority`` is urgent enough to bypass the WIP gate.

    Lower = more urgent, so the predicate is ``priority <= 1`` (P0/P1). A
    missing / null / non-integer priority is NOT exempt: the exemption is for
    DECLARED emergencies, not for cards that merely forgot to say. ``bool`` is
    rejected explicitly — ``True`` is an ``int`` of value 1 in Python, and a
    truthy flag fumbled into ``priority`` must not silently buy an exemption.
    """
    if priority is None or isinstance(priority, bool):
        return False
    try:
        return int(priority) <= EXEMPT_PRIORITY_MAX
    except (TypeError, ValueError):
        return False


def refusal_message(rep) -> str:
    """The hard-refusal text.

    It MUST name the emergency path. The old text said only "Close existing
    tasks before adding more" — during an incident that is the worst possible
    instruction, because closing unfinished cards is precisely the falsification
    this gate must not incentivise.
    """
    return (
        f"WIP gate refuses add: {rep.agent} already has {rep.wip_count} tasks "
        f"in_progress (>= 2 × limit {rep.limit}).\n"
        f"EMERGENCY PATH — recording an incident is NEVER gated:\n"
        f"  • file it with priority <= {EXEMPT_PRIORITY_MAX} (P0/P1; lower = "
        f"more urgent) — a priority <= {EXEMPT_PRIORITY_MAX} card is never "
        f"refused, whatever your WIP count;\n"
        f"  • a card filed as deferred or blocked is never gated either.\n"
        f"Otherwise this is ordinary new work: FINISH or PARK one in-flight "
        f"card before starting another. Do NOT close cards you have not "
        f"finished to get past this gate. See SCITEX_CARDS_WIP_LIMIT env."
    )


def _override_comment(rep, priority: object, author: str | None, ts: str) -> dict:
    """The audit stamp written onto a card that was admitted over the cap."""
    return stamp_comment_id(
        {
            "author": author or "unknown",
            "ts": ts,
            "kind": OVERRIDE_COMMENT_KIND,
            "text": (
                f"[wip-override] Created OVER the WIP cap: {rep.agent} had "
                f"{rep.wip_count} tasks in_progress at insert time (limit "
                f"{rep.limit}; refuse threshold {2 * rep.limit}). Admitted by "
                f"the emergency-recording exemption because "
                f"priority={priority} <= {EXEMPT_PRIORITY_MAX}. Recording an "
                f"incident is never gated — this stamp exists so the bypass "
                f"is auditable."
            ),
        }
    )


def enforce_wip_gate(new: dict, tasks: list[dict], *, now_iso: str) -> None:
    """Apply the WIP gate to the card ``new`` about to be inserted.

    Mutates ``new`` in place (appends the audit comment) when the card is
    admitted over the refuse threshold by the emergency exemption. Raises
    :class:`~scitex_cards._model.TaskValidationError` when the card is refused.
    No-op otherwise.

    The gate bounds work STARTED, never work RECORDED: it fires only when the
    INCOMING card is itself ``in_progress``, and it counts only the agent's
    other ``in_progress`` cards. Filing a card as blocked / deferred / goal —
    writing down a thing that exists — is always allowed. The inverse jammed the
    board shut on 2026-07-10.

    Direct YAML hand-edits bypass this gate by design (CLI/MCP path enforcement
    only — the operator wants the CLI/MCP path made fat so hand-edits are
    unnecessary, not policed).
    """
    from ._model import TaskValidationError

    agent = new.get("agent")
    if not agent or new.get("status") not in WIP_STATUSES:
        return

    rep = evaluate_wip(tasks, agent)
    if rep is None:
        return

    priority = new.get("priority")
    exempt = is_priority_exempt(priority)

    if rep.is_refuse:
        if not exempt:
            raise TaskValidationError(refusal_message(rep))
        # Admitted — and said so, on the card, where a human reviewing the
        # incident later will actually see it.
        comments = new.setdefault("comments", [])
        comments.append(
            _override_comment(rep, priority, new.get("created_by"), now_iso)
        )
        print(
            f"WARN: WIP gate OVERRIDDEN — {rep.agent} is at {rep.wip_count} "
            f"tasks in_progress (>= 2 × limit {rep.limit}); admitted "
            f"{new.get('id')!r} because priority={priority} <= "
            f"{EXEMPT_PRIORITY_MAX}. The card carries an audit stamp.",
            file=sys.stderr,
        )
        return

    if rep.is_warn:
        print(
            f"WARN: WIP gate — {rep.agent} now has {rep.wip_count + 1} tasks "
            f"in_progress (limit {rep.limit}). Completion is not keeping up "
            f"with starts; finish existing before starting more.",
            file=sys.stderr,
        )


__all__ = [
    "EXEMPT_PRIORITY_MAX",
    "OVERRIDE_COMMENT_KIND",
    "enforce_wip_gate",
    "is_priority_exempt",
    "refusal_message",
]
