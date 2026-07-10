#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI `comment` verb: append an entry to ``task.comments[]``.

Thin wrapper over :func:`scitex_todo._store.comment_task` (which already
exists and is exported). Matches the surface shape of the sibling
mutation verbs in ``_write.py`` (``add`` / ``update`` / ``done``):

  * positional ``TASK_ID`` + ``TEXT``
  * ``--author`` overrides the ``$SCITEX_TODO_AGENT`` → ``$USER``
    precedence chain (mirrors ``done --by``)
  * ``--json`` emits the structured ``{task_id, comment}`` payload
  * ``--dry-run`` prints the intended mutation and exits 0
  * ``-y`` / ``--yes`` accepted as a §2 forward-compat no-op
  * ``--tasks`` honors the standard store-resolution precedence
"""

from __future__ import annotations

import click

from scitex_dev.ecosystem import CliHelp, Example, SpecCommand

from .. import _store
from ._write import _TASKS_OPTION, _emit


@click.command(
    "comment",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Append a comment entry to task.comments[] (Gitea-compatible shape).",
        description=(
            "Wraps _store.comment_task. The timestamp is auto-stamped "
            "UTC by the store; --author overrides the "
            "$SCITEX_TODO_AGENT -> $USER precedence chain.",
        ),
        examples=(
            Example(
                "{prog} comment my-task 'investigating crash' "
                "--author agent:proj-scitex-todo",
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
    help="Override comment author (default: $SCITEX_TODO_AGENT, then $USER).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the comment payload as JSON.")
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
@_TASKS_OPTION
def comment_cmd(task_id, text, author, as_json, dry_run, yes, tasks_path) -> None:
    """Append a comment to ``task.comments[]`` via ``_store.comment_task``."""
    _ = yes  # accepted for §2 compliance
    if dry_run:
        click.echo(
            f"# dry-run: would append comment to task_id={task_id!r} "
            f"author={author!r} text={text!r}"
        )
        return
    try:
        payload = _store.comment_task(tasks_path, task_id, text, by=author)
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
