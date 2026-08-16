#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards migration`` — directory-card migration verbs.

PR-D. Operator directive 2026-06-13 (via lead a2a ``3cf31901``): canonical
card = ``tasks/<id>/`` directory; flat tasks.yaml writes forbidden. Two-phase
rollout: ``migration plan`` is the read-side dry-run scanner; ``migration
apply`` is the operator-blessed mutation. The group token is a NOUN per the
SciTeX noun-verb CLI convention (audit-cli §1, non-leaf nodes are nouns).

Extracted verbatim from ``_main.py`` to keep that module under the 512-line
cap; behaviour is unchanged. Attached to the root group via :func:`register`,
matching the sibling ``_notifyd`` / ``_deliver`` modules. Named
``_migration_cli`` (not ``_migration``) to avoid shadowing the existing
``scitex_cards._migration`` engine module imported below.
"""

from __future__ import annotations

import click

from ._compat import spec_command_kwargs, spec_group_kwargs


def register(main: click.Group) -> None:
    """Attach the ``migration`` noun group to the root group."""
    main.add_command(migration_group)


@click.group(
    "migration",
    **spec_group_kwargs(
        summary="Directory-card migration verbs (operator directive 2026-06-13).",
        description=(
            "`migration plan` is the read-side dry-run scanner emitted "
            "for operator review. `migration apply` (gated, operator-"
            "blessed) performs the actual flat-to-directory conversion."
        ),
        command_categories=(("Core", ("plan", "apply")),),
    ),
)
def migration_group() -> None:
    """The ``migration`` noun group."""


@migration_group.command(
    "plan",
    **spec_command_kwargs(
        summary="Scan every lane + emit a plan classifying each row (no writes).",
        description=(
            "Scans every discovered lane + the global store; classifies "
            "each row as CANONICAL or NEEDS_* (DIR / NOTE / TITLE / "
            "COMMENT). The output is the artifact shown to the operator "
            "before any real `migration apply`."
        ),
        examples=(
            ("{prog} migration plan --json", "Machine-readable plan."),
            ("{prog} migration plan --markdown", "Operator-facing review doc."),
        ),
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the plan as JSON (machine-readable). Required by SciTeX "
    "§2 audit on read verbs.",
)
@click.option(
    "--markdown",
    "as_md",
    is_flag=True,
    help="Emit the plan as Markdown (operator-facing review document).",
)
def migration_plan_cmd(as_json: bool, as_md: bool) -> None:
    """Read-side dry-run scanner.

    Example:
      $ scitex-cards migration plan --json
      $ scitex-cards migration plan --markdown
    """
    import json as _json

    from scitex_cards._migration import render_markdown, scan_all_lanes

    fleet = scan_all_lanes()
    if as_md:
        click.echo(render_markdown(fleet))
        return
    if as_json:
        click.echo(_json.dumps(fleet.to_dict(), indent=2))
        return
    # Default: short summary.
    top = fleet.to_dict()
    click.echo(
        f"# migration plan: {top['lane_count']} lane(s), "
        f"{top['total_rows']} rows — "
        f"{top['canonical_rows']} canonical, "
        f"{top['needs_migration_rows']} need migration. "
        f"Pass --json or --markdown for detail."
    )


@migration_group.command(
    "apply",
    **spec_command_kwargs(
        summary="Run the directory-card migration across every discovered lane.",
        description=(
            "For every row that needs it, writes tasks/<id>/README.md "
            "(atomic + bytes-equal verified) THEN strips the migrated "
            "fields from tasks.yaml. Per-lane git commit at the end. "
            "Operator-blessed for ALL 7 lanes (2026-06-13)."
        ),
        examples=(
            ("{prog} migration apply --dry-run", "Preview without writing."),
            ("{prog} migration apply -y", "Run the migration."),
        ),
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the planned actions without touching disk. Required "
    "by SciTeX §2 audit on mutating verbs.",
)
@click.option(
    "-y",
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Skip the interactive confirmation. Required when the planned "
    "action would mutate the store.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit per-lane counts + per-row outcomes as JSON.",
)
def migration_apply_cmd(
    dry_run: bool,
    assume_yes: bool,
    as_json: bool,
) -> None:
    """Run the migration across every discovered lane + global store.

    Example:
      $ scitex-cards migration apply --dry-run
      $ scitex-cards migration apply -y
    """
    import json as _json
    import sys as _sys

    from scitex_cards._migration import apply_all_lanes

    if not dry_run and not assume_yes and _sys.stdin.isatty():
        raise click.ClickException(
            "`migration apply` mutates lane YAMLs + writes README.md files. "
            "Pass -y / --yes to confirm, or --dry-run to preview."
        )

    results = apply_all_lanes(dry_run=dry_run)

    if as_json:
        click.echo(
            _json.dumps(
                [r.to_dict() for r in results],
                indent=2,
            )
        )
        return

    # Human summary.
    total_written = 0
    total_updated = 0
    total_skipped = 0
    for lr in results:
        click.echo(
            f"# {lr.lane_path}: written={lr.written_count} "
            f"updated={lr.updated_count} skipped={lr.skipped_count} "
            f"git_committed={lr.git_committed} "
            f"({lr.git_skip_reason or 'ok'})"
        )
        total_written += lr.written_count
        total_updated += lr.updated_count
        total_skipped += lr.skipped_count
    click.echo(
        f"# TOTAL: written={total_written} updated={total_updated} "
        f"skipped={total_skipped}" + (" (DRY-RUN — no disk changes)" if dry_run else "")
    )


# EOF
