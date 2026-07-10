#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``index <verb>`` — SQLite derived-index lifecycle (PR-B of Stage 2 plan,
lead a2a `aa02fb0e`).

YAML stays authoritative; the index is a rebuildable read cache.

Extracted from ``_cli/_main.py`` (which had grown past the 512-line
convention) into its own module — same split shape every other
verb-group in this package already uses. Pure extraction: no behavior
change. ``register()`` is called from ``_main.py``.
"""

from __future__ import annotations

import click

from scitex_dev.ecosystem import CliHelp, Example, SpecCommand, SpecGroup


@click.group(
    "index",
    cls=SpecGroup,
    command_categories=[("Core", ["rebuild", "info"])],
    help_spec=CliHelp(
        summary="Manage the SQLite derived-index (rebuildable read cache).",
        description=(
            "YAML stays authoritative; ~/.scitex/todo/.tasks.index.sqlite "
            "is a rebuildable read cache built from it.",
        ),
        examples=(
            Example("{prog} index rebuild -y", "Rebuild the index from YAML."),
            Example("{prog} index info", "Report row count + last-rebuild time."),
        ),
    ),
)
def index_group() -> None:
    """The ``index`` noun group — verbs rebuild + info."""


@index_group.command(
    "rebuild",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Drop + repopulate the SQLite index from the YAML source(s).",
        description=(
            "Rebuilds from the global store + every discovered "
            "per-project lane (PR #137 union policy).",
        ),
        examples=(Example("{prog} index rebuild -y", "Rebuild now."),),
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be rebuilt (source paths + projected row "
    "count) without touching the index. Required by SciTeX §2 audit on "
    "mutating verbs.",
)
@click.option(
    "-y",
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Skip the interactive confirmation. Required when the planned "
    "action would mutate the index and stdin is a TTY.",
)
def index_rebuild_cmd(dry_run: bool, assume_yes: bool) -> None:
    """Drop + repopulate the SQLite index from the YAML source(s).

    Example:
      $ scitex-todo index rebuild -y
    """
    import sys as _sys

    from scitex_todo._django.services import _discover_lanes
    from scitex_todo._index import index_path, rebuild_index
    from scitex_todo._paths import resolve_tasks_path

    global_path = resolve_tasks_path(None)
    lane_paths = _discover_lanes()
    target = index_path()

    if dry_run:
        click.echo(
            f"# dry-run: would rebuild {target}\n"
            f"#   global: {global_path}\n"
            f"#   lanes ({len(lane_paths)}):"
        )
        for lp in lane_paths:
            click.echo(f"#     - {lp}")
        return

    if not assume_yes and _sys.stdin.isatty():
        raise click.ClickException(
            "`index rebuild` mutates the SQLite index. Pass -y / --yes "
            "to confirm, or --dry-run to preview."
        )
    stats = rebuild_index(global_path, lane_paths)
    click.echo(
        f"# rebuilt {target}: {stats['total']} tasks "
        f"({stats['global']} global + {stats['lanes']} lane, "
        f"{stats['skipped']} skipped)"
    )


@index_group.command(
    "info",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Print row count / last-rebuild time / schema version of the index.",
        examples=(
            Example("{prog} index info", "Human-readable summary."),
            Example("{prog} index info --json", "Structured JSON."),
        ),
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON. Required by SciTeX §2 audit on read verbs.",
)
def index_info_cmd(as_json: bool) -> None:
    """Read-side report on the SQLite index.

    Example:
      $ scitex-todo index info
      $ scitex-todo index info --json
    """
    import json as _json

    from scitex_todo._index import info

    payload = info()
    if as_json:
        click.echo(_json.dumps(payload))
        return
    if not payload["exists"]:
        click.echo(f"# index does not exist yet: {payload['path']}")
        click.echo("# run `scitex-todo index rebuild -y` to populate.")
        return
    click.echo(
        f"# index: {payload['path']}\n"
        f"#   rows: {payload['rows']}\n"
        f"#   schema version: {payload['index_version']}\n"
        f"#   last index at: {payload['last_index_at']}\n"
        f"#   yaml mtime: {payload['yaml_mtime']}\n"
        f"#   lane count: {payload['lane_count']}"
    )


def register(main: click.Group) -> None:
    """Attach the ``index`` noun group to the root CLI."""
    main.add_command(index_group)


# EOF
