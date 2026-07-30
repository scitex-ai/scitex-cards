#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI ``help-wait`` / ``help-clear`` verbs: the "agent is stuck waiting on
the operator" card, lifted out of the dotfiles Notification hook.

A dotfiles hook used to hand-roll these cards by shelling out to the generic
``scitex-todo add / update / list-tasks`` verbs; a schema drift broke it
silently. These two verbs own the card semantics in-package (the single
source of truth) so the hook can become a thin trigger that calls one verb.

  scitex-todo help-wait  <agent> [--question TEXT] [--host HOST]
  scitex-todo help-clear <agent>

The card contract (byte-for-byte what the old hook produced) lives in
:mod:`scitex_cards._help_wait`; these verbs are thin click wrappers around it,
writing to the ambient store.
"""

from __future__ import annotations

import click

from .. import _help_wait
from ._compat import spec_command_kwargs
from ._write import _emit


@click.command(
    "help-wait",
    **spec_command_kwargs(
        summary="UPSERT the canonical 'agent is waiting on the operator' card.",
        description=(
            "Idempotent: exactly one help-<agent>-waiting card per "
            "agent; a re-run refreshes the note + last_activity in "
            "place (never duplicates).",
        ),
        examples=(
            (
                '{prog} help-wait "$SCITEX_TODO_AGENT_ID" '
                "--question 'merge PR #240 or wait for CI?'",
                "Raise (or refresh) the waiting card.",
            ),
        ),
    ),
)
@click.argument("agent")
@click.option(
    "--question",
    default=None,
    help="The question text shown in the card note (empty -> placeholder).",
)
@click.option(
    "--host",
    default=None,
    help="Where the agent is waiting (default: best-effort hostname).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the upserted card as JSON.")
def help_wait_cmd(agent, question, host, as_json) -> None:
    """UPSERT the help-<agent>-waiting card (status=blocked / operator-decision)."""
    try:
        card = _help_wait.help_wait(None, agent, question=question, host=host)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from None
    _emit(
        card,
        as_json=as_json,
        human=f"help-wait {card['id']}  ({card['status']}/{card['blocker']}) {card['title']}",
    )


@click.command(
    "help-clear",
    **spec_command_kwargs(
        summary="Resolve the help-<agent>-waiting card (status=done + clear blocker).",
        description="No-op (exit 0) if the card does not exist.",
        examples=(
            ('{prog} help-clear "$SCITEX_TODO_AGENT_ID"', "Clear the waiting card."),
        ),
    ),
)
@click.argument("agent")
@click.option("--json", "as_json", is_flag=True, help="Emit the clear payload as JSON.")
def help_clear_cmd(agent, as_json) -> None:
    """Resolve the help-<agent>-waiting card; no-op when absent."""
    try:
        payload = _help_wait.help_clear(None, agent)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from None
    if payload["cleared"]:
        human = f"help-clear {payload['task_id']}  (resolved)"
    else:
        human = f"help-clear {payload['task_id']}  (no-op — card not present)"
    _emit(payload, as_json=as_json, human=human)


def register(main: click.Group) -> None:
    """Attach the `help-wait` / `help-clear` verbs to the top-level CLI group."""
    main.add_command(help_wait_cmd, name="help-wait")
    main.add_command(help_clear_cmd, name="help-clear")


# EOF
