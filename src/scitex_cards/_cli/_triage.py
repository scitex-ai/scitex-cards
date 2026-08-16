#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verb ``scitex-cards triage`` — the backlog-consumption payload.

READ-ONLY. Draws an owner's pick-for-action sample (recency-weighted, see
:mod:`scitex_cards._backlog_triage`) plus their expired set, and prints it.
Mutation stays with the existing verbs (``update``, ``close``, ``comment``):
the consumer decides, this verb only puts the decision in front of them.

Primary consumer: a short-lived twin agent (sac concept, operator 2026-07-10)
spawned from its parent with the PARENT's ``SCITEX_CARDS_AGENT_ID``, which runs
``scitex-cards triage --mine --json``, decides each drawn card (start it, name
its blocker, cancel it, or keep it), and exits. The parent never stops.
"""

from __future__ import annotations

import json
import os

import click

from .._backlog_triage import (
    build_triage_body,
    expired,
    sample_for_triage,
)
from .._model import load_tasks
from .._paths import resolve_tasks_path


def register(main: click.Group) -> None:
    """Attach the ``triage`` verb to the root group."""
    main.add_command(triage_cmd)


@click.command(
    "triage",
    help=(
        "Draw an owner's deferred-backlog triage payload (read-only).\n\n"
        "Two sections:\n"
        "  DRAWN    ~N deferred cards, weighted toward RECENCY — decide each\n"
        "           now: start / name blocker / cancel / keep-deferred.\n"
        "  EXPIRED  deferred past the horizon (default 30d) — default outcome\n"
        "           is cancellation; rescue what you still want.\n\n"
        "Keep-deferred does NOT reset a card's age; the clock reads\n"
        "deferred_at, stamped once on entry into the backlog.\n\n"
        "Example:\n"
        "  scitex-cards triage --mine --json"
    ),
)
@click.option(
    "--agent",
    "agent",
    default=None,
    help="Owner to triage. Mutually exclusive with --mine.",
)
@click.option(
    "--mine",
    is_flag=True,
    help="Same as --agent $SCITEX_CARDS_AGENT_ID.",
)
@click.option(
    "--n",
    "sample_n",
    type=int,
    default=None,
    help="Sample size (default 10; env SCITEX_CARDS_TRIAGE_SAMPLE).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def triage_cmd(agent, mine, sample_n, as_json):
    if mine and agent:
        raise click.UsageError("--mine and --agent are mutually exclusive.")
    if mine:
        agent = (os.environ.get("SCITEX_CARDS_AGENT_ID") or "").strip()
        if not agent:
            raise click.UsageError("--mine requires SCITEX_CARDS_AGENT_ID to be set.")

    resolved = resolve_tasks_path(None)
    tasks = load_tasks(resolved)

    drawn = sample_for_triage(tasks, owner=agent, n=sample_n)
    rotten = expired(tasks, owner=agent)

    if as_json:
        payload = {
            "agent": agent,
            "drawn": [
                {
                    "id": c.id,
                    "title": c.title,
                    "owner": c.owner,
                    "age_hours": c.age_hours,
                    "weight": c.weight,
                }
                for c in drawn
            ],
            "expired": [
                {
                    "id": t.get("id"),
                    "title": t.get("title"),
                    "owner": t.get("agent") or t.get("assignee"),
                }
                for t in rotten
            ],
            "decisions": [
                "in_progress",
                "blocked+blocker",
                "cancelled",
                "keep-deferred",
            ],
        }
        click.echo(json.dumps(payload, default=str))
        return

    body = build_triage_body(drawn, rotten)
    click.echo(
        body if body else "Nothing to triage — no drawable or expired deferred cards."
    )
