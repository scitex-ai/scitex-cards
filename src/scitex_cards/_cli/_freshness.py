#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards freshness-gc`` — forget stale cards in one locked transaction.

WHY THE CUTOFF IS AN INPUT AND NOT A CONSTANT. The organization-wide default
number of days is scitex-dev's to own: it is one number for every package, and a
leaf that hard-codes its own would drift from it silently. So this verb takes
either an explicit ``--cutoff`` (ISO-8601, what scitex-dev passes) or a
``--days`` convenience that converts through the SAME helper the Python API
uses, and the store owns everything downstream of that decision.

WHY ``--dry-run`` EXISTS AND IS THE FIRST THING A CALLER SHOULD USE: the sweep
is irreversible from this verb's perspective (it preserves rows and history, but
the status flip is a write). ``--dry-run`` runs the identical selection, the
identical lock, and the identical readback, and skips only the UPDATE — so the
count a caller sees in a dry run is the count the real run will flip, which is
what makes it a rehearsal rather than a guess.
"""

from __future__ import annotations

import click

from .._store_freshness_gc import cutoff_from_days, freshness_gc


def _json_out(payload: dict) -> None:
    import json

    click.echo(json.dumps(payload, indent=2, sort_keys=True))


@click.command(
    "freshness-gc",
    help=(
        "Forget stale cards: flip goal/in_progress/blocked/deferred to\n"
        "cancelled when their last_activity (or created_at) is older than the\n"
        "cutoff. One locked transaction, one UPDATE for the whole set, no\n"
        "per-card comments, no separate ledger. Rows and history are\n"
        "preserved — a forgotten card stops being work, not being a record.\n"
        "\n"
        "Examples:\n"
        "  $ scitex-cards freshness-gc --days 30 --dry-run\n"
        "  $ scitex-cards freshness-gc --cutoff 2026-08-18T00:00:00Z\n"
        "  $ scitex-cards freshness-gc --days 30 --json"
    ),
)
@click.option(
    "--cutoff",
    type=str,
    default=None,
    help=(
        "ISO-8601 instant; cards whose clock is older are forgotten. This is\n"
        "the interface scitex-dev passes the organization-wide default through."
    ),
)
@click.option(
    "--days",
    type=float,
    default=None,
    help="Convenience: forget cards untouched for this many days (24h each).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Run the identical selection and lock, skip only the UPDATE.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the counts as JSON.")
def freshness_gc_cmd(
    cutoff: str | None,
    days: float | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Run the sweep, or rehearse it."""
    if (cutoff is None) == (days is None):
        raise click.UsageError("pass exactly one of --cutoff or --days")
    resolved = cutoff if cutoff is not None else cutoff_from_days(days or 0.0)
    result = freshness_gc(cutoff=resolved, dry_run=dry_run)
    if as_json:
        _json_out(result.as_dict())
        return
    verb = "would forget" if dry_run else "forgot"
    click.echo(
        f"cutoff {result.cutoff}: {verb} {result.matched if dry_run else result.cancelled} "
        f"card(s); {result.remaining} still older than the cutoff"
    )
    if result.sample_ids:
        click.echo("  e.g. " + ", ".join(result.sample_ids))


def register(main: click.Group) -> None:
    """Attach the sweep to the root group (the repo's per-module pattern)."""
    main.add_command(freshness_gc_cmd)
