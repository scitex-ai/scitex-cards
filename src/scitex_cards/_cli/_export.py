#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Human-readable task export commands."""

from __future__ import annotations

from pathlib import Path

import click

from .._markdown import build_markdown
from .._store import list_tasks
from ._compat import spec_command_kwargs


@click.command(
    "export",
    **spec_command_kwargs(
        summary="Export tasks as a deterministic document.",
        description=(
            "Reads the resolved store and emits stable Markdown. The export is "
            "read-only and writes to stdout unless --output is supplied."
        ),
        examples=(("{prog} export --format markdown", "Write Markdown to stdout."),),
    ),
)
@click.option(
    "--format",
    "format_name",
    type=click.Choice(["markdown"], case_sensitive=True),
    default="markdown",
    show_default=True,
    help="Document format.",
)
@click.option(
    "--group-by",
    type=click.Choice(["status", "project", "assignee", "none"]),
    default="status",
    show_default=True,
    help="Group task lines under stable headings.",
)
@click.option(
    "--detail",
    type=click.Choice(["title", "summary", "full"]),
    default="title",
    show_default=True,
    help="Amount of card detail to include.",
)
@click.option(
    "--hierarchy",
    is_flag=True,
    help="Nest children below parents when both are in the same group.",
)
@click.option(
    "--status",
    "statuses",
    multiple=True,
    help="Include this status. Repeat to include more than one.",
)
@click.option("--project", default=None, help="Match project exactly.")
@click.option("--assignee", default=None, help="Match assignee exactly.")
@click.option(
    "--scope",
    default=None,
    help="Match scope exactly (use '' to ignore $SCITEX_CARDS_SCOPE).",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the document here instead of stdout.",
)
@click.pass_context
def export_cmd(
    ctx: click.Context,
    format_name: str,
    group_by: str,
    detail: str,
    hierarchy: bool,
    statuses: tuple[str, ...],
    project: str | None,
    assignee: str | None,
    scope: str | None,
    output: Path | None,
) -> None:
    """Export cards in a stable human-readable format."""
    _ = format_name
    loader = list_tasks
    if isinstance(ctx.obj, dict) and callable(ctx.obj.get("task_loader")):
        loader = ctx.obj["task_loader"]
    tasks = loader(
        scope=scope,
        assignee=assignee,
        statuses=list(statuses) if statuses else None,
        project=project,
    )
    text = build_markdown(
        tasks,
        detail=detail,
        group_by=group_by,
        hierarchy=hierarchy,
    )
    if output is not None:
        output.write_bytes(text.encode("utf-8"))
        return
    click.echo(text, nl=False)


def register(main: click.Group) -> None:
    """Attach the export command to the root group."""
    main.add_command(export_cmd)


__all__ = ["export_cmd", "register"]

# EOF
