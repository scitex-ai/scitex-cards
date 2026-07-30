#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-cards reconcile-merged-prs` — auto-close cards whose linked PR merged.

Terminal twin of the periodic JobSpec (``_jobs_provider.py``). Scans open
cards (pending / in_progress / blocked) that carry a ``pr_url``, asks GitHub
whether the PR is merged, and — for the merged ones — flips the card to
``done`` plus an audit comment. DRY-RUN by default (report only); ``--apply``
performs the mutation (mirrors scitex-dev's board-mutation pattern — no
silent auto-close).

The merge-state check + the pure decision logic live in
:mod:`scitex_cards._reconcile_prs`; this module is thin CLI plumbing.
"""

from __future__ import annotations

import json

import click

from .._paths import resolve_tasks_path
from .._reconcile_prs import reconcile_merged_prs


@click.command(
    "reconcile-merged-prs",
    help=(
        "Auto-close cards whose linked PR (pr_url) has MERGED.\n\n"
        "Scans pending / in_progress / blocked cards with a pr_url; for "
        "each, checks the PR merge-state (gh, then a curl GitHub REST "
        "fallback) and — when merged — flips the card to `done` with an "
        "audit comment. DRY-RUN by default; pass --apply to mutate.\n\n"
        "Examples:\n"
        "  scitex-cards reconcile-merged-prs            # dry-run report\n"
        "  scitex-cards reconcile-merged-prs --apply    # actually close\n"
        "  scitex-cards reconcile-merged-prs --json"
    ),
)
@click.option(
    "--apply",
    "apply",
    is_flag=True,
    help=(
        "Actually flip merged-PR cards to `done` + comment. Without this "
        "flag the verb is DRY-RUN (report only). Required by SciTeX §2 "
        "audit on mutating verbs."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the summary as JSON (machine-readable).",
)
def reconcile_merged_prs_cmd(apply: bool, as_json: bool) -> None:
    """Run the reconcile pass and print the summary (dry-run by default)."""
    resolved = resolve_tasks_path(None)
    result = reconcile_merged_prs(resolved, apply=apply)

    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2))
        return

    if apply:
        click.echo(
            f"# reconcile-merged-prs (APPLIED): closed {len(result.closed)} "
            f"card(s) whose PR merged"
        )
        for c in result.closed:
            click.echo(f"  closed {c['id']:55} | {c['pr_url']}")
    else:
        click.echo(
            f"# reconcile-merged-prs (DRY-RUN): {len(result.would_close)} "
            f"card(s) would close (pass --apply to mutate)"
        )
        for c in result.would_close:
            click.echo(f"  would-close {c['id']:50} | {c['pr_url']}")

    if result.skipped:
        summary = ", ".join(f"{k}={v}" for k, v in sorted(result.skipped.items()))
        click.echo(f"# skipped: {summary}")


def register(main: click.Group) -> None:
    """Attach the `reconcile-merged-prs` verb to the top-level CLI group."""
    main.add_command(reconcile_merged_prs_cmd, name="reconcile-merged-prs")


# EOF
