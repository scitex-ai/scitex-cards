#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards dev list-undelivered`` — name the messages that never arrived.

WHY UNDER ``dev``. This is upkeep of the agent's own operation, not card
product surface: it runs at every restart and on a cadence, exactly like the
``dev`` group's own description says ("typically run on a schedule rather than
by hand"). Doctrine 20_dev-commands.md §13 puts self-maintenance under ``dev``.

WHY ``list-undelivered``. Doctrine 06_noun-verb-catalog: compounds are
kebab-case and VERB-FIRST, and ``list`` is the canonical enumerate-verb —
the same shape as the sibling ``list-stale`` and ``list-tasks``. A bare
``undelivered`` leaf would be an adjective where §1 requires a verb.
``check-`` was considered and rejected: §1f maps both ``check`` and ``verify``
onto ``validate``, and this verb does not validate anything — it enumerates
rows and names them, which is ``list``.
"""

from __future__ import annotations

import json

import click

from ._compat import spec_command_kwargs
from ._dev import get_dev_group

__all__ = ["register", "report_as_dict"]


def report_as_dict(report) -> dict:
    """The report as JSON-ready data, with the SAME keys on every path.

    A caller must never have to guess which key exists this run, so the
    failure paths carry the full shape too — empty lists and ``cannot_tell``
    signals rather than absent fields.
    """
    return {
        "agent": report.agent,
        "rail": report.rail,
        "control_passed": report.control_passed.value,
        "total_rows": report.total_rows,
        "inbound_undelivered": report.inbound_undelivered.value,
        "outbound_undelivered": report.outbound_undelivered.value,
        "inbound": [
            {
                "id": row.id,
                "peer": row.peer,
                "ts": row.ts,
                "when": row.when,
                "preview": row.preview,
            }
            for row in report.inbound
        ],
        "outbound": [
            {
                "id": row.id,
                "peer": row.peer,
                "ts": row.ts,
                "when": row.when,
                "preview": row.preview,
            }
            for row in report.outbound
        ],
        "detail": report.detail,
    }


def register(main: click.Group) -> click.Command:
    """Attach ``dev list-undelivered`` to the root group."""
    dev = get_dev_group(main)

    @dev.command(
        "list-undelivered",
        **spec_command_kwargs(
            summary="Name the messages on the durable rail that never arrived.",
            description=(
                "Answers 'did I miss anything?' from the DURABLE channel "
                "rail rather than from a2a_inbox, which is an in-memory "
                "buffer that a restart empties — so it reads 0 whether or "
                "not anything was sent, the same answer in the good and the "
                "bad case. Run it at every restart and on a cadence.",
                "A POSITIVE CONTROL RUNS FIRST and is mandatory: the rail "
                "must hold rows before any filtered zero is believed. The "
                "per-agent runtime/<agent>/state.db shards carry the "
                "channel_events TABLE with ZERO ROWS, so querying one "
                "succeeds and looks exactly like an all-clear. When the "
                "control fails the answer is CANNOT TELL, which is NOT a "
                "pass, and every signal says so.",
                "Rows are NAMED — id, peer, timestamp, first 80 characters "
                "— so a failed send can be resent without asking the peer "
                "what they said. Exit 0 = control passed and nothing "
                "undelivered; 1 = undelivered messages found; 2 = CANNOT "
                "TELL.",
            ),
            examples=(
                (
                    "{prog} dev list-undelivered",
                    "The restart check, for the current agent.",
                ),
                (
                    "{prog} dev list-undelivered --json",
                    "Structured, for a cadence that records history.",
                ),
                (
                    "{prog} dev list-undelivered --agent scitex-hub",
                    "Ask the same question about another agent.",
                ),
            ),
        ),
    )
    @click.option(
        "--agent",
        default=None,
        help="Agent identity to report on. Defaults to $SCITEX_CARDS_AGENT_ID.",
    )
    @click.option(
        "--rail",
        default=None,
        type=click.Path(dir_okay=False),
        help=(
            "Rail database. Defaults to the SINGLE TOP-LEVEL "
            "~/.scitex/agent-container/runtime/state.db. Deliberately NOT "
            "$SCITEX_AGENT_CONTAINER_STATE_DB, which points at the "
            "per-agent shard and holds no fleet rows."
        ),
    )
    @click.option(
        "--json",
        "as_json",
        is_flag=True,
        default=False,
        help="Emit the report as structured JSON.",
    )
    def list_undelivered(agent: "str | None", rail: "str | None", as_json: bool) -> None:
        from .._channel_identity import resolve_agent_id
        from .._channel_rail import read_undelivered

        try:
            who = resolve_agent_id(agent)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

        report = read_undelivered(who, rail)

        if as_json:
            click.echo(json.dumps(report_as_dict(report), indent=2))
        else:
            click.echo(report.describe())

        if report.cannot_tell:
            raise SystemExit(2)
        if report.actionable:
            raise SystemExit(1)

    return list_undelivered


# EOF
