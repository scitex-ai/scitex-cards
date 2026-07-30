#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI `comment` verb: append an entry to ``task.comments[]``.

Thin wrapper over :func:`scitex_cards._store.comment_task` (which already
exists and is exported). Matches the surface shape of the sibling
mutation verbs in ``_write.py`` (``add`` / ``update`` / ``done``):

  * positional ``TASK_ID`` + ``TEXT``
  * ``--author`` overrides the ``$SCITEX_TODO_AGENT_ID`` → ``$USER``
    precedence chain (mirrors ``done --by``)
  * ``--json`` emits the structured ``{task_id, comment}`` payload
  * ``--dry-run`` prints the intended mutation and exits 0
  * ``-y`` / ``--yes`` accepted as a §2 forward-compat no-op
"""

from __future__ import annotations

import click

from .. import _store
from ._compat import spec_command_kwargs
from ._write import _emit


@click.command(
    "comment",
    **spec_command_kwargs(
        summary="Append a comment entry to task.comments[] (Gitea-compatible shape).",
        description=(
            "Wraps _store.comment_task. The timestamp is auto-stamped "
            "UTC by the store; --author overrides the "
            "$SCITEX_TODO_AGENT_ID -> $USER precedence chain.",
        ),
        examples=(
            (
                "{prog} comment my-task 'investigating crash' "
                '--author "$SCITEX_TODO_AGENT_ID"',
                "Append a comment as a specific author.",
            ),
        ),
    ),
)
@click.argument("task_id")
@click.argument("text")
@click.option(
    "--author",
    default=None,
    help="Override comment author (default: $SCITEX_TODO_AGENT_ID, then $USER).",
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the comment payload as JSON."
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be appended and exit 0 without mutating the store.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — comment is non-interactive; reserved for §2).",
)
def comment_cmd(task_id, text, author, as_json, dry_run, yes) -> None:
    """Append a comment to ``task.comments[]`` via ``_store.comment_task``."""
    _ = yes  # accepted for §2 compliance
    if dry_run:
        click.echo(
            f"# dry-run: would append comment to task_id={task_id!r} "
            f"author={author!r} text={text!r}"
        )
        return
    try:
        payload = _store.comment_task(None, task_id, text, by=author)
    except _store.TaskNotFoundError as exc:
        raise click.ClickException(str(exc)) from None
    except (_store.TaskValidationError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None
    entry = payload["comment"]
    _emit(
        payload,
        as_json=as_json,
        human=f"comment {payload['task_id']}  (by {entry['author']} at {entry['ts']})",
    )


def register(main: click.Group) -> None:
    """Attach the `comment` verb to the top-level CLI group."""
    main.add_command(comment_cmd, name="comment")


# EOF
