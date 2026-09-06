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

from ._compat import deprecated_alias


def register(main: click.Group) -> None:
    """Attach ``deliver-notifications``, with ``deliver`` forwarding.

    RENAMED because a bare transitive verb at the top level does not say what
    it acts on. Audit §1: "bare transitive verb at top level — needs an object;
    use 'deliver-<object>' or nest under a noun, OR add a required positional
    argument that IS the object". `deliver` alone reads as a question — deliver
    WHAT? — and the answer was only in the help text.

    The object is notifications: this runs one pass over every recipient's
    pending notifications. So the leaf says so.

    `deliver` stays as a Phase-W alias because it is a published contract —
    the command is documented as cron/loop-runnable, so it may sit in a
    crontab or a unit file on any host, where a rename is invisible until the
    timer fires and does nothing.
    """
    main.add_command(deliver_cmd)
    deprecated_alias(
        main, "deliver", target="deliver-notifications", remove_in="0.52"
    )


@click.command(
    "deliver-notifications",
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
