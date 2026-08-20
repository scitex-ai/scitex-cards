#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards stop-hook`` — the Claude Code Stop hook, emitted directly.

WHY THIS LIVES IN CARDS AND NOT IN THE RUNTIME (operator, 2026-07-18): the
runtime's first version of this parsed ``may-stop``'s stdout and its numbered
stderr hints, which made cards' output format a public API the runtime
depended on — the mirror image of the coupling we had just deleted in the
other direction. Cards owns both ends here: it knows what work exists AND
what a useful next instruction reads like, so the format stays private and
can change freely. The runtime's remaining job is registration into
``.claude/settings.json`` and nothing else.

TWO RAILS, ONE HOOK (the second added 2026-07-29):

1. RUNNABLE WORK — refuse the stop while the agent's board holds work, and say
   what the next action is.
2. UNCONFIRMED MESSAGES — the SECOND DELIVERY RAIL. Pull the agent's pending
   notifications straight out of the store, SHOW them here, and only then
   require the ack. Delivery previously had exactly one rail (the MCP channel
   push); when a spec/registration name mismatch made Claude Code discard every
   push, ``send()`` still returned normally, the drain acked on that success,
   and three weeks of operator DMs vanished — 228 inbox rows, ZERO unseen. This
   rail does not use the push and cannot be silenced by it.

ORDER OF OPERATIONS IS THE SAFETY PROPERTY. Pull, PRESENT, then require the
ack — never require an ack for a message the agent was not just shown, because
that is a deadlock (on the morning of the outage nothing had been shown, so no
agent COULD have acked). :mod:`scitex_cards._inbox_present` makes it
structural: it returns the rendered text together with the ids that text
contains, and this hook demands acks for those ids and no others.

BOUNDED. :mod:`scitex_cards._stop_hook_bound` carries the four failure
questions and their answers — a hook that can refuse forever is a new outage,
not a fix.

FAIL-OPEN, DELIBERATELY, PER RAIL. Any error — unreadable store, missing agent
id, malformed card — allows the stop and explains itself on stderr. An agent
wedged because the task store had a bad day is worse than an agent that stopped
early: the first is invisible and self-inflicted, the second is caught by the
failure-net sweep. Never let this hook be the reason an agent cannot finish.
"""

from __future__ import annotations

import json
import sys

import click

#: Cap on items named in the reason. The reason becomes the agent's next
#: instruction, and an instruction listing forty cards is not an instruction.
_MAX_ITEMS = 5

#: ``may_stop`` also reports unread mail, as a one-line summary for consumers
#: that only want a verdict. This hook PRESENTS the mail itself, so it drops
#: that summary rather than saying the same thing twice in one instruction.
_INBOX_ITEM_ID = "(inbox)"


def _read_payload() -> dict:
    """The Stop-hook JSON Claude Code writes on stdin. Never raises, never hangs.

    A TTY stdin means a human ran this by hand — reading it would block
    forever, so that is treated as "no payload". Everything else is read to EOF
    and parsed leniently: an unparseable payload costs us ``session_id`` and
    ``stop_hook_active``, which only degrades the loop bound, and must never
    cost the agent its ability to stop.
    """
    try:
        stream = sys.stdin
        if stream is None or stream.isatty():
            return {}
        raw = stream.read()
    except Exception:  # noqa: BLE001
        return {}
    try:
        data = json.loads(raw or "{}")
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _reason_for(verdict: dict) -> str:
    """Render the runnable-card verdict as an instruction the agent can act on."""
    items = verdict.get("items") or []
    shown = items[:_MAX_ITEMS]
    lines = [
        f"{i}. {it.get('card_id', '(no id)')} — {it.get('reason', '')}"
        f" — {it.get('next_action', '')}".rstrip(" —")
        for i, it in enumerate(shown, start=1)
    ]
    more = len(items) - len(shown)
    if more > 0:
        lines.append(f"{len(shown) + 1}. (+{more} more runnable item(s))")
    idle = verdict.get("idle_seconds")
    idle_note = f" You have been idle {idle}s." if idle else ""
    head = (
        f"You still have {len(items)} runnable item(s) on the board.{idle_note}"
        " Do not stop — take the next one now, or reconcile it (close it,"
        " block it with a NAMED gate, or defer it with a stated reason) so the"
        " board tells the truth:"
    )
    tail = (
        "Pick ONE and act on it in this turn. If something genuinely blocks"
        " you that is not on a card, write that card."
    )
    return "\n".join([head, *lines, tail])


def _inbox_section(agent: str, text: str, ids: list[str]) -> str:
    """The delivered messages plus the exact call that confirms them."""
    quoted = ", ".join(json.dumps(i) for i in ids)
    return "\n".join(
        [
            f"UNCONFIRMED MESSAGE(S) — {len(ids)} delivered to you right here,"
            " by the scitex-cards Stop hook reading the store directly. You are"
            " seeing them NOW; the push rail can fail silently and has.",
            text,
            "CONFIRM what you have just read, then act on it. Confirming is"
            " what releases this block:",
            f"  ack_notifications(agent={json.dumps(agent)}, ids=[{quoted}])",
            f"  (no MCP? scitex-cards inbox ack --agent {agent} " + " ".join(ids) + ")",
            "Anything you do not confirm stays unseen and is shown to you again"
            " next turn — confirming loses nothing and destroys nothing.",
        ]
    )


def _gather_inbox(agent: str, session_id, stop_hook_active: bool, store) -> tuple:
    """Return ``(section_text, warnings)`` for the unconfirmed-message rail.

    Fails open in every branch: an empty section means this rail contributes no
    block at all, and the warnings explain the silence on stderr.
    """
    from .._inbox_present import pending, present
    from .._stop_hook_bound import (
        MAX_PRESENTATIONS,
        counts_for,
        exhausted,
        record_presented,
    )

    warnings: list[str] = []
    records = pending(agent, store)
    if not records:
        return "", warnings

    # BOUND FIRST, on PRIOR counts, so an id we are about to give up on is
    # never re-rendered and never re-charged. Intersected with what is ACTUALLY
    # pending: the session counter also remembers ids that have since been
    # acked, and naming those in a warning would report a give-up on a message
    # that was in fact delivered.
    ids_now = {str(r.get("id")) for r in records}
    spent = exhausted(counts_for(session_id, store)) & ids_now
    live_records = [r for r in records if str(r.get("id")) not in spent]
    if not live_records:
        warnings.append(
            "giving up on " + ", ".join(sorted(spent)) + f": presented "
            f"{MAX_PRESENTATIONS} time(s) with no confirmation. They are still"
            " in the store and still unseen — NOT lost — but this hook stops"
            " blocking on them. If you cannot call ack_notifications, THAT is"
            " the bug to report."
        )
        return "", warnings

    text, ids = present(live_records)
    if not ids:
        # Nothing could be rendered, so nothing was delivered, so nothing may
        # be demanded. Blocking here would be precisely the deadlock.
        warnings.append(
            f"{len(live_records)} unconfirmed message(s) could not be rendered"
            " — allowing the stop rather than demanding an ack for something"
            " you were not shown"
        )
        return "", warnings

    state = record_presented(session_id, ids, store)
    if not state.get("durable") and stop_hook_active:
        warnings.append(
            "cannot persist the presentation counter AND this turn is already a"
            " stop-hook continuation — allowing the stop so this cannot loop"
            f" (still unconfirmed: {', '.join(ids)})"
        )
        return "", warnings
    if spent:
        warnings.append(
            "no longer demanding " + ", ".join(sorted(spent)) + " (retry limit)"
        )
    return _inbox_section(agent, text, ids), warnings


def evaluate(
    agent: str,
    *,
    session_id=None,
    stop_hook_active: bool = False,
    store=None,
) -> dict:
    """Decide the Stop hook's answer. Returns ``{"decision", "warnings"}``.

    The seam the tests drive against a REAL store — no mocks, because the bug
    this guards against was a mock-shaped one: every layer reported success
    while the message went nowhere.

    Each rail is guarded on its own, so a broken board still lets a pending
    message through, and a broken inbox still lets runnable cards through.
    """
    warnings: list[str] = []
    sections: list[str] = []

    try:
        section, inbox_warnings = _gather_inbox(
            agent, session_id, stop_hook_active, store
        )
        warnings.extend(inbox_warnings)
        if section:
            sections.append(section)
    except Exception as exc:  # noqa: BLE001 — fail-open per rail
        warnings.append(
            f"message rail unavailable, not blocking on it "
            f"({type(exc).__name__}: {exc})"
        )

    try:
        from .._may_stop import may_stop

        verdict = may_stop(agent, store)
        items = [
            it
            for it in (verdict.get("items") or [])
            if it.get("card_id") != _INBOX_ITEM_ID
        ]
        if items:
            sections.append(_reason_for({**verdict, "items": items}))
    except Exception as exc:  # noqa: BLE001 — fail-open per rail
        warnings.append(
            f"board rail unavailable, not blocking on it ({type(exc).__name__}: {exc})"
        )

    if not sections:
        # HARD CEILING: never block with an empty reason. A refusal that says
        # nothing leaves the agent stopped-but-refused, which is still idle.
        return {"decision": {}, "warnings": warnings}
    return {
        "decision": {"decision": "block", "reason": "\n\n".join(sections)},
        "warnings": warnings,
    }


@click.command("stop-hook")
@click.option(
    "--agent",
    default=None,
    help="Agent to check (default: $SCITEX_CARDS_AGENT_ID / $SCITEX_CARDS_AGENT_ID).",
)
def stop_hook_cmd(agent):
    """Emit Claude Code Stop-hook JSON: deliver pending messages, block on work.

    Example:
      $ echo '{}' | scitex-cards stop-hook
    """
    try:
        from .._store import _default_agent

        payload = _read_payload()
        result = evaluate(
            _default_agent(agent),
            session_id=payload.get("session_id"),
            stop_hook_active=bool(payload.get("stop_hook_active")),
        )
        for warning in result.get("warnings") or []:
            print(f"scitex-cards stop-hook: {warning}", file=sys.stderr)
        click.echo(json.dumps(result["decision"]))
    except Exception as exc:  # noqa: BLE001 — fail-open is the whole design
        # Never block on our own failure. Say so on stderr so the silence is
        # explainable, but let the agent stop.
        print(
            f"scitex-cards stop-hook: allowing stop, detector failed "
            f"({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        click.echo(json.dumps({}))


def register(main) -> None:
    main.add_command(stop_hook_cmd)


__all__ = ["evaluate", "register", "stop_hook_cmd"]

# EOF
