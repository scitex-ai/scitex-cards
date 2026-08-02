#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI `reassign` verb: atomically change a card's owner (C5 primitive).

Thin wrapper over :func:`scitex_cards._store.reassign_task`. Matches the
surface shape of the sibling mutation verbs in ``_write.py`` (``add`` /
``update`` / ``done``) and ``_comment.py``:

  * positional ``TASK_ID`` + ``NEW_OWNER``
  * ``--by NAME`` overrides the ``$SCITEX_TODO_AGENT_ID`` → ``$USER``
    precedence chain (mirrors ``done --by`` / ``comment --author``)
  * ``--json`` emits the structured ``{task_id, from_owner, to_owner,
    actor, changed, task}`` payload
  * ``--dry-run`` prints the intended mutation and exits 0
  * ``-y`` / ``--yes`` accepted as a §2 forward-compat no-op

Lives in its own module (rather than ``_write.py``) because that module
is already at its line budget; ``_comment.py`` set the one-verb-per-file
precedent.
"""

from __future__ import annotations

import click

from .. import _store
from ._write import _emit


@click.command(
    "reassign",
    help=(
        "Atomically change a card's owner (C5 reassign primitive).\n\n"
        "Sets agent = assignee = NEW_OWNER and scope = 'agent:<NEW_OWNER>'\n"
        "in one locked write, appends an audit comment, and emits a\n"
        "canonical `reassigned` card-event (the notification path; delivery\n"
        "is a separate concern). Idempotent: reassigning to the SAME current\n"
        "owner is a no-op (no write, no event).\n\n"
        "Example:\n"
        "  scitex-cards reassign my-task <new-owner-agent-id> --by operator"
    ),
)
@click.argument("task_id")
@click.argument("new_owner")
@click.option(
    "--by",
    default=None,
    help="Override the actor (default: $SCITEX_TODO_AGENT_ID, then $USER).",
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the result payload as JSON."
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the intended reassignment and exit 0 without mutating the store.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — reassign is non-interactive; reserved for §2).",
)
def reassign_cmd(task_id, new_owner, by, as_json, dry_run, yes) -> None:
    """Atomically reassign ``task_id`` to ``new_owner`` via ``_store.reassign_task``."""
    _ = yes  # accepted for §2 compliance
    if dry_run:
        click.echo(
            f"# dry-run: would reassign task_id={task_id!r} -> {new_owner!r} by={by!r}"
        )
        return
    try:
        payload = _store.reassign_task(None, task_id, new_owner, by=by)
    except _store.TaskNotFoundError as exc:
        raise click.ClickException(str(exc)) from None
    except (_store.TaskValidationError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None
    if payload["changed"]:
        human = (
            f"reassigned {payload['task_id']}  "
            f"({payload['from_owner'] or '(unassigned)'} -> {payload['to_owner']})"
        )
    else:
        human = (
            f"reassign {payload['task_id']}  (noop — already owned by "
            f"{payload['to_owner']})"
        )
    _emit(payload, as_json=as_json, human=human)


def register(main: click.Group) -> None:
    """Attach the `reassign` verb to the top-level CLI group."""
    main.add_command(reassign_cmd, name="reassign")


# EOF
