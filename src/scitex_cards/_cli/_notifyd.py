#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verb ``scitex-cards notifyd`` — the always-on delivery daemon (slice 2).

Foreground run = what the systemd ``ExecStart`` calls: it ticks
:func:`scitex_cards._delivery.deliver_pending` every ``--interval`` seconds
until SIGTERM/SIGINT, holding a single-instance lock so two daemons never run
concurrently.

* ``scitex-cards notifyd`` — run the daemon in the foreground (blocking).
* ``scitex-cards notifyd --once`` — a single delivery pass then exit (cron/
  testing convenience; same as ``scitex-cards deliver`` but on the daemon verb).
* ``scitex-cards notifyd install-unit [--force]`` — WRITE the systemd user-unit
  TEMPLATE to ``~/.config/systemd/user/`` and PRINT the exact ``systemctl
  --user`` enable commands. Operator-gated: it never runs systemctl.

The long-running daemon, single-instance lock, signal handling, and throttled
terminal-comm-miss re-reporting all live in
:mod:`scitex_cards._delivery._daemon`; the unit template + install helper live
in :mod:`scitex_cards._delivery._systemd`.
"""

from __future__ import annotations

import logging

import click

from ._mutating import DRY_RUN_PREFIX, confirm_or_abort, mutating_options


def register(main: click.Group) -> None:
    """Attach the ``notifyd`` group to the root group."""
    main.add_command(notifyd_group)


@click.group(
    "notifyd",
    invoke_without_command=True,
    help=(
        "Run the always-on notification-delivery daemon (foreground).\n\n"
        "Bare `scitex-cards notifyd` runs the loop in the foreground — what "
        "the systemd unit's ExecStart calls. It ticks the delivery pass every "
        "--interval seconds, holding a single-instance lock so two daemons "
        "never run at once, until SIGTERM/SIGINT.\n\n"
        "Verbs:\n"
        "  install-unit      Write the systemd user-unit template (operator-gated)\n"
        "  collapse-digests  Collapse each recipient's unseen digest backlog\n\n"
        "Examples:\n"
        "  scitex-cards notifyd --interval 120\n"
        "  scitex-cards notifyd --once          # single pass then exit\n"
        "  scitex-cards notifyd install-unit\n"
        "  scitex-cards notifyd collapse-digests"
    ),
)
@click.option(
    "--interval",
    type=float,
    default=120.0,
    show_default=True,
    help="Seconds between delivery ticks (foreground run only).",
)
@click.option(
    "--once",
    "run_once",
    is_flag=True,
    help="Run a SINGLE delivery pass and exit (cron / testing convenience).",
)
@click.option(
    "--terminal-report-every",
    type=int,
    default=10,
    show_default=True,
    help="Re-surface standing terminal comm-misses every N ticks (throttle; "
    "<=0 disables).",
)
@click.option(
    "--nudge-sweep-minutes",
    type=float,
    default=None,
    help="Cadence (minutes) of the fleet-liveness stale/backlog nudge sweep, "
    "kept OUT of the hot delivery path (default: 30, or "
    "$SCITEX_CARDS_NUDGE_SWEEP_MINUTES; <=0 disables).",
)
@click.pass_context
def notifyd_group(
    ctx: click.Context,
    interval: float,
    run_once: bool,
    terminal_report_every: int,
    nudge_sweep_minutes: float | None,
) -> None:
    """The ``notifyd`` group — bare invocation runs the daemon foreground."""
    if ctx.invoked_subcommand is not None:
        # `scitex-cards notifyd install-unit` — let Click route to the verb.
        return

    # Foreground run (or a single pass with --once).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if run_once:
        from .._delivery import deliver_pending

        summary = deliver_pending(store=None)
        click.echo(
            f"# notifyd --once: sent={summary['sent']} "
            f"failed={summary['failed']} "
            f"failed_terminal={summary['failed_terminal']} "
            f"skipped={summary['skipped']} "
            f"({len(summary['outcomes'])} item(s) recorded)"
        )
        return

    import os as _os

    from .._delivery._daemon import DaemonAlreadyRunning, pidfile_path, run_notifyd
    from .._inbox import _resolved_store

    click.echo(
        f"# scitex-cards notifyd starting: pid={_os.getpid()} "
        f"store={_resolved_store(None)} "
        f"interval={interval}s "
        f"pidfile={pidfile_path(None)}"
    )
    try:
        result = run_notifyd(
            store=None,
            interval=interval,
            terminal_report_every=terminal_report_every,
            nudge_sweep_minutes=nudge_sweep_minutes,
        )
    except DaemonAlreadyRunning as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"# notifyd stopped ({result['stopped_by']}): "
        f"iterations={result['iterations']} totals={result['totals']}"
    )


@notifyd_group.command(
    "install-unit",
    help=(
        "Write the systemd user-unit template to ~/.config/systemd/user/ and "
        "print the exact `systemctl --user` enable commands for the operator "
        "to run. OPERATOR-GATED: this NEVER runs systemctl / enables / starts "
        "the service.\n\n"
        "Example:\n"
        "  scitex-cards notifyd install-unit\n"
        "  scitex-cards notifyd install-unit --dry-run  # say what it would do\n"
        "  scitex-cards notifyd install-unit --force   # overwrite existing"
    ),
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing unit file (default: leave it untouched).",
)
@mutating_options
def install_unit_cmd(force: bool, dry_run: bool, assume_yes: bool) -> None:
    """Write the unit file (operator-gated) and print the enable commands.

    `--dry-run` resolves ExecStart and reports the target path without writing.
    The resolution is the step that fails, so previewing it is worth more than
    previewing the write.
    """
    from .._delivery._systemd import ExecStartUnresolved, install_unit

    if not dry_run:
        confirm_or_abort(
            "Write the notifyd systemd user unit?", assume_yes=assume_yes
        )
    try:
        result = install_unit(force=force, dry_run=dry_run)
    except ExecStartUnresolved as exc:
        # Fail LOUDLY: a unit with an unresolvable ExecStart would install fine
        # and then die at 203/EXEC the moment the operator enables it.
        raise click.ClickException(str(exc)) from exc
    if result.get("dry_run"):
        click.echo(f"{DRY_RUN_PREFIX} would write systemd user unit: {result['path']}")
        click.echo(f"{DRY_RUN_PREFIX}   ExecStart={result['exec_start']}")
        if result["existed"] and not force:
            click.echo(f"{DRY_RUN_PREFIX}   (exists; --force would be needed)")
        click.echo(f"{DRY_RUN_PREFIX} nothing written")
        return
    if result["written"]:
        click.echo(f"# wrote systemd user unit: {result['path']}")
        click.echo(f"#   ExecStart={result['exec_start']}")
    elif result["existed"]:
        click.echo(
            f"# unit already exists (NOT overwritten): {result['path']}\n"
            "#   pass --force to overwrite."
        )
    click.echo("#")
    click.echo("# To enable + start it, the OPERATOR runs (this tool does NOT):")
    click.echo(f"#   {result['enable_commands']}")


@notifyd_group.command(
    "collapse-digests",
    help=(
        "One-time maintenance: collapse each recipient's UNSEEN digest backlog "
        "to the single newest digest (mark the older stale ones seen; delete "
        "nothing). Clears a fleet-wide digest replay-storm in one safe locked "
        "pass. The durable fix (supersede-on-enqueue) already prevents new "
        "backlog; this cleans up what accumulated before it landed.\n\n"
        "Example:\n"
        "  scitex-cards notifyd collapse-digests\n"
        "  scitex-cards notifyd collapse-digests --json"
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit the summary as JSON.")
def collapse_digests_cmd(as_json: bool) -> None:
    """Collapse the unseen digest backlog per recipient (maintenance verb)."""
    from .._inbox_maint import collapse_digests

    summary = collapse_digests(store=None)
    if as_json:
        import json

        click.echo(json.dumps(summary))
        return
    click.echo(
        f"# notifyd collapse-digests: "
        f"recipients_collapsed={summary['recipients_collapsed']} "
        f"digests_marked_seen={summary['digests_marked_seen']}"
    )


# EOF
