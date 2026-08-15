#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards gui`` — the ecosystem-standard GUI verbs.

Verbs: ``open`` / ``serve`` / ``status`` / ``stop``, matching figrecipe,
scitex-writer and scitex-scholar. The operator's ``scitex_start_gui_servers``
script loops ``<pkg> gui serve &`` over every SciTeX tool; scitex-cards was the
odd one out — it exposed the board only as ``scitex-cards board``, so his loop
died on ``Error: No such command 'gui'`` and NOTHING ever bound :8051. The
board was never broken; the verb simply did not exist.

This group is a THIN FRONT over the existing ``board`` lifecycle
(:mod:`scitex_cards._cli._board`), not a reimplementation. ``board`` already
owns a pidfile, a stale-pidfile fallback that re-finds a board by port, and a
SIGTERM-then-SIGKILL escalation — strictly more than the generic
pid/state-file pattern the other tools hand-roll. Duplicating it would mean
two lifecycles racing for one pidfile.

The ``board`` verbs are NOT deprecated: they stay the canonical noun for the
dependency-graph board. ``gui`` is the cross-tool alias the operator's script
speaks.
"""

from __future__ import annotations

import click

from .._systemd_gui import DEFAULT_HOST as GUI_DEFAULT_HOST
from .._systemd_gui import DEFAULT_PORT as GUI_DEFAULT_PORT
from ._board import (
    _board_run_server,
    board_status_cmd,
    board_stop_cmd,
)
from ._board_force import force_stop_running_board
from ._board_proc import _board_read_pid
from ._compat import spec_command_kwargs, spec_group_kwargs
from ._store_guard import refuse_unconfigured_store

#: The board's long-standing default. The operator's startup script and the
#: `board` verbs already agree on it; `gui` must not invent a second one — so
#: these are IMPORTED, from the module that also bakes them into the systemd
#: unit. A unit that promised :8051 while the CLI served something else would
#: be a health check reporting on a port nobody uses.
DEFAULT_PORT = GUI_DEFAULT_PORT
DEFAULT_HOST = GUI_DEFAULT_HOST


def register(main: click.Group) -> None:
    """Attach the ``gui`` noun group to the root group."""
    main.add_command(gui_group)


@click.group(
    "gui",
    invoke_without_command=True,
    **spec_group_kwargs(
        summary="Serve the board GUI (open/serve/status/stop).",
        description=(
            "The ecosystem-standard GUI verb group, shared with figrecipe / "
            "scitex-writer / scitex-scholar so one startup script can bring "
            "every SciTeX GUI up the same way. A thin front over the `board` "
            "lifecycle — `board` remains the canonical noun and is not "
            "deprecated."
        ),
        command_categories=(
            ("Core", ("open", "serve", "status", "stop")),
            ("Host setup", ("install-service",)),
        ),
    ),
)
@click.pass_context
def gui_group(ctx: click.Context) -> None:
    """The ``gui`` noun group — REQUIRES an explicit verb.

    Mirrors the ``board`` group's noun-verb contract (operator directive TG
    13316): a bare noun hard-errors with a redirect rather than guessing.
    """
    if ctx.invoked_subcommand is not None:
        return
    click.echo(
        "ERROR: `scitex-cards gui` needs a verb. Use:\n"
        "  scitex-cards gui serve [--port N] [--host H]  # foreground/blocking\n"
        "  scitex-cards gui open [SURFACE]               # serve + open a browser\n"
        "  scitex-cards gui status [--json]\n"
        "  scitex-cards gui stop\n"
        "  scitex-cards gui install-service              # make it resident",
        err=True,
    )
    ctx.exit(2)


def _refuse_unconfigured_store() -> None:
    """Fail BEFORE binding a port if nobody chose a store. No silent fallback.

    Checked here rather than deeper in the stack because the damage is done by
    SERVING, not by resolving: once the port is bound the page renders, and a
    board that renders is a board people believe. The operator ran one for
    eight days showing week-old data with no error anywhere.

    THE BODY NOW LIVES IN :mod:`._store_guard`, shared with `board start`. This
    guard was written for `gui serve` alone because that was the surface that
    burned him -- and `board start`, the other door onto the same Django app,
    stayed open for three days as a result. A refusal implemented once per door
    is a refusal that will be missing from the next door somebody adds.

    KEPT AS A NAMED WRAPPER, and DEFINED ABOVE THE DECORATOR STACK rather than
    between it and ``gui_serve_cmd``. Placing a ``def`` inside a decorator chain
    silently rebinds every decorator onto the WRONG function: the options and
    ``@gui_group.command`` would have landed on this helper, leaving
    ``gui_serve_cmd`` an undecorated plain function and unregistering the
    `serve` verb entirely. Caught by the positive control
    (`test_serve_does_not_refuse_when_a_target_is_configured`) rather than in
    production, which is the only reason this comment exists.
    """
    refuse_unconfigured_store()


@gui_group.command(
    "serve",
    **spec_command_kwargs(
        summary="Serve the board GUI in the foreground (blocking).",
        description=(
            "The verb the operator's startup script calls. Blocking and "
            "headless by design: it does NOT open a browser (use `gui open` "
            "for that), so it is safe to background with `&` in a loop over "
            "every SciTeX tool. Requires the web extra: "
            "pip install scitex-cards[all]."
        ),
        examples=(
            ("{prog} gui serve", "Serve on 127.0.0.1:8051 (blocking)."),
            ("{prog} gui serve --port 9000", "Serve on another port."),
            ("{prog} gui serve --force", "Take over from a running board."),
        ),
    ),
)
@click.option("--port", type=int, default=DEFAULT_PORT, show_default=True)
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option(
    "--force",
    is_flag=True,
    help="Stop a board that is already running, then serve. A no-op when "
    "nothing is running — `--force` is a takeover, NOT a stop verb, so "
    "the absence of an incumbent is success, not an error.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the planned launch without starting the server.",
)
def gui_serve_cmd(port: int, host: str, force: bool, dry_run: bool) -> None:
    """Foreground-blocking serve, no browser.

    ``--force`` exists because the operator asked for one command that
    serves whether or not a board is already up (2026-08-14): 「stop する
    のめんどくさいので。あとはなければ通すように。stop ではないので。」
    Nothing running is therefore the NORMAL case for `--force`, not an
    error case. WITHOUT `--force` the refusal is unchanged.

    Example:
      $ scitex-cards gui serve --port 8051
      $ scitex-cards gui serve --force
    """
    # BEFORE the dry-run branch, and before anything is killed or bound.
    # Moved ahead of `--dry-run` when `--force` landed: a dry run that
    # answers "would stop pid 4711, then serve" for a store nobody
    # configured is a confident answer to the question the operator is
    # asking precisely because he is unsure. `board start` has always
    # ordered it this way; see `_store_guard`'s note on doors.
    _refuse_unconfigured_store()
    if force:
        # Takeover, not a stop: returns None when nothing is running and we
        # serve exactly as if `--force` were absent. It raises (naming the
        # pid + the next step) if the kernel refused the signal, because
        # binding a port that is demonstrably still held is the silent
        # failure this repo does not ship.
        force_stop_running_board(port, dry_run=dry_run)
    if dry_run:
        click.echo(f"# dry-run: would serve the board on {host}:{port}, no browser")
        return
    if not force:
        existing = _board_read_pid()
        if existing is not None:
            raise click.ClickException(
                f"the board is already running (pid {existing}). Use "
                "`scitex-cards gui stop` or `scitex-cards gui status`, or "
                "pass --force to take over."
            )
    _board_run_server(None, port, no_browser=True, host=host)


@gui_group.command(
    "open",
    **spec_command_kwargs(
        summary="Serve the GUI and open it in a browser.",
        description=(
            "Auto-serves, then opens SURFACE in the default browser. If a "
            "board is ALREADY running we do not start a second one — we just "
            "open the browser at the running instance (starting a rival "
            "server would only lose the port race and confuse the pidfile)."
        ),
        examples=(
            ("{prog} gui open", "Open the board."),
            ("{prog} gui open timeline", "Open the timeline surface."),
        ),
    ),
)
@click.argument("surface", required=False, default="")
@click.option("--port", type=int, default=DEFAULT_PORT, show_default=True)
@click.option("--host", default=DEFAULT_HOST, show_default=True)
def gui_open_cmd(surface: str, port: int, host: str) -> None:
    """Serve + open a browser. Reuses a running board if there is one.

    Example:
      $ scitex-cards gui open
      $ scitex-cards gui open timeline
    """
    url = f"http://{host}:{port}/{surface.lstrip('/')}"

    running = _board_read_pid()
    if running is not None:
        # Already up — just point the browser at it. Do NOT race the port.
        import webbrowser

        click.echo(f"# board already running (pid {running}); opening {url}")
        webbrowser.open(url)
        return

    if surface:
        # The server's own browser-open lands on "/", so drive the browser
        # ourselves once the server is listening.
        import threading
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        _board_run_server(None, port, no_browser=True, host=host)
        return

    _board_run_server(None, port, no_browser=False, host=host)


@gui_group.command(
    "install-service",
    **spec_command_kwargs(
        summary="Make the board RESIDENT on this host (systemd user unit).",
        description=(
            "Writes a systemd USER unit that serves the board on loopback and "
            "restarts it whenever it goes away, then prints the exact "
            "`systemctl --user` commands to enable it. OPERATOR-GATED: this "
            "NEVER runs systemctl itself.\n\n"
            "Run it on EVERY host. The board is served per-host on 127.0.0.1 "
            "and the card data travels between hosts via the per-host "
            "Postgres sync — one shared board reachable over the VPN would be "
            "a single point of failure and would vanish with the network."
        ),
        examples=(
            ("{prog} gui install-service", "Write the unit for 127.0.0.1:8051."),
            ("{prog} gui install-service --force", "Overwrite an existing unit."),
        ),
    ),
)
@click.option("--port", type=int, default=DEFAULT_PORT, show_default=True)
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing unit file (default: leave it untouched).",
)
def gui_install_service_cmd(port: int, host: str, force: bool) -> None:
    """Write the systemd user unit (operator-gated) and print the enable commands.

    Example:
      $ scitex-cards gui install-service
    """
    from .._systemd_gui import install_gui_unit
    from .._systemd_unit import ExecStartUnresolved

    try:
        result = install_gui_unit(host=host, port=port, force=force)
    except ExecStartUnresolved as exc:
        # Fail LOUDLY: a unit with an unresolvable ExecStart installs fine and
        # then dies at 203/EXEC the moment the operator enables it — a board
        # that is silently absent, which is the exact fault this verb exists
        # to end.
        raise click.ClickException(str(exc)) from exc
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


# `status` and `stop` are the SAME commands the `board` group exposes, not
# copies of them: one pidfile, one implementation, one set of behaviours to
# keep correct. Re-registering the objects under the `gui` name is the whole
# aliasing story.
gui_group.add_command(board_status_cmd, "status")
gui_group.add_command(board_stop_cmd, "stop")


# EOF
