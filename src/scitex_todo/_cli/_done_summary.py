#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verbs ``done`` and ``summary``.

Extracted from ``_cli/_write.py`` (which had grown past the 512-line
convention) into its own module — same split shape every other
verb-group in this package already uses. Pure extraction: no behavior
change. ``register()`` is called from ``_write.py``.
"""

from __future__ import annotations

import json

import click

from scitex_dev.ecosystem import CliHelp, Example, SpecCommand

from .. import _store
from ._write import _TASKS_OPTION, _emit


# --------------------------------------------------------------------------- #
# done                                                                        #
# --------------------------------------------------------------------------- #
@click.command(
    "done",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Mark a task as done; stamps completion metadata.",
        description=(
            "Sets status=done and stamps _log_meta.completed_{at,by}. "
            "Idempotent: re-doneing an already-done task keeps the "
            "original stamp.",
        ),
        examples=(
            Example(
                "{prog} done my-task --by agent:proj-scitex-todo",
                "Complete a task with an explicit author.",
            ),
        ),
    ),
)
@click.argument("task_id")
@click.option(
    "--by",
    default=None,
    help="Override completed_by (default: $SCITEX_TODO_AGENT, then $USER).",
)
@click.option("--json", "as_json", is_flag=True)
@_TASKS_OPTION
def done_cmd(task_id, by, as_json, tasks_path) -> None:
    """Set status=done and stamp the completion meta."""
    try:
        done = _store.complete_task(tasks_path, task_id, by=by)
    except _store.TaskNotFoundError as exc:
        raise click.ClickException(str(exc)) from None
    stamp = done.get("_log_meta", {}).get("completed_at", "?")
    who = done.get("_log_meta", {}).get("completed_by", "?")
    _emit(
        done,
        as_json=as_json,
        human=f"done {done['id']}  (by {who} at {stamp})",
    )


# --------------------------------------------------------------------------- #
# summary                                                                     #
# --------------------------------------------------------------------------- #
@click.command(
    "summary",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Print task counts by status / scope / assignee.",
        description=(
            "Numeric progress report over the resolved store, optionally "
            "restricted to one scope/assignee before counting.",
        ),
        examples=(Example("{prog} summary --json", "Structured counts."),),
    ),
)
@click.option("--scope", default=None, help="Filter to this scope before counting.")
@click.option("--assignee", default=None)
@click.option("--json", "as_json", is_flag=True)
@_TASKS_OPTION
def summary_cmd(scope, assignee, as_json, tasks_path) -> None:
    """Counts by status, scope, assignee for the resolved store."""
    info = _store.summarize_tasks(tasks_path, scope=scope, assignee=assignee)
    if as_json:
        click.echo(json.dumps(info))
        return
    click.echo(f"# {info['store']}  ({info['total']} tasks)")
    click.echo("by_status:")
    for s, n in info["by_status"].items():
        click.echo(f"  {s:<12} {n}")
    click.echo("by_scope:")
    for s, n in sorted(info["by_scope"].items()):
        click.echo(f"  {s or '(none)':<28} {n}")
    click.echo("by_assignee:")
    for s, n in sorted(info["by_assignee"].items()):
        click.echo(f"  {s or '(none)':<28} {n}")


def register(main: click.Group) -> None:
    """Attach the `done` / `summary` verbs to the top-level CLI group."""
    main.add_command(done_cmd, name="done")
    main.add_command(summary_cmd, name="summary")


# EOF
