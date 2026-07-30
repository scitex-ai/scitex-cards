#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verb ``scitex-cards deliver`` — one-shot notification delivery pass.

Runs :func:`scitex_cards._delivery.deliver_pending` once: read every
configured recipient's pending notifications and hand them to the channels
configured for that user, recording outcomes in the delivery ledger.

This one-shot command is slice 1's "always-on" stand-in — it is
cron/loop-runnable (run it on a timer to keep notifications flowing). The
long-running daemon + systemd unit are a LATER slice and are intentionally
NOT built here.
"""

from __future__ import annotations

import json

import click


def register(main: click.Group) -> None:
    """Attach the ``deliver`` verb to the root group."""
    main.add_command(deliver_cmd)


@click.command(
    "deliver",
    help=(
        "Run ONE notification-delivery pass (cron/loop-runnable).\n\n"
        "Reads each configured recipient's pending notifications "
        "(read-only — never touches their `seen` cursor) and hands them "
        "to the channels in recipients.json, recording outcomes in the "
        "delivery ledger so nothing is double-sent.\n\n"
        "Example:\n"
        "  scitex-cards deliver\n"
        "  scitex-cards deliver --json"
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the delivery summary as JSON (machine-readable).",
)
def deliver_cmd(as_json: bool) -> None:
    """Run one delivery pass and print the summary."""
    from .._delivery import deliver_pending

    summary = deliver_pending(store=None)

    if as_json:
        click.echo(json.dumps(summary))
        return

    click.echo(
        f"# delivery: sent={summary['sent']} "
        f"failed={summary['failed']} "
        f"failed_terminal={summary['failed_terminal']} "
        f"skipped={summary['skipped']} "
        f"({len(summary['outcomes'])} item(s) recorded this run)"
    )
    if summary["failed_terminal"]:
        click.echo(
            f"# WARNING: {summary['failed_terminal']} notification(s) gave up "
            "after max attempts (comm-miss) — see stderr / delivery_ledger.json"
        )
    for item in summary["outcomes"]:
        click.echo(
            f"  {item['outcome']:<8} {item['recipient']} "
            f"{item['notification_id']} via {item['channel']}"
        )


# EOF
