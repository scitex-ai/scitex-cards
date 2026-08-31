#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards inbox`` — inbox delivery verbs.

Verbs:
  * ``inbox ack`` — confirm delivery of specific notification ids. The
    STANDALONE surface onto :func:`scitex_cards._inbox_confirm.
    confirm_notifications` (NOT a second ack path — the same one verb, reachable
    without MCP). The Stop hook demands an ack, so an agent that has cards and
    nothing else must have a way to give one; otherwise the hook would be
    blocking where the actor cannot remediate.

The two backend-lifecycle verbs this group used to carry were removed
2026-08-28 along with the per-host inbox file they drove (operator ruling
2026-08-23). The notification rail lives in the store; there is nothing left
for them to move it to.

Attached to the root group via :func:`register`, matching the sibling
``_migration_cli`` module.
"""

from __future__ import annotations

import click


def register(main: click.Group) -> None:
    """Attach the ``inbox`` noun group to the root group."""
    main.add_command(inbox_group)


@click.group(
    "inbox",
    help="Inbox delivery verbs.",
)
def inbox_group() -> None:
    """The ``inbox`` noun group — the ``ack`` verb."""


@inbox_group.command(
    "ack",
    help=(
        "CONFIRM delivery of specific notification ids (the only "
        "cursor-advancing verb, reachable without MCP).\n\n"
        "Idempotent: re-acking an id is a no-op, never an error. Anything you "
        "do not ack stays unseen and is redelivered.\n\n"
        "\b\n"
        "Example:\n"
        "  $ scitex-cards inbox ack --agent scitex-cards n_abc n_def"
    ),
)
@click.option(
    "--agent",
    default=None,
    help="Whose inbox to confirm in (default: $SCITEX_CARDS_AGENT_ID).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the confirmation payload as JSON.",
)
@click.argument("ids", nargs=-1)
def inbox_ack_cmd(agent: str | None, as_json: bool, ids: tuple) -> None:
    """Confirm notification ids for an agent.

    \b
    Example:
      $ scitex-cards inbox ack --agent scitex-cards n_abc n_def
    """
    import json as _json

    from scitex_cards._inbox_confirm import confirm_notifications
    from scitex_cards._store import _default_agent

    if not ids:
        raise click.ClickException(
            "no notification ids given. Pass the `id` field of each record you "
            "actually read, e.g. `scitex-cards inbox ack --agent <you> n_abc`."
        )
    result = confirm_notifications(_default_agent(agent), list(ids))
    if as_json:
        click.echo(_json.dumps(result))
        return
    click.echo(
        f"# confirmed {len(result['confirmed'])} of {len(result['requested'])} "
        f"for {result['recipient_id']}\n"
        f"#   confirmed:         {', '.join(result['confirmed']) or '-'}\n"
        f"#   already confirmed: {', '.join(result['already_confirmed']) or '-'}\n"
        f"#   unknown:           {', '.join(result['unknown']) or '-'}"
    )


# EOF
